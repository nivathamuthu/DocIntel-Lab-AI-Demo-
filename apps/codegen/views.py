from django.conf import settings
from django.contrib import messages
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.codegen.contract import CODE_MODULE, DECISION_SOURCE, TEST_MODULE
from apps.codegen.models import CodegenRun
from apps.codegen.services import conflict_rows, create_run, label, selectable_rules
from apps.codegen.tasks import task_codegen
from apps.rules.models import Rule
from core.llm import describe_backend


def index(request):
    show_unreviewed = request.GET.get("unreviewed") == "1"
    rules, approved_exists = selectable_rules(show_unreviewed)
    partners: dict[str, list[str]] = {}
    for row in conflict_rows():
        partners.setdefault(row.rule_a.key, []).append(row.rule_b.key)
        partners.setdefault(row.rule_b.key, []).append(row.rule_a.key)
    items = [{"rule": rule, "label": label(rule), "partners": partners.get(rule.key, [])} for rule in rules]
    context = {
        "llm": describe_backend(),
        "items": items,
        "approved_exists": approved_exists,
        "show_unreviewed": show_unreviewed,
        "approved_count": Rule.objects.filter(status=Rule.Status.APPROVED).count(),
        "unreviewed_count": Rule.objects.filter(status=Rule.Status.PENDING).count(),
        "unreviewed_shown": sum(1 for item in items if item["label"] == "unreviewed"),
        "unreviewed_runs": CodegenRun.objects.filter(unreviewed=True).count(),
        "runs": CodegenRun.objects.all()[:15],
        "max_attempts": settings.CODEGEN_MAX_ATTEMPTS,
        "timeout": settings.CODEGEN_TEST_TIMEOUT_SECONDS,
    }
    return render(request, "codegen/index.html", context)


@require_POST
def start(request):
    keys = request.POST.getlist("rule")
    if not keys:
        messages.error(request, "Tick at least one rule, then click Generate code.")
        return redirect("codegen:index")
    try:
        run = create_run(keys)
    except ValueError as exc:
        messages.error(request, str(exc))
        return redirect("codegen:index")
    if settings.CELERY_TASK_ALWAYS_EAGER:
        task_codegen.apply(args=[run.id])
    else:
        task_codegen.delay(run.id)
    return redirect("codegen:run", run_id=run.id)


def _run_context(run: CodegenRun) -> dict:
    return {"run": run, "attempts": list(run.attempts.all()), "code_module": CODE_MODULE, "test_module": TEST_MODULE}


def run_detail(request, run_id: int):
    run = get_object_or_404(CodegenRun, id=run_id)
    return render(request, "codegen/run.html", _run_context(run))


def run_body(request, run_id: int):
    run = get_object_or_404(CodegenRun, id=run_id)
    response = render(request, "codegen/_run_body.html", _run_context(run))
    if request.GET.get("poll") and not run.active:
        response["HX-Refresh"] = "true"
    return response


def download(request, run_id: int, name: str):
    run = get_object_or_404(CodegenRun, id=run_id)
    files = {f"{CODE_MODULE}.py": run.final_code, f"{TEST_MODULE}.py": run.final_tests, "decision.py": DECISION_SOURCE}
    if name not in files or not files[name]:
        raise Http404("No such file for this run.")
    response = HttpResponse(files[name], content_type="text/x-python; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{name}"'
    return response
