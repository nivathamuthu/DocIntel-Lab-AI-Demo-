"""Prompt versions, the mock score gap, and validation retry."""
from pathlib import Path

import pytest
from django.conf import settings
from django.core.management import call_command

from apps.prompts.schemas import SCHEMAS, AnswerOutput
from apps.prompts.services.eval import run_eval
from core.llm import Usage, structured_call
from core.models import EvalRun, LLMCall
from core.prompts import render_prompt

pytestmark = pytest.mark.django_db


def test_every_prompt_task_has_both_versions_on_disk():
    for task in SCHEMAS:
        folder = Path(settings.BASE_DIR) / "prompts" / task
        assert (folder / "v1.md").exists(), task
        assert (folder / "v2.md").exists(), task


def test_v2_template_carries_the_abstain_rule_and_v1_does_not():
    variables = {"chunk": "A short chunk.", "question": "What is the limit?", "domain": "aviation maintenance"}
    v1 = render_prompt("answer_with_citations", "v1", variables, AnswerOutput)
    v2 = render_prompt("answer_with_citations", "v2", variables, AnswerOutput)
    assert "not contained in the chunk" in v2
    assert "not contained in the chunk" not in v1
    assert "Example 1" in v2
    assert "known" in v2


def test_v2_scores_higher_than_v1_and_records_the_provider():
    run = run_eval()
    assert run.metrics["v2"] > run.metrics["v1"]
    assert run.llm_provider == "mock"
    assert run.llm_model
    assert "LLM:" in run.report_md
    assert "Embeddings:" in run.report_md
    assert EvalRun.objects.filter(kind="prompts").count() == 1


def test_compare_page_shows_both_versions(client):
    call_command("eval_prompts")
    page = client.get("/prompts/")
    body = page.content.decode()
    assert page.status_code == 200
    assert "v1" in body and "v2" in body
    assert "mock" in body


def test_invalid_output_is_retried_and_logged():
    class Flaky:
        provider = "mock"
        model = "flaky"
        calls = 0

        def generate(self, schema, messages):
            self.calls += 1
            if self.calls == 1:
                return {"answer": "", "known": True, "citation_indexes": []}, Usage()
            return {"answer": "PN-77-4418", "known": True, "citation_indexes": [1]}, Usage()

    backend = Flaky()
    result = structured_call(AnswerOutput, "Question: which part?", task="test.retry", backend=backend)
    assert result.answer == "PN-77-4418"
    assert backend.calls == 2
    logged = LLMCall.objects.get(task="test.retry")
    assert logged.attempts == 2
    assert logged.validation_ok is True
