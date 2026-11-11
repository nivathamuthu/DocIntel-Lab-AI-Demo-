"""Score v1 against v2 on the fixed sample file and store the table."""
from datetime import datetime, timezone
from pathlib import Path

import yaml
from django.conf import settings

from apps.prompts.schemas import SCHEMAS
from apps.prompts.services import run_prompt
from core.embeddings import describe as describe_embeddings
from core.llm import describe_backend
from core.models import EvalRun


def load_samples() -> list[dict]:
    path = Path(settings.BASE_DIR) / "evals" / "datasets" / "prompt_samples.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def score_sample(sample: dict, output) -> float:
    """1 when the output matches the sample's expectation, else 0."""
    if sample["task"] == "answer_with_citations":
        if not sample["answerable"]:
            return 1.0 if (not output.known or output.answer.strip().lower() == "unknown") else 0.0
        if not output.known:
            return 0.0
        return 1.0 if sample["expect"].lower() in output.answer.lower() else 0.0
    if not sample["has_rule"]:
        return 0.0 if output.found else 1.0
    if not output.found:
        return 0.0
    condition_ok = sample["expect_condition"].lower() in output.condition.lower()
    action_ok = sample["expect_action"].lower() in output.action.lower()
    return 1.0 if condition_ok and action_ok else 0.0


def output_payload(output) -> dict:
    return output.model_dump()


def run_eval() -> EvalRun:
    """Run both versions on every sample, write a markdown table, and store an EvalRun."""
    samples = load_samples()
    rows = []
    for sample in samples:
        schema = SCHEMAS[sample["task"]]
        variables = {
            "domain": sample.get("domain", "aviation maintenance"),
            "chunk": sample["chunk"],
            "question": sample["question"],
        }
        scored = {}
        for version in ("v1", "v2"):
            output = run_prompt(sample["task"], version, variables, schema)
            scored[version] = {"output": output_payload(output), "score": score_sample(sample, output)}
        rows.append({"id": sample["id"], "task": sample["task"], "question": sample["question"], **scored})

    v1_mean = _mean(row["v1"]["score"] for row in rows)
    v2_mean = _mean(row["v2"]["score"] for row in rows)
    llm = describe_backend()
    embeddings = describe_embeddings()
    report = _report(rows, v1_mean, v2_mean, llm, embeddings)
    results_dir = Path(settings.EVAL_RESULTS_DIR)
    results_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    (results_dir / f"prompts_{stamp}.md").write_text(report, encoding="utf-8")
    return EvalRun.objects.create(
        kind="prompts",
        llm_provider=llm["provider"],
        llm_model=llm["model"],
        embeddings_provider=embeddings["provider"],
        embeddings_model=embeddings["model"],
        config={"samples": len(rows), "versions": ["v1", "v2"]},
        metrics={"v1": v1_mean, "v2": v2_mean, "rows": rows},
        report_md=report,
    )


def _mean(values) -> float:
    values = list(values)
    return round(sum(values) / len(values), 3) if values else 0.0


def _report(rows, v1_mean, v2_mean, llm, embeddings) -> str:
    lines = [
        "# Prompt eval: v1 vs v2",
        "",
        f"- LLM: `{llm['provider']}` / `{llm['model']}`",
        f"- Embeddings: `{embeddings['provider']}` / `{embeddings['model']}` (not used by this eval)",
        f"- v1 mean: {v1_mean}",
        f"- v2 mean: {v2_mean}",
        "",
        "| sample | task | v1 | v2 |",
        "| --- | --- | --- | --- |",
    ]
    for row in rows:
        lines.append(f"| {row['id']} | {row['task']} | {row['v1']['score']} | {row['v2']['score']} |")
    lines.append("")
    return "\n".join(lines)
