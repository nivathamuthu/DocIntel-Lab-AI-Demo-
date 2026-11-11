"""Test defaults: mock models, no network, and the pipeline running in-process.

This file sits at the repo root so it applies to tests under apps/ as well as tests/.
"""
import pytest


@pytest.fixture(autouse=True)
def _runtime(settings, tmp_path):
    settings.CELERY_TASK_ALWAYS_EAGER = True
    settings.LLM_PROVIDER = "mock"
    settings.EMBEDDINGS_PROVIDER = "mock"
    settings.INGEST_CHAOS_STAGE = ""
    settings.INGEST_MAX_RETRIES = 2
    settings.GRAPH_BACKEND = "networkx"
    settings.KG_PROMPT_VERSION = "v2"
    settings.RULES_PROMPT_VERSION = "v2"
    media = tmp_path / "media"
    media.mkdir()
    settings.MEDIA_ROOT = media
    settings.EVAL_RESULTS_DIR = tmp_path / "eval_results"
    from django.core.files.storage import storages

    storages._storages.pop("default", None)
