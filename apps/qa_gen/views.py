from django.conf import settings
from django.contrib import messages
from django.http import HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_POST

from apps.qa_gen.models import QAPair, QARun
from apps.qa_gen.services.generate import totals
from apps.qa_gen.services.present import approved_rows, segments, to_csv, to_json
from apps.qa_gen.tasks import task_evaluate_qa, task_generate_qa
from core.embeddings import describe as describe_embeddings
from core.llm import describe_backend
from core.models import EvalRun

BULK_MIN_GROUNDEDNESS = 0.9

STATUS_FILTERS = {
    "all": {},
    "kept": {"verdict": QAPair.Verdict.KEPT},
    "pending": {"verdict": QAPair.Verdict.KEPT, "review": QAPair.Review.PENDING},
    "approved": {"verdict": QAPair.Verdict.KEPT, "review": QAPair.Review.APPROVED},
    "rejected": {"verdict": QAPair.Verdict.KEPT, "review": QAPair.Review.REJECTED},
    "dropped": None,
}


def _pairs(qtype: str, status: str):
    rows = QAPair.objects.prefetch_related("chunks__document")
    if qtype in QAPair.QType.values:
        rows = rows.filter(qtype=qtype)
    if status == "dropped":
        rows = rows.exclude(verdict=QAPair.Verdict.KEPT)
    else:
        rows = rows.filter(**STATUS_FILTERS.get(status, {}))
    return rows


def _dispatch(task, *args, **kwargs) -> None:
    if settings.CELERY_TASK_ALWAYS_EAGER:
        task.apply(args=args, kwargs=kwargs)
    else:
        task.delay(*args, **kwargs)


def _new_run(kind: str, forced: bool = False) -> QARun:
    backend = describe_backend()
    return QARun.objects.create(kind=kind, forced=forced, llm_provider=backend["provider"], llm_model=backend["model"],
                                message="Waiting for the worker")


def index(request):
    qtype = request.GET.get("type", "all")
    status = request.GET.get("status", "all")
    latest_eval = EvalRun.objects.filter(kind="qa").first()
    context = {
        "llm": describe_backend(),
        "embeddings": describe_embeddings(),
        "counts": totals(),
        "pairs": list(_pairs(qtype, status)),
        "qtype": qtype,
        "status": status,
        "types": QAPair.QType.choices,
        "statuses": list(STATUS_FILTERS),
        "generate_run": QARun.objects.filter(kind=QARun.Kind.GENERATE).first(),
        "evaluate_run": QARun.objects.filter(kind=QARun.Kind.EVALUATE).first(),
        "latest_eval": latest_eval,
        "min_groundedness": settings.QA_MIN_GROUNDEDNESS,
        "prompt_version": settings.QA_PROMPT_VERSION,
        "judge_version": settings.QA_JUDGE_PROMPT_VERSION,
        "eval_modes": settings.QA_EVAL_MODES,
        "per_type": settings.QA_EVAL_MAX_PER_TYPE,
        "bulk_min": BULK_MIN_GROUNDEDNESS,
        "bulk_eligible": _reviewable().filter(groundedness__gt=BULK_MIN_GROUNDEDNESS).count(),
    }
    return render(request, "qa_gen/index.html", context)


def _reviewable():
    return QAPair.objects.filter(verdict=QAPair.Verdict.KEPT, review=QAPair.Review.PENDING)


@require_POST
def bulk_approve(request):
    """Approve ticked kept, pending pairs, or every one above BULK_MIN_GROUNDEDNESS."""
    if request.POST.get("mode") == "threshold":
        chosen = _reviewable().filter(groundedness__gt=BULK_MIN_GROUNDEDNESS)
    else:
        ids = [value for value in request.POST.getlist("pair") if value.isdigit()]
        if not ids:
            messages.error(request, "Tick at least one pair, then click Approve selected.")
            return redirect("qa:index")
        chosen = _reviewable().filter(id__in=ids)
    count = chosen.update(review=QAPair.Review.APPROVED, reviewed_at=timezone.now())
    messages.success(request, f"{count} pair{'s' if count != 1 else ''} approved. Only kept pairs that were pending change.")
    return redirect("qa:index")


def _start(request, kind: str, task, forced: bool = False, label: str = ""):
    running = QARun.objects.filter(kind=kind, status__in=[QARun.Status.QUEUED, QARun.Status.RUNNING]).first()
    if running:
        messages.warning(request, f"A {label} is already running. Its progress is shown below.")
        return redirect("qa:index")
    run = _new_run(kind, forced=forced)
    if kind == QARun.Kind.GENERATE:
        _dispatch(task, run.id, forced)
    else:
        _dispatch(task, run.id)
    return redirect("qa:index")


@require_POST
def generate(request):
    return _start(request, QARun.Kind.GENERATE, task_generate_qa, forced=request.POST.get("force") == "1",
                  label="generation")


@require_POST
def evaluate(request):
    if not QAPair.objects.filter(verdict=QAPair.Verdict.KEPT).exists():
        messages.error(request, "There are no kept Q&A pairs yet. Click Generate Q&A first.")
        return redirect("qa:index")
    return _start(request, QARun.Kind.EVALUATE, task_evaluate_qa, label="evaluation")


def progress(request, run_id: int):
    run = get_object_or_404(QARun, id=run_id)
    response = render(request, "qa_gen/_progress.html", {"run": run})
    if request.GET.get("poll") and not run.active:
        response["HX-Refresh"] = "true"
    return response


def _panel_context(pair: QAPair) -> dict:
    sources = [{"chunk": chunk, "parts": segments(chunk.text, pair.supporting_quotes)}
               for chunk in pair.chunks.select_related("document").order_by("document_id", "ordinal")]
    return {"pair": pair, "sources": sources, "min_groundedness": settings.QA_MIN_GROUNDEDNESS}


def panel(request, pair_id: int):
    pair = get_object_or_404(QAPair, id=pair_id)
    return render(request, "qa_gen/_panel.html", _panel_context(pair))


@require_POST
def review(request, pair_id: int):
    pair = get_object_or_404(QAPair, id=pair_id)
    action = request.POST.get("action")
    if pair.verdict != QAPair.Verdict.KEPT:
        error = "Dropped pairs cannot be approved. They failed the quote or groundedness check."
    elif action not in ("approve", "reject"):
        error = "Unknown review action."
    else:
        error = ""
        pair.review = QAPair.Review.APPROVED if action == "approve" else QAPair.Review.REJECTED
        pair.reviewed_at = timezone.now()
        pair.save(update_fields=["review", "reviewed_at"])
    context = {**_panel_context(pair), "error": error, "oob_row": not error}
    if request.htmx:
        return render(request, "qa_gen/_panel.html", context)
    if error:
        messages.error(request, error)
    return redirect("qa:index")


def export(request, fmt: str):
    rows = approved_rows()
    if fmt == "csv":
        response = HttpResponse(to_csv(rows), content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = 'attachment; filename="approved_qa.csv"'
        return response
    response = HttpResponse(to_json(rows), content_type="application/json; charset=utf-8")
    response["Content-Disposition"] = 'attachment; filename="approved_qa.json"'
    return response
