"""Quote verification, cache and stable keys, merging, conflicts, review, export and the eval. Offline."""
import csv
import io
import json

import pytest
from django.core.files.base import ContentFile
from django.core.management import call_command
from django.urls import reverse

from apps.ingest.models import Chunk, Document
from apps.rules.models import Rule, RuleConflict, RuleExtraction, RuleRun
from apps.rules.schemas import ConflictJudgment, RuleSet
from apps.rules.services.eval import matches, score
from apps.rules.services.extract import run_extraction, sync_rules, verify_quotes
from apps.rules.services.merge import merge_duplicates
from apps.rules.services.pipeline import run_rules
from apps.rules.services.present import is_deferral_against_limit, present_conflict
from apps.rules.services.text import identifiers, quote_in_chunk, thresholds
from core.embeddings import embed_texts
from core.llm import Usage, structured_call
from core.models import EvalRun, LLMCall

pytestmark = pytest.mark.django_db

PROCEDURE = [
    ("IF part 65-45210-3 is installed AND its flight hours are at least 12000, THEN replace it with "
     "PN-77-4418 before the next revenue flight. Exception: the part may stay fitted for 50 flight hours "
     "when PN-77-4418 is on order."),
    "Record the part number before the aircraft is released. A missing placard may be deferred under the MEL.",
]
ORDER = [
    "IF part 65-45210-3 is installed on the narrow-body fleet, THEN replace it at 11500 flight hours.",
    "Record the part number before the aircraft is released.",
]


def _document(title: str, texts: list[str], sha: str) -> list[Chunk]:
    document = Document(title=title, original_name=f"{title}.docx", sha256=sha * 64, size_bytes=10,
                        file_type="docx", status=Document.Status.DONE)
    document.file.save(f"{title}.docx", ContentFile(b"x"), save=False)
    document.save()
    return [
        Chunk.objects.create(
            document=document, ordinal=index, text=text, text_hash=f"{sha}{index}", page_start=index + 1,
            section_title="2.1 Replacement criteria", section_path="2. Parts > 2.1 Replacement criteria",
            token_count=20, metadata={}, embedding=embed_texts([text])[0].tolist(),
        )
        for index, text in enumerate(texts)
    ]


@pytest.fixture
def corpus():
    return _document("procedure", PROCEDURE, "a") + _document("order", ORDER, "b")


def test_quote_check_ignores_whitespace_and_typographic_quotes_but_not_wording():
    chunk = "Replace the side-stay\nbefore it reaches 12000 flight hours. Don\u2019t ferry it."
    assert quote_in_chunk("Replace the side-stay before it reaches 12000 flight hours.", chunk)
    assert quote_in_chunk("Don't ferry it.", chunk)
    assert not quote_in_chunk("Replace the side-stay before 12000 FH.", chunk)
    assert not quote_in_chunk("replace the side-stay", chunk)
    assert not quote_in_chunk("   ", chunk)


def test_rules_whose_quote_is_not_in_the_chunk_are_dropped_and_counted():
    rules = [
        {"condition": "c", "action": "a", "exception": "", "source_quote": "Do this.", "confidence": 0.9},
        {"condition": "c2", "action": "a2", "exception": "", "source_quote": "Do that within 30 days.", "confidence": 0.9},
        {"condition": "c", "action": "a", "exception": "", "source_quote": "Do this.", "confidence": 0.9},
    ]
    kept, dropped = verify_quotes(rules, "Do this. Do that.")
    assert [rule["condition"] for rule in kept] == ["c"]
    assert len(dropped) == 1 and dropped[0]["reason"] == "source_quote not found in chunk"


def test_v1_paraphrased_quotes_are_dropped_and_logged(corpus):
    extraction, ran = run_extraction(corpus[0], "v1")
    assert ran and extraction.dropped
    assert extraction.kept_count + len(extraction.dropped) <= len(extraction.raw_llm_output["rules"])
    assert all("FH" in row["source_quote"] for row in extraction.dropped)


def test_extraction_is_cached_per_chunk_and_version(corpus):
    chunk = corpus[0]
    first, ran = run_extraction(chunk, "v2")
    assert ran and first.raw_llm_output["rules"][0]["exception"].startswith("the part may stay fitted")
    calls = LLMCall.objects.filter(task="rules.extract").count()
    _again, ran_again = run_extraction(chunk, "v2")
    assert ran_again is False and LLMCall.objects.filter(task="rules.extract").count() == calls
    _forced, ran_forced = run_extraction(chunk, "v2", force=True)
    assert ran_forced is True
    chunk.text_hash = "changed"
    chunk.save()
    _changed, ran_changed = run_extraction(chunk, "v2")
    assert ran_changed is True
    assert RuleExtraction.objects.filter(chunk=chunk).count() == 1


def test_cached_output_from_another_model_is_not_reused(corpus):
    first, _ran = run_extraction(corpus[0], "v2")
    first.llm_model = "some-other-model"
    first.save()
    _again, ran = run_extraction(corpus[0], "v2")
    assert ran is True
    assert RuleExtraction.objects.get(chunk=corpus[0], prompt_version="v2").llm_model == "mock-structured-v1"


def test_keys_are_stable_and_reviews_survive_re_extraction(corpus):
    run_rules()
    keys = dict(Rule.objects.values_list("source_quote", "key"))
    assert min(keys.values()) == "R-0001"
    rule = Rule.objects.get(condition__startswith="part 65-45210-3 is installed AND")
    assert rule.page == 1 and rule.section.endswith("2.1 Replacement criteria")
    assert rule.document.title == "procedure" and rule.raw_llm_output["prompt_version"] == "v2"
    rule.status = Rule.Status.APPROVED
    rule.save()
    run_rules(force=True)
    assert dict(Rule.objects.values_list("source_quote", "key")) == keys
    assert Rule.objects.get(key=rule.key).status == Rule.Status.APPROVED


def test_rules_that_vanish_are_retired_not_deleted(corpus):
    extraction, _ran = run_extraction(corpus[1], "v2")
    sync_rules(extraction)
    count = Rule.objects.count()
    extraction.raw_llm_output = {"rules": extraction.raw_llm_output["rules"][:1]}
    extraction.save()
    stats = sync_rules(extraction)
    assert stats["retired"] == 1 and Rule.objects.count() == count
    assert Rule.objects.filter(status=Rule.Status.RETIRED).count() == 1


def test_exact_duplicates_merge_but_different_numbers_never_do(corpus):
    run_rules()
    merged = Rule.objects.filter(status=Rule.Status.MERGED)
    assert merged.count() == 1
    duplicate = merged.get()
    assert duplicate.merged_into.action == duplicate.action
    assert duplicate.merge_reason == "same normalized condition and action"
    assert RuleRun.objects.get().merge_log[0]["rule"] == duplicate.key
    replace = Rule.objects.filter(action__startswith="replace it")
    assert replace.count() == 2 and not replace.filter(status=Rule.Status.MERGED).exists()


def test_merge_never_folds_an_approved_rule(corpus):
    run_rules()
    for rule in Rule.objects.filter(action__startswith="Record"):
        rule.status = Rule.Status.APPROVED
        rule.merged_into = None
        rule.save()
    assert merge_duplicates() == []


def test_contradicting_intervals_are_flagged_by_the_judge(corpus):
    run_rules()
    conflict = RuleConflict.objects.get(is_conflict=True)
    actions = {conflict.rule_a.action, conflict.rule_b.action}
    assert any("11500" in action for action in actions)
    assert conflict.kind == "threshold" and conflict.severity == "high"
    assert "12000" in conflict.reason and "11500" in conflict.reason
    assert conflict.llm_provider == "mock"
    judged = LLMCall.objects.filter(task="rules.judge").count()
    run_rules()
    assert LLMCall.objects.filter(task="rules.judge").count() == judged


def test_ccheck_deferral_against_a_limit_is_shown_as_possible():
    limit = "Replace the side-stay before the accumulated flight hours on part 65-45210-3 reach 12000"
    deferral = "Do the replacement at the next C-check when the hours are already above 11000"
    other_limit = "replace it at 11500 flight hours"
    assert is_deferral_against_limit(limit, deferral)
    assert not is_deferral_against_limit(limit, other_limit)
    assert present_conflict(limit, deferral, "high") == ("medium", "possible conflict")
    assert present_conflict(limit, other_limit, "high") == ("high", "conflict")


def test_identifiers_and_thresholds():
    text = "part 65-45210-3 with P/N 77-4418 under AD 2023-14-09, section 2.1, serials S1000, at 12000 flight hours"
    assert identifiers(text) == {"65-45210-3", "PN-77-4418", "AD 2023-14-09"}
    assert thresholds(text) == {"12000"}


def test_inconsistent_judge_output_is_retried():
    class Flaky:
        provider = "mock"
        model = "flaky"
        calls = 0

        def generate(self, schema, messages):
            self.calls += 1
            kind = "none" if self.calls == 1 else "threshold"
            return {"conflict": True, "kind": kind, "severity": "high", "reason": "12000 vs 11500"}, Usage()

    result = structured_call(ConflictJudgment, "RULE A", task="test.rules_judge", backend=Flaky())
    assert result.kind == "threshold"
    assert LLMCall.objects.get(task="test.rules_judge").attempts == 2


def test_invalid_extraction_is_retried():
    class Flaky:
        provider = "mock"
        model = "flaky"
        calls = 0

        def generate(self, schema, messages):
            self.calls += 1
            confidence = 1.7 if self.calls == 1 else 0.8
            return {"rules": [{"condition": "c", "action": "a", "source_quote": "q", "confidence": confidence}]}, Usage()

    result = structured_call(RuleSet, "CHUNK:\nq", task="test.rules_retry", backend=Flaky())
    assert result.rules[0].confidence == 0.8


def test_review_and_export(client, corpus):
    run_rules()
    rule = Rule.objects.get(action__startswith="may be deferred")
    response = client.post(reverse("rules:review", args=[rule.key]), {
        "action": "edit", "condition": "A missing non-essential placard", "action_text": "may be deferred under the MEL",
        "exception": "", "note": "added non-essential",
    })
    assert response.status_code == 302
    client.post(reverse("rules:review", args=[rule.key]), {"action": "approve", "note": "checked against MEL"})
    rule.refresh_from_db()
    assert rule.status == Rule.Status.APPROVED and rule.edited
    assert rule.original["condition"] == "A missing placard" and rule.reviewer_note == "checked against MEL"
    other = Rule.objects.exclude(key=rule.key).filter(status=Rule.Status.PENDING).first()
    client.post(reverse("rules:review", args=[other.key]), {"action": "reject", "note": "not a rule"})

    data = json.loads(client.get(reverse("rules:export", args=["json"])).content)
    assert data["count"] == 1 and data["rules"][0]["key"] == rule.key
    assert data["rules"][0]["llm_provider"] == "mock" and data["rules"][0]["page"] == 2
    rows = list(csv.DictReader(io.StringIO(client.get(reverse("rules:export", args=["csv"])).content.decode())))
    assert [row["key"] for row in rows] == [rule.key]
    assert rows[0]["condition"] == "A missing non-essential placard"


def test_bulk_approve_selected_and_above_threshold(client, corpus):
    run_rules()
    pending = list(Rule.objects.filter(status=Rule.Status.PENDING).order_by("key"))
    page = client.get(reverse("rules:index")).content.decode()
    assert "Approve selected" in page and "confidence" in page and "human judgement" in page
    rejected = pending[-1]
    Rule.objects.filter(pk=rejected.pk).update(status=Rule.Status.REJECTED)
    client.post(reverse("rules:bulk_approve"), {"mode": "selected", "rule": [pending[0].key, rejected.key]})
    assert Rule.objects.get(pk=pending[0].pk).status == Rule.Status.APPROVED
    assert Rule.objects.get(pk=rejected.pk).status == Rule.Status.REJECTED
    Rule.objects.filter(pk=pending[1].pk).update(confidence=0.5)
    Rule.objects.filter(pk=pending[2].pk).update(confidence=0.95)
    client.post(reverse("rules:bulk_approve"), {"mode": "threshold"})
    assert Rule.objects.get(pk=pending[1].pk).status == Rule.Status.PENDING
    assert Rule.objects.get(pk=pending[2].pk).status == Rule.Status.APPROVED


def test_pages_show_provider_counts_and_highlight(client, corpus):
    run_rules()
    page = client.get(reverse("rules:index")).content.decode()
    assert "LLM_PROVIDER=mock" in page and "mock-structured-v1" in page
    assert "dropped (quote not found)" in page and "conflict" in page
    rule = Rule.objects.get(action__contains="11500")
    detail = client.get(reverse("rules:detail", args=[rule.key])).content.decode()
    assert "<mark>IF part 65-45210-3 is installed on the narrow-body fleet," in detail
    filtered = client.get(reverse("rules:index"), {"status": "merged"}).content.decode()
    assert "Record the part number" in filtered and "replace it at 11500 flight hours" not in filtered


def test_gold_matching_and_scoring():
    gold = [{"id": "g1", "document": "d", "condition": ["12000"], "action": ["replace"]},
            {"id": "g2", "document": "d", "condition": ["placard"], "action": ["deferred"]}]
    predictions = [
        {"document": "d", "condition": "hours reach 12000", "action": "Replace the side-stay", "exception": ""},
        {"document": "d", "condition": "hours reach 12000", "action": "Replace it", "exception": ""},
        {"document": "other", "condition": "placard", "action": "deferred", "exception": ""},
    ]
    assert matches(predictions[0], gold[0]) and not matches(predictions[0], gold[1])
    result = score(predictions, gold)
    assert result["matched"] == 1 and result["precision"] == 0.333 and result["recall"] == 0.5


def test_eval_rules_command_reports_both_versions_and_provider(corpus):
    call_command("eval_rules")
    run = EvalRun.objects.get(kind="rules")
    assert set(run.metrics) == {"v1", "v2"} and run.llm_provider == "mock"
    assert "| v1 |" in run.report_md and "offline mock" in run.report_md
    assert run.config["missing_documents"]
