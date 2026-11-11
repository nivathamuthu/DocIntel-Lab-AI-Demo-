"""Generation and evaluation off the request path. Progress goes to the QARun row the page polls."""
import logging

from celery import shared_task
from django.utils import timezone

from apps.qa_gen.models import QARun
from apps.qa_gen.services.evaluate import run_eval
from apps.qa_gen.services.generate import run_generation

logger = logging.getLogger(__name__)


def _finish(run_id: int, status: str, **fields) -> None:
    QARun.objects.filter(id=run_id).update(status=status, finished_at=timezone.now(), **fields)


@shared_task
def task_generate_qa(run_id: int, force: bool = False) -> None:
    run = QARun.objects.get(id=run_id)
    try:
        stats = run_generation(run, force=force)
    except Exception as exc:
        logger.exception("qa generation run %s failed", run_id)
        _finish(run_id, QARun.Status.FAILED, error=f"{type(exc).__name__}: {exc}"[:2000])
        return
    _finish(run_id, QARun.Status.DONE, stats=stats, message="Finished")


@shared_task
def task_evaluate_qa(run_id: int) -> None:
    run = QARun.objects.get(id=run_id)
    try:
        eval_run = run_eval(run)
    except Exception as exc:
        logger.exception("qa evaluation run %s failed", run_id)
        _finish(run_id, QARun.Status.FAILED, error=f"{type(exc).__name__}: {exc}"[:2000])
        return
    _finish(run_id, QARun.Status.DONE, eval_run_id=eval_run.id, message="Finished")
