"""The codegen loop off the request path. Each attempt is saved as it finishes, and the run page polls."""
import logging

from celery import shared_task

from apps.codegen.models import CodegenRun
from apps.codegen.services import execute

logger = logging.getLogger(__name__)


@shared_task
def task_codegen(run_id: int) -> None:
    run = CodegenRun.objects.get(id=run_id)
    execute(run)
    if run.status == CodegenRun.Status.FAILED:
        logger.warning("codegen run %s failed: %s", run_id, run.error)
