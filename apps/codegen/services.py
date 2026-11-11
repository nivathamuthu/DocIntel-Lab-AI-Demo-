"""Pick rules, start a codegen run, execute the loop, and check the result with a mutation."""
from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from apps.codegen.graph import build_graph
from apps.codegen.models import CodegenRun
from apps.codegen.render import mutate_threshold
from apps.codegen.sandbox import run_pytest
from apps.rules.models import Rule, RuleConflict
from core.llm import describe_backend

USABLE = (Rule.Status.APPROVED, Rule.Status.PENDING)


def label(rule: Rule) -> str:
    return "approved" if rule.status == Rule.Status.APPROVED else "unreviewed"


def conflict_rows(keys=None):
    rows = RuleConflict.objects.filter(is_conflict=True).select_related("rule_a", "rule_b")
    if keys is not None:
        rows = rows.filter(Q(rule_a__key__in=keys) | Q(rule_b__key__in=keys))
    return [row for row in rows if row.rule_a.status in USABLE and row.rule_b.status in USABLE]


def selectable_rules(show_unreviewed: bool = False) -> tuple[list[Rule], bool]:
    """Approved rules; if none are approved (or asked to), pending rules too, labelled unreviewed."""
    approved_exists = Rule.objects.filter(status=Rule.Status.APPROVED).exists()
    statuses = USABLE if (show_unreviewed or not approved_exists) else (Rule.Status.APPROVED,)
    rules = list(Rule.objects.filter(status__in=statuses).select_related("document").order_by("key"))
    return rules, approved_exists


def snapshot(rule: Rule) -> dict:
    return {
        "key": rule.key, "label": label(rule), "citation": rule.citation(),
        "condition": rule.condition, "action": rule.action, "exception": rule.exception,
        "quote": rule.source_quote, "document": rule.document.title, "page": rule.page,
    }


def create_run(keys: list[str], source: str = "ui", add_partners: bool = True) -> CodegenRun:
    """Snapshot the chosen rules. Conflict partners join the run unless add_partners is False."""
    chosen = list(Rule.objects.filter(key__in=keys, status__in=USABLE).select_related("document"))
    if not chosen:
        raise ValueError("Choose at least one approved or pending rule.")
    chosen_keys = {rule.key for rule in chosen}
    conflicts, added = [], []
    for row in conflict_rows(set(chosen_keys)):
        if not add_partners and not {row.rule_a.key, row.rule_b.key} <= chosen_keys:
            continue
        for rule in (row.rule_a, row.rule_b):
            if rule.key not in chosen_keys:
                chosen.append(rule)
                chosen_keys.add(rule.key)
                added.append(rule.key)
        conflicts.append({"a": row.rule_a.key, "b": row.rule_b.key, "reason": row.reason, "severity": row.severity})
    chosen.sort(key=lambda rule: rule.key)
    backend = describe_backend()
    return CodegenRun.objects.create(
        rule_keys=[rule.key for rule in chosen], added_keys=added, rules=[snapshot(rule) for rule in chosen],
        conflicts=conflicts, unreviewed=any(rule.status != Rule.Status.APPROVED for rule in chosen),
        max_attempts=settings.CODEGEN_MAX_ATTEMPTS, llm_provider=backend["provider"], llm_model=backend["model"],
        source=source,
    )


def conflict_surfaced(run: CodegenRun, per_test: dict) -> bool | None:
    if not run.conflicts:
        return None
    conflict_tests = [f"test_{case['name']}" for case in run.cases if case["expected"] == "conflict"]
    return bool(conflict_tests) and all(per_test.get(name) == "PASSED" for name in conflict_tests)


def mutation_check(run: CodegenRun) -> dict:
    mutated, change = mutate_threshold(run.final_code, run.rules)
    if mutated is None:
        return {"ran": False, "note": change}
    result = run_pytest(mutated, run.final_tests, timeout=settings.CODEGEN_TEST_TIMEOUT_SECONDS)
    return {
        "ran": True, "change": change, "status": result.status, "failed": result.failed,
        "killed": result.status != "passed",
        "note": (f"After the threshold was {change}, {result.failed} test(s) failed, so the tests notice the change."
                 if result.status != "passed" else
                 f"After the threshold was {change}, every test still passed: the tests miss this boundary."),
    }


def execute(run: CodegenRun) -> CodegenRun:
    run.status = CodegenRun.Status.RUNNING
    run.save(update_fields=["status"])
    try:
        state = build_graph().invoke(
            {"run_id": run.id, "rules": run.rules, "conflicts": run.conflicts, "max_attempts": run.max_attempts},
            {"recursion_limit": 6 + 3 * run.max_attempts},
        )
    except Exception as exc:
        run.refresh_from_db()
        run.status = CodegenRun.Status.FAILED
        run.error = f"{type(exc).__name__}: {exc}"
        run.finished_at = timezone.now()
        run.save(update_fields=["status", "error", "finished_at"])
        return run
    run.refresh_from_db()
    last = run.attempts.order_by("-number").first()
    run.final_code = state.get("code", "")
    run.final_tests = state.get("tests", "")
    run.status = CodegenRun.Status.PASSED if state.get("passed") else CodegenRun.Status.NEEDS_REVIEW
    run.review_reason = state.get("stop_reason", "") if not state.get("passed") else ""
    if not state.get("passed") and not run.review_reason:
        run.review_reason = f"The tests still fail after {run.attempts_used} attempts."
    run.conflict_surfaced = conflict_surfaced(run, last.per_test if last else {})
    run.mutation = mutation_check(run) if state.get("passed") else {"ran": False, "note": "Skipped: the tests do not pass yet."}
    run.finished_at = timezone.now()
    run.save()
    return run
