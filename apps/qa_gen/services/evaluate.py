"""Use the kept Q&A set to evaluate Module 3 Search: accuracy per question type and retrieval mode."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from django.conf import settings
from django.db.models import Case, IntegerField, Value, When

from apps.qa_gen.models import QAPair, QARun
from apps.qa_gen.schemas import AnswerJudgment
from apps.rag.services.answer import answer_question
from core.embeddings import describe as describe_embeddings
from core.llm import describe_backend, structured_call
from core.models import EvalRun
from core.prompts import render_prompt

TYPES = [QAPair.QType.FACTUAL, QAPair.QType.MULTI_HOP, QAPair.QType.UNANSWERABLE]
SAME_MODEL_NOTE = ("The same model wrote the questions, judged their groundedness and judged the answers, "
                   "so the judge may share the generator's blind spots.")


def sample_pairs(per_type: int) -> list[QAPair]:
    """Up to `per_type` kept, not rejected pairs per type, approved ones first."""
    picked: list[QAPair] = []
    for qtype in TYPES:
        rows = (QAPair.objects.filter(verdict=QAPair.Verdict.KEPT, qtype=qtype)
                .exclude(review=QAPair.Review.REJECTED)
                .annotate(first=Case(When(review=QAPair.Review.APPROVED, then=Value(0)), default=Value(1),
                                     output_field=IntegerField()))
                .order_by("first", "id"))
        picked.extend(rows[:per_type])
    return picked


def judge_answer(pair: QAPair, result: dict) -> tuple[bool, str]:
    """Unanswerable: correct only when Search abstains. Otherwise an LLM judge compares with the reference."""
    if pair.qtype == QAPair.QType.UNANSWERABLE:
        if result["known"]:
            return False, "Answered a question the documents do not answer."
        return True, "Said it does not know, as expected."
    if not result["known"]:
        return False, "Said it does not know."
    prompt = render_prompt("judge_answer", "v1", {
        "question": pair.question, "reference": pair.answer, "answer": result["answer"], "domain": "aviation maintenance",
    }, AnswerJudgment)
    verdict = structured_call(AnswerJudgment, prompt, task="qa.judge_answer", prompt_name="judge_answer",
                              prompt_version="v1")
    return verdict.correct, verdict.reason


def _progress(run: QARun | None, done: int, total: int, message: str) -> None:
    if run is not None:
        QARun.objects.filter(id=run.id).update(status=QARun.Status.RUNNING, done=done, total=total, message=message)


def takeaway(accuracy: dict, overall: dict, counts: dict) -> str:
    """One plain sentence: the best mode overall and the hardest question type."""
    if not overall:
        return ""
    best = max(overall.values())
    leaders = [mode for mode, score in overall.items() if score == best]
    lead = (f"{leaders[0]} answered {round(best * 100)}% correctly, the best of the modes tried"
            if len(leaders) == 1 else f"{' and '.join(leaders)} tie at {round(best * 100)}% correct")
    scored = {qtype: max(row.values()) for qtype, row in accuracy.items() if counts.get(qtype)}
    if not scored:
        return lead + "."
    hardest = min(scored, key=scored.get)
    return f"{lead}; {hardest.replace('_', '-')} questions were the hardest ({round(scored[hardest] * 100)}% at best)."


def run_eval(run: QARun | None = None, per_type: int | None = None, modes: list[str] | None = None) -> EvalRun:
    per_type = per_type or settings.QA_EVAL_MAX_PER_TYPE
    modes = list(modes or settings.QA_EVAL_MODES)
    pairs = sample_pairs(per_type)
    if not pairs:
        raise ValueError("No kept Q&A pairs to evaluate. Generate Q&A first.")
    total = len(pairs) * len(modes)
    rows, step = [], 0
    for index, pair in enumerate(pairs, start=1):
        row = {"id": pair.id, "qtype": pair.qtype, "question": pair.question, "reference": pair.answer,
               "review": pair.review, "results": {}}
        for mode in modes:
            _progress(run, step, total, f"Asking question {index} of {len(pairs)} with {mode}")
            result = answer_question(pair.question, mode)
            correct, reason = judge_answer(pair, result)
            row["results"][mode] = {"answer": result["answer"], "known": result["known"],
                                    "correct": correct, "reason": reason}
            step += 1
        rows.append(row)
    counts = {qtype: sum(1 for row in rows if row["qtype"] == qtype) for qtype in TYPES}
    accuracy = {
        qtype: {mode: round(sum(row["results"][mode]["correct"] for row in rows if row["qtype"] == qtype)
                            / counts[qtype], 3) for mode in modes}
        for qtype in TYPES if counts[qtype]
    }
    overall = {mode: round(sum(row["results"][mode]["correct"] for row in rows) / len(rows), 3) for mode in modes}
    metrics = {"modes": modes, "counts": counts, "accuracy": accuracy, "overall": overall,
               "takeaway": takeaway(accuracy, overall, counts), "rows": rows}
    _progress(run, total, total, "Finished")
    llm, embeddings = describe_backend(), describe_embeddings()
    report = _report(metrics, llm, embeddings, per_type)
    results_dir = Path(settings.EVAL_RESULTS_DIR)
    results_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    (results_dir / f"qa_{stamp}.md").write_text(report, encoding="utf-8")
    return EvalRun.objects.create(
        kind="qa", llm_provider=llm["provider"], llm_model=llm["model"],
        embeddings_provider=embeddings["provider"], embeddings_model=embeddings["model"],
        config={"per_type": per_type, "modes": modes, "questions": len(rows),
                "min_groundedness": settings.QA_MIN_GROUNDEDNESS, "prompt_version": settings.QA_PROMPT_VERSION,
                "same_model": True},
        metrics=metrics, report_md=report,
    )


def _report(metrics: dict, llm: dict, embeddings: dict, per_type: int) -> str:
    modes = metrics["modes"]
    lines = [
        "# Q&A eval: generated questions against Search",
        "",
        f"- LLM: `{llm['provider']}` / `{llm['model']}` (generator, groundedness judge and answer judge)",
        f"- Embeddings: `{embeddings['provider']}` / `{embeddings['model']}`",
        f"- Questions: {sum(metrics['counts'].values())} kept pairs, at most {per_type} per type "
        f"(min groundedness {settings.QA_MIN_GROUNDEDNESS})",
        "",
        "| question type | n | " + " | ".join(modes) + " |",
        "| --- | --- | " + " | ".join("---" for _ in modes) + " |",
    ]
    for qtype, row in metrics["accuracy"].items():
        lines.append(f"| {qtype} | {metrics['counts'][qtype]} | " + " | ".join(str(row[mode]) for mode in modes) + " |")
    lines.append(f"| **all** | {sum(metrics['counts'].values())} | "
                 + " | ".join(f"**{metrics['overall'][mode]}**" for mode in modes) + " |")
    lines += [
        "",
        metrics["takeaway"],
        "",
        "Caveats:",
        f"- {SAME_MODEL_NOTE}",
        "- Questions are written from the chunks themselves, so their wording overlaps the source; this favours exact-word retrieval.",
        "- An unanswerable question is scored only on whether Search abstains.",
        "- Small sample; one question moves a cell by a large step.",
        "",
        "## Per question",
        "",
    ]
    for row in metrics["rows"]:
        marks = ", ".join(f"{mode} {'yes' if row['results'][mode]['correct'] else 'no'}" for mode in modes)
        lines.append(f"- Q{row['id']} ({row['qtype']}): {row['question']} -> {marks}")
    if llm["provider"] == "mock":
        lines += ["", "These numbers come from the offline mock. They test the wiring, not a model."]
    lines.append("")
    return "\n".join(lines)
