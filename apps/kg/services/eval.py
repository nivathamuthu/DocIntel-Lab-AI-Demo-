"""Plain hybrid RAG vs GraphRAG on evals/datasets/kg_questions.yaml."""
from datetime import datetime, timezone
from pathlib import Path

import yaml
from django.conf import settings

from apps.kg.models import GraphBuild
from apps.kg.services.graphrag import graph_answer
from apps.rag.services.answer import answer_question
from core.embeddings import describe as describe_embeddings
from core.llm import describe_backend
from core.models import EvalRun


def load_questions() -> list[dict]:
    path = Path(settings.BASE_DIR) / "evals" / "datasets" / "kg_questions.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def score(answer: str, expects: list[str]) -> float:
    """Share of expected strings that appear in the answer, case-insensitive."""
    if not expects:
        return 0.0
    lowered = answer.lower()
    return round(sum(1 for expect in expects if expect.lower() in lowered) / len(expects), 3)


def run_eval() -> EvalRun:
    questions = load_questions()
    rows = []
    for question in questions:
        plain = answer_question(question["question"], "hybrid")
        graph = graph_answer(question["question"])
        rows.append({
            "id": question["id"],
            "question": question["question"],
            "expect": question["expect"],
            "hybrid": {"answer": plain["answer"], "score": score(plain["answer"], question["expect"]),
                       "citations": plain["citations"]},
            "graphrag": {"answer": graph["answer"], "score": score(graph["answer"], question["expect"]),
                         "citations": graph["citations"],
                         "path": [f"{f['source']} -{f['relation']}-> {f['target']}" for f in graph["path"]],
                         "seeds": [seed["name"] for seed in graph["seeds"]]},
        })
    count = len(rows) or 1
    metrics = {
        "hybrid": round(sum(row["hybrid"]["score"] for row in rows) / count, 3),
        "graphrag": round(sum(row["graphrag"]["score"] for row in rows) / count, 3),
        "hybrid_exact": sum(1 for row in rows if row["hybrid"]["score"] == 1.0),
        "graphrag_exact": sum(1 for row in rows if row["graphrag"]["score"] == 1.0),
        "rows": rows,
    }
    llm = describe_backend()
    embeddings = describe_embeddings()
    build = GraphBuild.objects.first()
    report = _report(rows, metrics, llm, embeddings, build)
    results_dir = Path(settings.EVAL_RESULTS_DIR)
    results_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    (results_dir / f"kg_{stamp}.md").write_text(report, encoding="utf-8")
    return EvalRun.objects.create(
        kind="kg",
        llm_provider=llm["provider"],
        llm_model=llm["model"],
        embeddings_provider=embeddings["provider"],
        embeddings_model=embeddings["model"],
        config={
            "questions": len(rows),
            "graph_backend": build.backend if build else settings.GRAPH_BACKEND,
            "kg_prompt_version": build.prompt_version if build else settings.KG_PROMPT_VERSION,
            "graph_build_id": build.id if build else None,
        },
        metrics=metrics,
        report_md=report,
    )


def _short(text: str, limit: int = 200) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 3] + "..."


def verdict(rows: list[dict], metrics: dict) -> list[str]:
    wins = [row for row in rows if row["graphrag"]["score"] > row["hybrid"]["score"]]
    losses = [row for row in rows if row["graphrag"]["score"] < row["hybrid"]["score"]]
    ties = len(rows) - len(wins) - len(losses)
    if metrics["graphrag"] > metrics["hybrid"]:
        head = f"GraphRAG scores higher: {metrics['graphrag']} vs {metrics['hybrid']}."
    elif metrics["graphrag"] == metrics["hybrid"]:
        head = f"GraphRAG does not win: both score {metrics['graphrag']}."
    else:
        head = f"GraphRAG does not win: {metrics['graphrag']} vs {metrics['hybrid']} for plain hybrid."
    lines = [head, f"GraphRAG better on {len(wins)}, worse on {len(losses)}, tied on {ties}."]
    if wins:
        best = max(wins, key=lambda row: row["graphrag"]["score"] - row["hybrid"]["score"])
        lines.append(f"Case where GraphRAG wins ({best['id']}): {best['question']}")
        lines.append(f"  hybrid: {_short(best['hybrid']['answer'])}")
        lines.append(f"  graphrag: {_short(best['graphrag']['answer'])}")
        if best["graphrag"]["path"]:
            lines.append(f"  path: {' | '.join(best['graphrag']['path'][:4])}")
    other = losses or [row for row in rows if row["graphrag"]["score"] == row["hybrid"]["score"]]
    if other:
        row = other[0]
        label = "loses" if losses else "ties"
        lines.append(f"Case where GraphRAG {label} ({row['id']}): {row['question']}")
        lines.append(f"  hybrid: {_short(row['hybrid']['answer'])}")
        lines.append(f"  graphrag: {_short(row['graphrag']['answer'])}")
    misses = [row for row in rows if row["graphrag"]["score"] < 1.0]
    for row in misses:
        lines.append(f"GraphRAG incomplete on {row['id']} ({row['graphrag']['score']}): {_short(row['graphrag']['answer'])}")
    lines.append("The score is recall only. A GraphRAG list can also name extra, loosely linked entities.")
    return lines


def _report(rows, metrics, llm, embeddings, build) -> str:
    lines = [
        "# GraphRAG eval: plain hybrid vs GraphRAG",
        "",
        f"- LLM: `{llm['provider']}` / `{llm['model']}`",
        f"- Embeddings: `{embeddings['provider']}` / `{embeddings['model']}` ({embeddings['dimensions']}-dim)",
    ]
    if build:
        lines.append(
            f"- Graph: `{build.backend}`, extraction prompt `{build.prompt_version}`, "
            f"{build.node_count} nodes, {build.edge_count} edges, {build.merged_count} merged, "
            f"{build.dropped_count} dropped relations (build {build.id})"
        )
    lines += [
        f"- Questions: {len(rows)}. Score is the share of expected strings found in the answer.",
        "",
        "| question | hybrid | graphrag |",
        "| --- | --- | --- |",
    ]
    for row in rows:
        lines.append(f"| {row['id']} | {row['hybrid']['score']} | {row['graphrag']['score']} |")
    lines.append(f"| **mean** | **{metrics['hybrid']}** | **{metrics['graphrag']}** |")
    lines += ["", *verdict(rows, metrics)]
    if llm["provider"] == "mock":
        lines += [
            "",
            "Both answers come from the offline mock. The hybrid answer is the single best sentence from the",
            "retrieved chunks; the GraphRAG answer lists the graph facts of the asked-for type. The gap shows",
            "what context each method hands the model. Set LLM_PROVIDER=anthropic or openai and rerun for a",
            "model-graded result.",
            "The fact ranking was tuned while writing these 8 questions, so the GraphRAG score is an upper",
            "bound for this corpus. New questions are the fair test.",
        ]
    lines.append("")
    return "\n".join(lines)
