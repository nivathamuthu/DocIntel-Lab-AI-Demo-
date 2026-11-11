"""hit@5 for each retrieval mode on evals/datasets/rag_questions.yaml."""
from datetime import datetime, timezone
from pathlib import Path

import yaml
from django.conf import settings

from apps.rag.services.retrieve import MODES, retrieve
from core.embeddings import describe as describe_embeddings
from core.llm import describe_backend
from core.models import EvalRun


def load_questions() -> list[dict]:
    path = Path(settings.BASE_DIR) / "evals" / "datasets" / "rag_questions.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def hit_at_k(texts: list[str], expects: list[str], k: int = 5) -> bool:
    """True when any expected string appears in the top k chunk texts."""
    blob = "\n".join(texts[:k]).lower()
    return any(expect.lower() in blob for expect in expects)


def run_eval(k: int = 5) -> EvalRun:
    questions = load_questions()
    per_mode = {mode: [] for mode in MODES}
    for question in questions:
        for mode in MODES:
            hits = retrieve(question["question"], mode, k=k)
            per_mode[mode].append(hit_at_k([hit.text for hit in hits], question["expect"], k=k))
    scores = {mode: round(sum(flags) / len(flags), 3) if flags else 0.0 for mode, flags in per_mode.items()}
    llm = describe_backend()
    embeddings = describe_embeddings()
    report = _report(questions, scores, llm, embeddings, k)
    results_dir = Path(settings.EVAL_RESULTS_DIR)
    results_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    (results_dir / f"rag_{stamp}.md").write_text(report, encoding="utf-8")
    return EvalRun.objects.create(
        kind="rag",
        llm_provider=llm["provider"],
        llm_model=llm["model"],
        embeddings_provider=embeddings["provider"],
        embeddings_model=embeddings["model"],
        config={"k": k, "questions": len(questions), "modes": list(MODES)},
        metrics={"hit_at_k": scores},
        report_md=report,
    )


def _report(questions, scores, llm, embeddings, k) -> str:
    lines = [
        f"# Retrieval eval: hit@{k}",
        "",
        f"- LLM: `{llm['provider']}` / `{llm['model']}`",
        f"- Embeddings: `{embeddings['provider']}` / `{embeddings['model']}` ({embeddings['dimensions']}-dim)",
        f"- Questions: {len(questions)}",
        "",
        "| mode | hit@{k} |".replace("{k}", str(k)),
        "| --- | --- |",
    ]
    for mode, score in scores.items():
        lines.append(f"| {mode} | {score} |")
    if embeddings["provider"] == "mock":
        lines += [
            "",
            "Mock embeddings are a lexical hash, so vector and BM25 often agree on exact part numbers.",
            "Set EMBEDDINGS_PROVIDER=openai, run `python manage.py reembed`, then run this eval again",
            "to measure the semantic encoder.",
        ]
    lines.append("")
    return "\n".join(lines)
