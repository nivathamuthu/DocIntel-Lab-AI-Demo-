"""Quote check, groundedness threshold, generation and cache, multi-hop pairs, review, export, eval, pages. Offline."""
import csv
import io
import json

import pytest
from django.core.files.base import ContentFile
from django.core.management import call_command
from django.urls import reverse

from apps.ingest.models import Chunk, Document
from apps.kg.models import KGExtraction
from apps.qa_gen.models import QAPair, QARun
from apps.qa_gen.services.evaluate import run_eval
from apps.qa_gen.services.generate import run_generation, totals
from apps.qa_gen.services.present import segments
from apps.qa_gen.services.verify import check_quotes, groundedness_verdict
from core.embeddings import embed_texts
from core.models import EvalRun, LLMCall

pytestmark = pytest.mark.django_db

PROCEDURE = ("Part 65-45210-3 must be replaced when its accumulated flight hours reach 12000. "
             "The replacement may be deferred for 50 flight hours when PN-77-4418 is on order.")
ORDER = ("The fleet order sets the replacement of part 65-45210-3 at 11500 flight hours on the narrow-body fleet. "
         "Quote AD 2023-14-09 and PN-77-4418 together on the work order.")


def _chunk(title: str, text: str, sha: str) -> Chunk:
    document = Document(title=title, original_name=f"{title}.docx", sha256=sha * 64, size_bytes=10,
                        file_type="docx", status=Document.Status.DONE)
    document.file.save(f"{title}.docx", ContentFile(b"x"), save=False)
    document.save()
    chunk = Chunk.objects.create(
        document=document, ordinal=0, text=text, text_hash=f"{sha}0", page_start=2,
        section_title="Replacement", section_path="Replacement", token_count=30, metadata={},
        embedding=embed_texts([text])[0].tolist(),
    )
    KGExtraction.objects.create(chunk=chunk, prompt_version="v2", llm_provider="mock",
                                entities=[{"name": "65-45210-3", "type": "Part", "aliases": []}])
    return chunk


@pytest.fixture
def corpus():
    return [_chunk("procedure", PROCEDURE, "a"), _chunk("order", ORDER, "b")]


def test_quote_must_be_verbatim_in_the_source():
    assert check_quotes("factual", ["The replacement may be deferred for 50 flight hours"], [PROCEDURE]) == ""
    assert "not found" in check_quotes("factual", ["The replacement may be deferred for 50 FH"], [PROCEDURE])
    assert check_quotes("factual", [], [PROCEDURE]) == "No supporting quote was given."


def test_multi_hop_needs_a_quote_from_each_chunk():
    quote_a = "Part 65-45210-3 must be replaced when its accumulated flight hours reach 12000."
    quote_b = "Quote AD 2023-14-09 and PN-77-4418 together on the work order."
    assert check_quotes("multi_hop", [quote_a, quote_b], [PROCEDURE, ORDER]) == ""
    assert "each linked chunk" in check_quotes("multi_hop", [quote_a], [PROCEDURE, ORDER])


def test_groundedness_threshold_keeps_at_and_above_only():
    assert groundedness_verdict(0.8, 0.8) == QAPair.Verdict.KEPT
    assert groundedness_verdict(0.79, 0.8) == QAPair.Verdict.DROPPED_GROUNDEDNESS


def test_generation_keeps_drops_and_counts(corpus):
    stats = run_generation()
    assert stats["chunks"] == 2 and stats["linked_pairs"] == 1 and stats["ran"] == 3
    counts = totals()
    assert counts["generated"] == 7  # 2 x (2 factual + 1 unanswerable) + 1 multi-hop
    assert counts["kept"] >= 4
    assert counts["unjudged"] == 0
    multi = QAPair.objects.get(qtype=QAPair.QType.MULTI_HOP)
    assert multi.chunks.count() == 2 and multi.verdict == QAPair.Verdict.KEPT
    assert all(pair.llm_provider == "mock" for pair in QAPair.objects.all())


def test_raising_the_threshold_drops_pairs_for_low_groundedness(corpus, settings):
    settings.QA_MIN_GROUNDEDNESS = 0.95
    run_generation()
    counts = totals()
    assert counts["dropped_grounded"] >= 1
    assert all(pair.groundedness >= 0.95 for pair in QAPair.objects.filter(verdict=QAPair.Verdict.KEPT))


def test_v1_paraphrases_quotes_and_they_are_dropped(corpus):
    run_generation(version="v1")
    assert QAPair.objects.filter(verdict=QAPair.Verdict.DROPPED_QUOTE).exists()


def test_unchanged_chunks_are_not_sent_to_the_model_again(corpus):
    run_generation()
    calls = LLMCall.objects.filter(task="qa.generate").count()
    stats = run_generation()
    assert stats["ran"] == 0 and stats["cached"] == 3
    assert LLMCall.objects.filter(task="qa.generate").count() == calls


def test_segments_mark_the_quote():
    parts = segments(PROCEDURE, ["may be deferred for 50 flight hours"])
    assert [part["text"] for part in parts if part["mark"]] == ["may be deferred for 50 flight hours"]
    assert "".join(part["text"] for part in parts) == PROCEDURE


def test_eval_reports_accuracy_per_type_and_mode(corpus):
    run_generation()
    eval_run = run_eval(per_type=4)
    assert eval_run.kind == "qa" and eval_run.config["same_model"] is True
    assert set(eval_run.metrics["overall"]) == {"vector", "bm25", "hybrid_rerank"}
    assert "unanswerable" in eval_run.metrics["accuracy"]
    assert "same model" in eval_run.report_md
    assert eval_run.metrics["takeaway"]


def test_eval_without_kept_pairs_explains_itself():
    with pytest.raises(ValueError, match="Generate Q&A first"):
        run_eval()


def test_commands_print_counts(corpus):
    out = io.StringIO()
    call_command("generate_qa", stdout=out)
    assert "generated 7" in out.getvalue()
    out = io.StringIO()
    call_command("eval_qa", "--per-type", "2", stdout=out)
    assert "hybrid_rerank" in out.getvalue()
    assert EvalRun.objects.filter(kind="qa").count() == 1


def test_page_renders_empty(client):
    response = client.get(reverse("qa:index"))
    assert response.status_code == 200
    page = response.content.decode()
    assert "No Q&amp;A yet." in page and "Generate Q&amp;A" in page
    assert "LLM_PROVIDER=mock" in page


def test_page_renders_with_data_and_filters(client, corpus):
    client.post(reverse("qa:generate"))
    run = QARun.objects.get(kind=QARun.Kind.GENERATE)
    assert run.status == QARun.Status.DONE
    page = client.get(reverse("qa:index")).content.decode()
    assert "Reading chunk" not in page or "Finished" in page
    assert "Multi-hop" in page and "dropped (quote not found)" in page
    filtered = client.get(reverse("qa:index"), {"type": "unanswerable"}).content.decode()
    assert "Multi-hop</span>" not in filtered
    client.post(reverse("qa:evaluate"))
    page = client.get(reverse("qa:index")).content.decode()
    assert "Takeaway:" in page and "same model" in page
    results = client.get(reverse("results")).content.decode()
    assert "6 · Q&amp;A against Search" in results and "Takeaway:" in results


def test_panel_review_and_export(client, corpus):
    run_generation()
    pair = QAPair.objects.filter(verdict=QAPair.Verdict.KEPT).first()
    panel = client.get(reverse("qa:panel", args=[pair.id])).content.decode()
    assert "<mark>" in panel and "Approve" in panel
    response = client.post(reverse("qa:review", args=[pair.id]), {"action": "approve"}, HTTP_HX_REQUEST="true")
    assert response.status_code == 200 and 'hx-swap-oob="true"' in response.content.decode()
    pair.refresh_from_db()
    assert pair.review == QAPair.Review.APPROVED
    rows = json.loads(client.get(reverse("qa:export", args=["json"])).content)
    assert [row["question"] for row in rows] == [pair.question]
    reader = csv.DictReader(io.StringIO(client.get(reverse("qa:export", args=["csv"])).content.decode()))
    assert [row["question"] for row in reader] == [pair.question]


def test_bulk_approve_selected_and_above_threshold(client, corpus):
    run_generation()
    kept = list(QAPair.objects.filter(verdict=QAPair.Verdict.KEPT).order_by("id"))
    dropped = kept.pop()
    QAPair.objects.filter(id=dropped.id).update(verdict=QAPair.Verdict.DROPPED_QUOTE)
    page = client.get(reverse("qa:index")).content.decode()
    assert "Approve selected" in page and "above 0.9 groundedness" in page and "human judgement" in page
    assert "kept, not reviewed yet" in page
    client.post(reverse("qa:bulk_approve"), {"mode": "selected", "pair": [kept[0].id, dropped.id]})
    kept[0].refresh_from_db()
    dropped.refresh_from_db()
    assert kept[0].review == QAPair.Review.APPROVED and dropped.review == QAPair.Review.PENDING
    QAPair.objects.filter(id=kept[1].id).update(groundedness=0.85)
    client.post(reverse("qa:bulk_approve"), {"mode": "threshold"})
    assert QAPair.objects.get(id=kept[1].id).review == QAPair.Review.PENDING
    high = [p for p in kept[2:] if p.groundedness > 0.9]
    assert all(QAPair.objects.get(id=p.id).review == QAPair.Review.APPROVED for p in high)


def test_dropped_pairs_cannot_be_approved(client, corpus):
    run_generation(version="v1")
    pair = QAPair.objects.filter(verdict=QAPair.Verdict.DROPPED_QUOTE).first()
    response = client.post(reverse("qa:review", args=[pair.id]), {"action": "approve"}, HTTP_HX_REQUEST="true")
    assert "cannot be approved" in response.content.decode()
    pair.refresh_from_db()
    assert pair.review == QAPair.Review.PENDING
