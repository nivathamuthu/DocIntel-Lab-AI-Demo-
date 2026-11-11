"""Precision and recall of extract_rules v1 and v2 against evals/datasets/rules_gold.yaml."""
from datetime import datetime, timezone
from pathlib import Path

import yaml
from django.conf import settings

from apps.ingest.models import Chunk, Document
from apps.rules.services.extract import run_extraction, verify_quotes
from apps.rules.services.text import fingerprint
from core.embeddings import describe as describe_embeddings
from core.llm import LLMValidationError, describe_backend
from core.models import EvalRun


def load_gold() -> dict:
    path = Path(settings.BASE_DIR) / "evals" / "datasets" / "rules_gold.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def matches(prediction: dict, gold: dict) -> bool:
    """Every gold condition keyword in condition or exception, every action keyword in action."""
    condition = f"{prediction['condition']} {prediction.get('exception', '')}".lower()
    action = prediction["action"].lower()
    return (all(word.lower() in condition for word in gold["condition"])
            and all(word.lower() in action for word in gold["action"]))


def score(predictions: list[dict], gold_rules: list[dict]) -> dict:
    """Greedy one-to-one matching inside each document, gold rules in file order."""
    used: set[int] = set()
    hits = {}
    for gold in gold_rules:
        for index, prediction in enumerate(predictions):
            if index in used or prediction["document"] != gold["document"]:
                continue
            if matches(prediction, gold):
                used.add(index)
                hits[gold["id"]] = index
                break
    matched = len(hits)
    return {
        "predicted": len(predictions),
        "matched": matched,
        "precision": round(matched / len(predictions), 3) if predictions else 0.0,
        "recall": round(matched / len(gold_rules), 3) if gold_rules else 0.0,
        "hits": hits,
        "unmatched": [index for index in range(len(predictions)) if index not in used],
    }


def _gold_documents(titles: list[str]) -> list[Document]:
    """The newest finished upload of each gold document."""
    found = []
    for title in titles:
        document = Document.objects.filter(title=title, status=Document.Status.DONE).order_by("-created_at").first()
        if document:
            found.append(document)
    return found


def predictions_for(version: str, documents: list[Document], force: bool = False) -> tuple[list[dict], dict]:
    predictions: list[dict] = []
    stats = {"chunks": 0, "raw": 0, "dropped": 0, "failed": 0, "ran": 0}
    for document in documents:
        seen = set()
        for chunk in Chunk.objects.filter(document=document).order_by("ordinal"):
            stats["chunks"] += 1
            try:
                extraction, ran = run_extraction(chunk, version, force=force)
            except LLMValidationError:
                stats["failed"] += 1
                continue
            stats["ran"] += int(ran)
            rules = extraction.raw_llm_output.get("rules", [])
            kept, dropped = verify_quotes(rules, chunk.text)
            stats["raw"] += len(rules)
            stats["dropped"] += len(dropped)
            for rule in kept:
                key = fingerprint(rule["condition"], rule["action"])
                if key in seen:
                    continue
                seen.add(key)
                predictions.append({**rule, "document": document.title, "chunk_id": chunk.id, "page": chunk.page_start})
    return predictions, stats


def run_eval(versions: tuple[str, ...] = ("v1", "v2"), force: bool = False) -> EvalRun:
    gold = load_gold()
    documents = _gold_documents(gold["documents"])
    missing = sorted(set(gold["documents"]) - {document.title for document in documents})
    results = {}
    for version in versions:
        predictions, stats = predictions_for(version, documents, force=force)
        results[version] = {"stats": stats, "predictions": predictions, **score(predictions, gold["rules"])}
    llm = describe_backend()
    embeddings = describe_embeddings()
    metrics = {
        version: {key: result[key] for key in ("predicted", "matched", "precision", "recall")}
        | {"dropped": result["stats"]["dropped"], "raw": result["stats"]["raw"]}
        for version, result in results.items()
    }
    report = _report(gold, results, llm, embeddings, missing)
    results_dir = Path(settings.EVAL_RESULTS_DIR)
    results_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    (results_dir / f"rules_{stamp}.md").write_text(report, encoding="utf-8")
    return EvalRun.objects.create(
        kind="rules",
        llm_provider=llm["provider"],
        llm_model=llm["model"],
        embeddings_provider=embeddings["provider"],
        embeddings_model=embeddings["model"],
        config={"versions": list(versions), "gold_rules": len(gold["rules"]),
                "documents": [document.title for document in documents], "missing_documents": missing},
        metrics=metrics,
        report_md=report,
    )


def _short(text: str, limit: int = 110) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 3] + "..."


def table(metrics: dict) -> list[str]:
    lines = [
        "| prompt | raw rules | dropped (quote not found) | kept (deduplicated) | matched gold | precision | recall |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for version, row in metrics.items():
        lines.append(
            f"| {version} | {row['raw']} | {row['dropped']} | {row['predicted']} | {row['matched']} "
            f"| {row['precision']} | {row['recall']} |"
        )
    return lines


def _report(gold, results, llm, embeddings, missing) -> str:
    gold_rules = gold["rules"]
    metrics = {
        version: {key: result[key] for key in ("predicted", "matched", "precision", "recall")}
        | {"dropped": result["stats"]["dropped"], "raw": result["stats"]["raw"]}
        for version, result in results.items()
    }
    lines = [
        "# Rule extraction eval: extract_rules v1 vs v2",
        "",
        f"- LLM: `{llm['provider']}` / `{llm['model']}`",
        f"- Embeddings: `{embeddings['provider']}` / `{embeddings['model']}` (not used for scoring)",
        f"- Gold: {len(gold_rules)} hand-written rules from {', '.join(gold['documents'])}",
        "- precision = matched / kept rules, recall = matched / gold rules. One prediction matches at most one gold rule.",
    ]
    if missing:
        lines.append(f"- Missing documents (not ingested): {', '.join(missing)}")
    lines += ["", *table(metrics), "", "## Gold rules found", "", "| gold | " + " | ".join(results) + " |",
              "| --- |" + " --- |" * len(results)]
    for rule in gold_rules:
        cells = []
        for result in results.values():
            index = result["hits"].get(rule["id"])
            cells.append(result["predictions"][index]["source_quote"][:60] + "..." if index is not None else "**miss**")
        lines.append(f"| {rule['id']} | " + " | ".join(cells) + " |")
    for version, result in results.items():
        lines += ["", f"## {version}: kept rules that match no gold rule ({len(result['unmatched'])})", ""]
        for index in result["unmatched"][:30]:
            prediction = result["predictions"][index]
            lines.append(f"- [{prediction['document']}] IF {_short(prediction['condition'], 70)} "
                         f"THEN {_short(prediction['action'], 70)}")
        if len(result["unmatched"]) > 30:
            lines.append(f"- ... {len(result['unmatched']) - 30} more")
    lines += [
        "",
        "The gold set is not exhaustive. Some unmatched rules above are correct extractions of",
        "instructions the gold file leaves out, so precision is a lower bound. Read the list.",
    ]
    if llm["provider"] == "mock":
        lines += [
            "",
            "These numbers come from the offline mock, not a model. The mock extractor is a set of clause",
            "patterns written while looking at these three documents, so v2's score says the patterns fit",
            "this corpus, not that the v2 prompt is good. Set LLM_PROVIDER=anthropic or openai and rerun",
            "for a real comparison of the two prompts.",
        ]
    lines.append("")
    return "\n".join(lines)
