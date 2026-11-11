"""Highlighting and export for the Q&A page."""
import csv
import io
import json
import re

from apps.qa_gen.models import QAPair


def segments(text: str, quotes: list[str]) -> list[dict]:
    """Split `text` into plain and marked parts, marking every quote (matched across any whitespace)."""
    spans = []
    for quote in quotes:
        words = (quote or "").split()
        if not words:
            continue
        pattern = r"\s+".join(re.escape(word) for word in words)
        spans.extend(match.span() for match in re.finditer(pattern, text))
    merged: list[list[int]] = []
    for start, end in sorted(spans):
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])
    parts, position = [], 0
    for start, end in merged:
        if start > position:
            parts.append({"text": text[position:start], "mark": False})
        parts.append({"text": text[start:end], "mark": True})
        position = end
    if position < len(text):
        parts.append({"text": text[position:], "mark": False})
    return parts


def approved_rows() -> list[dict]:
    rows = []
    pairs = (QAPair.objects.filter(verdict=QAPair.Verdict.KEPT, review=QAPair.Review.APPROVED)
             .prefetch_related("chunks__document"))
    for pair in pairs:
        rows.append({
            "id": pair.id,
            "qtype": pair.qtype,
            "question": pair.question,
            "answer": pair.answer,
            "supporting_quotes": pair.supporting_quotes,
            "sources": [f"{chunk.document.title}" + (f", p.{chunk.page_start}" if chunk.page_start else "")
                        for chunk in pair.chunks.all()],
            "groundedness": pair.groundedness,
            "judge_reason": pair.judge_reason,
            "prompt_version": pair.prompt_version,
            "llm": f"{pair.llm_provider}/{pair.llm_model}",
        })
    return rows


def to_json(rows: list[dict]) -> str:
    return json.dumps(rows, indent=2, ensure_ascii=False)


def to_csv(rows: list[dict]) -> str:
    buffer = io.StringIO()
    fields = ["id", "qtype", "question", "answer", "supporting_quotes", "sources", "groundedness",
              "judge_reason", "prompt_version", "llm"]
    writer = csv.DictWriter(buffer, fieldnames=fields)
    writer.writeheader()
    for row in rows:
        writer.writerow({**row, "supporting_quotes": " | ".join(row["supporting_quotes"]),
                         "sources": " | ".join(row["sources"])})
    return buffer.getvalue()
