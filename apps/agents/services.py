"""eval_agent: run evals/datasets/agent_cases.yaml through the graph and score each case."""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

import yaml
from django.conf import settings

from apps.agents import graph
from apps.agents.models import AgentRun
from apps.agents.nodes import ACKNOWLEDGES_CONFLICT
from core.embeddings import describe as describe_embeddings
from core.llm import describe_backend
from core.models import EvalRun

AUTO_APPROVE_NOTE = "auto-approved by eval_agent"


def load_cases() -> list[dict]:
    path = Path(settings.BASE_DIR) / "evals" / "datasets" / "agent_cases.yaml"
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def normalize_answer(text: str) -> str:
    return re.sub(r"(?<=\d),(?=\d{3}\b)", "", (text or "").lower())


def score_case(case: dict, run: AgentRun) -> dict:
    final = run.final or {}
    answer = final.get("answer", "") or run.draft.get("answer", "")
    intent_ok = run.intent in case["intent"]
    refused = run.status == AgentRun.Status.REFUSED
    if case["kind"] == "out_of_scope":
        answer_ok = refused
    else:
        text = normalize_answer(answer)
        answer_ok = (not refused and run.draft.get("status") == "answered"
                     and all(any(option.lower() in text for option in group) for group in case.get("answer_all", [])))
    conflicts = final.get("conflicts") or run.review.get("conflicts") or []
    surfaced = bool(conflicts) and bool(ACKNOWLEDGES_CONFLICT.search(answer))
    expected = case.get("expect_conflict")
    return {
        "id": case["id"],
        "kind": case["kind"],
        "run": run.id,
        "status": run.status,
        "intent": run.intent,
        "intent_ok": intent_ok,
        "answer_ok": answer_ok,
        "verified": None if refused else run.verified,
        "conflict_surfaced": surfaced,
        "conflict_expected": expected,
        "conflict_ok": None if expected is None else surfaced == expected,
        "loops": run.loops,
        "answer": answer,
        "warning": run.warning,
        "problems": run.review.get("problems", []),
        "error": run.error,
    }


def run_case(case: dict) -> AgentRun:
    run = graph.create_run(case["question"], source="eval")
    try:
        run = graph.execute(run)
        if run.status == AgentRun.Status.AWAITING_APPROVAL:
            run = graph.resume(run, approved=True, note=AUTO_APPROVE_NOTE)
    except Exception:
        run.refresh_from_db()
    return run


def run_eval(only: list[str] | None = None) -> EvalRun:
    cases = [case for case in load_cases() if not only or case["id"] in only]
    rows = [score_case(case, run_case(case)) for case in cases]
    llm, embeddings = describe_backend(), describe_embeddings()
    scored_citations = [row for row in rows if row["verified"] is not None]
    scored_conflicts = [row for row in rows if row["conflict_ok"] is not None]
    metrics = {
        "cases": len(rows),
        "intent_correct": sum(row["intent_ok"] for row in rows),
        "answer_correct": sum(row["answer_ok"] for row in rows),
        "citation_cases": len(scored_citations),
        "citations_verified": sum(bool(row["verified"]) for row in scored_citations),
        "conflict_cases": len(scored_conflicts),
        "conflict_surfaced": sum(bool(row["conflict_ok"]) for row in scored_conflicts),
        "loops_total": sum(row["loops"] for row in rows),
        "rows": rows,
    }
    report = _report(rows, metrics, llm, embeddings)
    results_dir = Path(settings.EVAL_RESULTS_DIR)
    results_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    (results_dir / f"agent_{stamp}.md").write_text(report, encoding="utf-8")
    return EvalRun.objects.create(
        kind="agent", llm_provider=llm["provider"], llm_model=llm["model"],
        embeddings_provider=embeddings["provider"], embeddings_model=embeddings["model"],
        config={"cases": [case["id"] for case in cases], "retrieval_mode": settings.AGENT_RETRIEVAL_MODE,
                "max_loops": settings.AGENT_MAX_LOOPS, "approval": AUTO_APPROVE_NOTE},
        metrics=metrics, report_md=report,
    )


def _mark(value) -> str:
    if value is None:
        return "n/a"
    return "yes" if value else "**no**"


def table(rows: list[dict]) -> list[str]:
    lines = [
        "| case | intent correct | answer correct | citations verified | conflict surfaced | loops used | status |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in rows:
        if row["conflict_expected"] is None:
            conflict = f"{'yes' if row['conflict_surfaced'] else 'no'} (not scored)"
        else:
            conflict = f"{_mark(row['conflict_ok'])} (surfaced: {'yes' if row['conflict_surfaced'] else 'no'}, " \
                       f"expected: {'yes' if row['conflict_expected'] else 'no'})"
        intent = f"{_mark(row['intent_ok'])} ({row['intent'] or '-'})"
        lines.append(f"| {row['id']} | {intent} | {_mark(row['answer_ok'])} | {_mark(row['verified'])} | {conflict} "
                     f"| {row['loops']} | {row['status']} |")
    return lines


def _report(rows, metrics, llm, embeddings) -> str:
    lines = [
        "# Agent eval: compliance assistant",
        "",
        f"- LLM: `{llm['provider']}` / `{llm['model']}`",
        f"- Embeddings: `{embeddings['provider']}` / `{embeddings['model']}`",
        f"- Retrieval mode: `{settings.AGENT_RETRIEVAL_MODE}` · max loops {settings.AGENT_MAX_LOOPS}",
        f"- Approval: every paused run was approved by the command ({AUTO_APPROVE_NOTE}).",
        f"- Totals: intent {metrics['intent_correct']}/{metrics['cases']}, answer {metrics['answer_correct']}/{metrics['cases']}, "
        f"citations verified {metrics['citations_verified']}/{metrics['citation_cases']}, "
        f"conflict check {metrics['conflict_surfaced']}/{metrics['conflict_cases']}, loops {metrics['loops_total']}",
        "",
        *table(rows),
        "",
        "## Answers",
        "",
    ]
    for row in rows:
        lines.append(f"**{row['id']}** (run {row['run']}): {' '.join(row['answer'].split())}")
        if row["warning"]:
            lines.append(f"  - warning: {row['warning']}")
        for problem in row["problems"]:
            lines.append(f"  - last citation check: {problem}")
        if row["error"]:
            lines.append(f"  - error: {row['error']}")
        lines.append("")
    if llm["provider"] == "mock":
        lines += ["These numbers come from the offline mock, which routes and drafts with keyword patterns.",
                  "They test the graph wiring, not a model.", ""]
    return "\n".join(lines)
