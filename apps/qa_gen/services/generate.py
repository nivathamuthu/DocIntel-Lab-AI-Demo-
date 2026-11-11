"""Generate Q&A pairs per chunk and per linked chunk pair, verify quotes, and judge groundedness."""
from __future__ import annotations

import hashlib
import logging

from django.conf import settings
from django.db.models import Count, Q

from apps.ingest.models import Chunk, Document
from apps.qa_gen.models import QAGeneration, QAPair, QARun
from apps.qa_gen.schemas import UNANSWERABLE, Groundedness, QASet
from apps.qa_gen.services.pairs import related_pairs
from apps.qa_gen.services.verify import check_quotes, groundedness_verdict
from core.llm import LLMValidationError, describe_backend, structured_call
from core.prompts import render_prompt

logger = logging.getLogger(__name__)

DOMAIN = "aviation maintenance"
NOT_JUDGED = "Not judged yet."


def chunk_scope():
    return (Chunk.objects.filter(document__status=Document.Status.DONE)
            .select_related("document").order_by("document_id", "ordinal"))


def _text_hash(*chunks: Chunk) -> str:
    return hashlib.sha256("|".join(chunk.text_hash for chunk in chunks).encode()).hexdigest()


def _citation(chunk: Chunk) -> str:
    page = f", p.{chunk.page_start}" if chunk.page_start else ""
    return f"{chunk.document.title}{page}"


def judge_pair(pair: QAPair, sources: list[Chunk], version: str | None = None) -> QAPair:
    """Score groundedness 0-1 with a reason, and keep the pair when it reaches QA_MIN_GROUNDEDNESS."""
    version = version or settings.QA_JUDGE_PROMPT_VERSION
    source = "\n\n".join(f"[{_citation(chunk)}]\n{chunk.text}" for chunk in sources)
    prompt = render_prompt("judge_groundedness", version, {
        "qtype": pair.qtype, "question": pair.question, "answer": pair.answer, "source": source, "domain": DOMAIN,
    }, Groundedness)
    result = structured_call(Groundedness, prompt, task="qa.judge_groundedness",
                             prompt_name="judge_groundedness", prompt_version=version)
    pair.groundedness = round(result.score, 3)
    pair.judge_reason = result.reason
    pair.judge_prompt_version = version
    pair.verdict = groundedness_verdict(result.score, settings.QA_MIN_GROUNDEDNESS)
    pair.save(update_fields=["groundedness", "judge_reason", "judge_prompt_version", "verdict"])
    return pair


def _store_items(generation: QAGeneration, output: QASet, sources: list[Chunk], stats: dict) -> list[QAPair]:
    wanted = {QAPair.QType.MULTI_HOP} if generation.pair_chunk_id else {QAPair.QType.FACTUAL, QAPair.QType.UNANSWERABLE}
    stored = []
    for item in output.items:
        if item.qtype not in wanted:
            stats["wrong_type"] = stats.get("wrong_type", 0) + 1
            continue
        answer = UNANSWERABLE if item.qtype == QAPair.QType.UNANSWERABLE else item.answer.strip()
        problem = check_quotes(item.qtype, item.supporting_quotes, [chunk.text for chunk in sources])
        pair = QAPair.objects.create(
            generation=generation, qtype=item.qtype, question=item.question.strip(), answer=answer,
            supporting_quotes=[quote.strip() for quote in item.supporting_quotes],
            prompt_version=generation.prompt_version, quote_problem=problem,
            verdict=QAPair.Verdict.DROPPED_QUOTE if problem else QAPair.Verdict.DROPPED_GROUNDEDNESS,
            judge_reason="" if problem else NOT_JUDGED,
            llm_provider=generation.llm_provider, llm_model=generation.llm_model,
        )
        pair.chunks.set(sources)
        stored.append(pair)
    return stored


def generate_unit(chunk: Chunk, pair_chunk: Chunk | None = None, shared: str = "", version: str | None = None,
                  force: bool = False, stats: dict | None = None) -> tuple[QAGeneration, bool]:
    """Return (generation, ran). The model is called only when the cache misses or force is set."""
    version = version or settings.QA_PROMPT_VERSION
    stats = stats if stats is not None else {}
    backend = describe_backend()
    sources = [chunk] + ([pair_chunk] if pair_chunk else [])
    text_hash = _text_hash(*sources)
    existing = QAGeneration.objects.filter(
        chunk=chunk, pair_chunk=pair_chunk, prompt_version=version,
        llm_provider=backend["provider"], llm_model=backend["model"],
    ).first()
    if existing and existing.text_hash == text_hash and not force:
        for pair in existing.pairs.filter(quote_problem="", groundedness__isnull=True):
            judge_pair(pair, sources)
        return existing, False
    if existing:
        existing.delete()
    prompt = render_prompt("generate_qa", version, {
        "chunk": chunk.text, "chunk_b": pair_chunk.text if pair_chunk else "", "shared": shared, "domain": DOMAIN,
    }, QASet)
    output = structured_call(QASet, prompt, task="qa.generate", prompt_name="generate_qa", prompt_version=version)
    generation = QAGeneration.objects.create(
        chunk=chunk, pair_chunk=pair_chunk, shared=shared[:300], prompt_version=version, text_hash=text_hash,
        llm_provider=backend["provider"], llm_model=backend["model"], raw_output=output.model_dump(),
    )
    for pair in _store_items(generation, output, sources, stats):
        if not pair.quote_problem:
            judge_pair(pair, sources)
    return generation, True


def _progress(run: QARun | None, done: int, total: int, message: str) -> None:
    if run is not None:
        QARun.objects.filter(id=run.id).update(status=QARun.Status.RUNNING, done=done, total=total, message=message)


def run_generation(run: QARun | None = None, force: bool = False, version: str | None = None) -> dict:
    """Every finished chunk, then up to QA_MAX_PAIRS graph-linked chunk pairs."""
    version = version or settings.QA_PROMPT_VERSION
    chunks = list(chunk_scope())
    pairs = related_pairs(chunks, settings.QA_MAX_PAIRS)
    total = len(chunks) + len(pairs)
    stats = {"prompt_version": version, "chunks": len(chunks), "linked_pairs": len(pairs),
             "ran": 0, "cached": 0, "failed": 0, "failures": []}
    units = [(chunk, None, "") for chunk in chunks] + list(pairs)
    for index, (chunk, pair_chunk, shared) in enumerate(units, start=1):
        if pair_chunk is None:
            message = f"Reading chunk {index} of {len(chunks)}"
        else:
            message = f"Linking chunk pair {index - len(chunks)} of {len(pairs)} ({shared})"
        _progress(run, index - 1, total, message)
        try:
            _generation, ran = generate_unit(chunk, pair_chunk, shared, version=version, force=force, stats=stats)
        except LLMValidationError as exc:
            stats["failed"] += 1
            stats["failures"].append(f"chunk {chunk.id}{'+' + str(pair_chunk.id) if pair_chunk else ''}: {exc}"[:300])
            logger.warning("qa generation failed for chunk %s: %s", chunk.id, exc)
            continue
        stats["ran" if ran else "cached"] += 1
    _progress(run, total, total, "Finished")
    stats["totals"] = totals()
    return stats


def totals() -> dict:
    counts = QAPair.objects.aggregate(
        generated=Count("id"),
        kept=Count("id", filter=Q(verdict=QAPair.Verdict.KEPT)),
        dropped_quote=Count("id", filter=Q(verdict=QAPair.Verdict.DROPPED_QUOTE)),
        dropped_grounded=Count("id", filter=Q(verdict=QAPair.Verdict.DROPPED_GROUNDEDNESS, groundedness__isnull=False)),
        approved=Count("id", filter=Q(verdict=QAPair.Verdict.KEPT, review=QAPair.Review.APPROVED)),
        unreviewed=Count("id", filter=Q(verdict=QAPair.Verdict.KEPT, review=QAPair.Review.PENDING)),
        rejected=Count("id", filter=Q(verdict=QAPair.Verdict.KEPT, review=QAPair.Review.REJECTED)),
        unjudged=Count("id", filter=Q(quote_problem="", groundedness__isnull=True)),
    )
    return counts
