"""eval_codegen: run the loop on a fixed set of rules and report tests, attempts, conflicts and mutations."""
from datetime import datetime, timezone
from pathlib import Path

import yaml
from django.conf import settings
from django.db.models import Q

from apps.codegen.services import USABLE, create_run, execute
from apps.rules.models import Rule
from core.llm import describe_backend
from core.models import EvalRun

DATASET = Path(settings.BASE_DIR) / "evals" / "datasets" / "codegen_cases.yaml"
HONESTY_NOTE = ("Passing tests the model wrote itself proves the code is consistent with the model's reading of "
                "the rule, not that the rule, or that reading, is correct.")


def find_rule(phrase: str) -> Rule | None:
    query = Q(condition__icontains=phrase) | Q(action__icontains=phrase) | Q(exception__icontains=phrase)
    return Rule.objects.filter(query, status__in=USABLE).order_by("key").first()


def load_cases(path: Path | None = None) -> list[dict]:
    return yaml.safe_load((path or DATASET).read_text(encoding="utf-8")) or []


def run_eval(cases: list[dict] | None = None) -> EvalRun:
    cases = cases if cases is not None else load_cases()
    rows = []
    for case in cases:
        rules = [find_rule(phrase) for phrase in case["rules"]]
        missing = [phrase for phrase, rule in zip(case["rules"], rules) if rule is None]
        if missing:
            rows.append({"name": case["name"], "why": case.get("why", ""), "status": "skipped",
                         "note": f"no rule matches {missing}"})
            continue
        run = execute(create_run([rule.key for rule in rules], source="eval", add_partners=False))
        last = run.attempts.order_by("-number").first()
        rows.append({
            "name": case["name"], "why": case.get("why", ""), "run_id": run.id, "rules": run.rule_keys,
            "status": run.status, "function": run.function_name, "attempts": run.attempts_used,
            "tests_passed": last.passed_count if last else 0,
            "tests_total": (last.passed_count + last.failed_count) if last else 0,
            "conflict_expected": bool(run.conflicts), "conflict_surfaced": run.conflict_surfaced,
            "mutation": run.mutation, "error": run.error,
        })
    ran = [row for row in rows if row["status"] != "skipped"]
    metrics = {
        "rows": rows,
        "runs": len(ran),
        "passed": sum(1 for row in ran if row["status"] == "passed"),
        "rules": sum(len(row["rules"]) for row in ran),
        "mean_attempts": round(sum(row["attempts"] for row in ran) / len(ran), 2) if ran else 0,
        "conflicts_surfaced": sum(1 for row in ran if row["conflict_surfaced"]),
        "conflicts_expected": sum(1 for row in ran if row["conflict_expected"]),
        "mutations_killed": sum(1 for row in ran if row["mutation"].get("killed")),
        "mutations_ran": sum(1 for row in ran if row["mutation"].get("ran")),
    }
    llm = describe_backend()
    report = _report(metrics, llm)
    results_dir = Path(settings.EVAL_RESULTS_DIR)
    results_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    (results_dir / f"codegen_{stamp}.md").write_text(report, encoding="utf-8")
    return EvalRun.objects.create(
        kind="codegen", llm_provider=llm["provider"], llm_model=llm["model"],
        config={"cases": [case["name"] for case in cases], "max_attempts": settings.CODEGEN_MAX_ATTEMPTS,
                "timeout_seconds": settings.CODEGEN_TEST_TIMEOUT_SECONDS},
        metrics=metrics, report_md=report,
    )


def _mutation_cell(mutation: dict) -> str:
    if not mutation.get("ran"):
        return "not run"
    return f"{'caught' if mutation['killed'] else 'missed'} ({mutation['change']})"


def _report(metrics: dict, llm: dict) -> str:
    lines = [
        "# Codegen eval: rules to code with self-written tests",
        "",
        f"- LLM: `{llm['provider']}` / `{llm['model']}` (code writer, test writer and repair)",
        f"- Max attempts: {settings.CODEGEN_MAX_ATTEMPTS}; sandbox timeout {settings.CODEGEN_TEST_TIMEOUT_SECONDS} s",
        f"- Runs: {metrics['runs']} on {metrics['rules']} rules; all tests passed in {metrics['passed']}; "
        f"mean attempts {metrics['mean_attempts']}",
        f"- Conflicts surfaced: {metrics['conflicts_surfaced']} of {metrics['conflicts_expected']}; "
        f"mutations caught: {metrics['mutations_killed']} of {metrics['mutations_ran']}",
        "",
        "| case | rules | result | tests passed | attempts | conflict surfaced | mutation check |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in metrics["rows"]:
        if row["status"] == "skipped":
            lines.append(f"| {row['name']} | - | skipped: {row['note']} | - | - | - | - |")
            continue
        conflict = "n/a" if row["conflict_surfaced"] is None else ("yes" if row["conflict_surfaced"] else "no")
        result = row["status"] if not row["error"] else f"failed: {row['error'][:80]}"
        lines.append(
            f"| {row['name']} | {', '.join(row['rules'])} | {result} | {row['tests_passed']}/{row['tests_total']} "
            f"| {row['attempts']} | {conflict} | {_mutation_cell(row['mutation'])} |"
        )
    lines += [
        "",
        "Caveats:",
        f"- {HONESTY_NOTE}",
        "- The same model writes the code and the tests. The test writer never sees the code, but the repair step "
        "sees the failing pytest output.",
        "- The mutation check raises the largest rule threshold found in the code by one and reruns the tests once.",
        "- The sandbox is a demo sandbox, not a security boundary.",
    ]
    if llm["provider"] == "mock":
        lines += ["", "These numbers come from the offline mock. They test the wiring, not a model."]
    lines.append("")
    return "\n".join(lines)
