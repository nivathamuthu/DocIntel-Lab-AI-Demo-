import re

from django.conf import settings
from django.contrib import messages
from django.db.models import Q
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.ingest.models import Document
from apps.rules.models import Rule, RuleConflict, RuleRun
from apps.rules.services.export import approved_rows, to_csv, to_json
from apps.rules.services.pipeline import totals
from apps.rules.services.present import present_conflict
from apps.rules.tasks import task_extract_rules
from core.embeddings import describe as describe_embeddings
from core.llm import describe_backend
from core.models import EvalRun

BULK_MIN_CONFIDENCE = 0.9

STATUS_FILTERS = {
    "open": [Rule.Status.PENDING, Rule.Status.APPROVED],
    **{status: [status] for status in Rule.Status.values},
    "all": list(Rule.Status.values),
}


def _backend_context() -> dict:
    return {"llm": describe_backend(), "embeddings": describe_embeddings()}


def index(request):
    status = request.GET.get("status", "open")
    statuses = STATUS_FILTERS.get(status, STATUS_FILTERS["open"])
    document_id = request.GET.get("document", "")
    try:
        min_confidence = float(request.GET.get("min_confidence") or 0)
    except ValueError:
        min_confidence = 0.0
    rules = Rule.objects.filter(status__in=statuses, confidence__gte=min_confidence).select_related("document", "merged_into")
    if document_id.isdigit():
        rules = rules.filter(document_id=int(document_id))
    rules = list(rules)
    conflicts = []
    label_for = {}
    for row in RuleConflict.objects.filter(is_conflict=True).select_related("rule_a__document", "rule_b__document"):
        text_a = f"{row.rule_a.condition} {row.rule_a.action}"
        text_b = f"{row.rule_b.condition} {row.rule_b.action}"
        row.display_severity, row.display_label = present_conflict(text_a, text_b, row.severity)
        conflicts.append(row)
        for rule_id in (row.rule_a_id, row.rule_b_id):
            if row.display_label == "conflict":
                label_for[rule_id] = "conflict"
            elif label_for.get(rule_id) != "conflict":
                label_for[rule_id] = "possible conflict"
    for rule in rules:
        rule.conflict_label = label_for.get(rule.id, "")
    context = {
        **_backend_context(),
        "rules": rules,
        "counts": totals(),
        "run": RuleRun.objects.first(),
        "conflicts": conflicts,
        "has_possible_conflict": any(row.display_label == "possible conflict" for row in conflicts),
        "documents": Document.objects.filter(rules__isnull=False).distinct().order_by("title"),
        "status": status,
        "status_choices": list(STATUS_FILTERS),
        "document_id": document_id,
        "min_confidence": request.GET.get("min_confidence", ""),
        "prompt_version": settings.RULES_PROMPT_VERSION,
        "latest_eval": EvalRun.objects.filter(kind="rules").first(),
        "bulk_min": BULK_MIN_CONFIDENCE,
        "bulk_eligible": Rule.objects.filter(status=Rule.Status.PENDING, confidence__gt=BULK_MIN_CONFIDENCE).count(),
    }
    return render(request, "rules/index.html", context)


@require_POST
def bulk_approve(request):
    """Approve ticked pending rules, or every pending rule above BULK_MIN_CONFIDENCE."""
    pending = Rule.objects.filter(status=Rule.Status.PENDING)
    if request.POST.get("mode") == "threshold":
        chosen = pending.filter(confidence__gt=BULK_MIN_CONFIDENCE)
    else:
        keys = request.POST.getlist("rule")
        if not keys:
            messages.error(request, "Tick at least one rule, then click Approve selected.")
            return redirect("rules:index")
        chosen = pending.filter(key__in=keys)
    count = chosen.update(status=Rule.Status.APPROVED, reviewed_at=timezone.now(), merged_into=None)
    messages.success(request, f"{count} rule{'s' if count != 1 else ''} approved. Only pending rules are changed.")
    return redirect("rules:index")


@require_POST
def extract(request):
    force = request.POST.get("force") == "1"
    if settings.CELERY_TASK_ALWAYS_EAGER:
        task_extract_rules.apply(kwargs={"force": force})
        messages.info(request, "Rules extracted.")
    else:
        task_extract_rules.delay(force=force)
        messages.info(request, "Rule extraction queued on the worker. Reload in a moment for the new counts.")
    return redirect("rules:index")


def highlight(chunk_text: str, quote: str) -> tuple[str, str, str]:
    """(before, quote, after) slices of the chunk text, matching the quote across any whitespace."""
    words = quote.split()
    if not words:
        return chunk_text, "", ""
    pattern = r"\s+".join(re.escape(word) for word in words)
    match = re.search(pattern, chunk_text)
    if not match:
        return chunk_text, "", ""
    return chunk_text[:match.start()], match.group(0), chunk_text[match.end():]


def detail(request, key: str):
    rule = get_object_or_404(Rule.objects.select_related("document", "chunk", "merged_into"), key=key)
    before, marked, after = highlight(rule.chunk.text, rule.source_quote)
    conflicts = RuleConflict.objects.filter(Q(rule_a=rule) | Q(rule_b=rule), is_conflict=True).select_related(
        "rule_a__document", "rule_b__document"
    )
    shown = []
    for row in conflicts:
        text_a = f"{row.rule_a.condition} {row.rule_a.action}"
        text_b = f"{row.rule_b.condition} {row.rule_b.action}"
        row.display_severity, row.display_label = present_conflict(text_a, text_b, row.severity)
        shown.append({"row": row, "other": row.rule_b if row.rule_a_id == rule.id else row.rule_a})
    context = {
        **_backend_context(),
        "rule": rule,
        "before": before,
        "marked": marked,
        "after": after,
        "conflicts": shown,
        "duplicates": rule.duplicates.select_related("document"),
    }
    return render(request, "rules/detail.html", context)


@require_POST
def review(request, key: str):
    rule = get_object_or_404(Rule, key=key)
    action = request.POST.get("action")
    note = (request.POST.get("note") or "").strip()
    if action == "edit":
        changes = {
            "condition": (request.POST.get("condition") or "").strip(),
            "action": (request.POST.get("action_text") or "").strip(),
            "exception": (request.POST.get("exception") or "").strip(),
        }
        if not changes["condition"] or not changes["action"]:
            messages.error(request, "Condition and action are required.")
            return redirect("rules:detail", key=key)
        if not rule.edited:
            rule.original = {"condition": rule.condition, "action": rule.action, "exception": rule.exception}
        rule.condition, rule.action, rule.exception = changes["condition"], changes["action"], changes["exception"]
        rule.edited = True
        message = f"{rule.key} edited."
    elif action in ("approve", "reject"):
        rule.status = Rule.Status.APPROVED if action == "approve" else Rule.Status.REJECTED
        if action == "approve":
            rule.merged_into = None
        message = f"{rule.key} {rule.status}."
    else:
        messages.error(request, "Unknown review action.")
        return redirect("rules:detail", key=key)
    if note:
        rule.reviewer_note = note
    rule.reviewed_at = timezone.now()
    rule.save()
    messages.info(request, message)
    return redirect("rules:detail", key=key)


def export(request, fmt: str):
    rows = approved_rows()
    if fmt == "csv":
        response = HttpResponse(to_csv(rows), content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = 'attachment; filename="approved_rules.csv"'
        return response
    response = HttpResponse(to_json(rows), content_type="application/json; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="approved_rules.json"'
    return response
