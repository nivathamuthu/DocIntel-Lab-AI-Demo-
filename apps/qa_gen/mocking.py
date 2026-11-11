"""Offline stand-ins for the Q&A prompts (LLM_PROVIDER=mock). They exist for the test suite.

The generator copies chunk sentences; the judges count shared words. v1 ("write some
questions") paraphrases its quote, so the quote check drops it, which mirrors the
naive prompt's failure mode. None of this parses text written by a model.
"""
import re

from apps.qa_gen.schemas import UNANSWERABLE, AnswerJudgment, Groundedness, QASet
from apps.rules.services.text import content_words, identifiers
from core.mock_llm import register

_ENGINEERED = "ask only what the text answers"


def _block(prompt: str, start: str, end: str | None) -> str:
    begin = prompt.rfind(start)
    if begin < 0:
        return ""
    begin += len(start)
    finish = prompt.find(end, begin) if end else -1
    return prompt[begin:finish if finish >= 0 else None].strip()


def _sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+|\n+", text)
    return [part.strip() for part in parts if len(part.split()) >= 5]


def _topic(sentence: str) -> str:
    found = sorted(identifiers(sentence))
    if found:
        return found[0]
    words = [word for word in re.findall(r"[A-Za-z][A-Za-z-]+", sentence) if len(word) > 3]
    return " ".join(words[:3]) or "this item"


def _factual(sentence: str, engineered: bool) -> dict:
    quote = sentence if engineered else sentence.replace("flight hours", "FH").replace("the ", "a ", 1)
    return {"qtype": "factual", "question": f"What do the documents state about {_topic(sentence)}?",
            "answer": sentence, "supporting_quotes": [quote]}


@register(QASet)
def mock_generate(prompt: str) -> dict:
    engineered = _ENGINEERED in prompt.lower()
    if "CHUNK A:" in prompt:
        first = _sentences(_block(prompt, "CHUNK A:", "CHUNK B:"))
        second = _sentences(_block(prompt, "CHUNK B:", None))
        if not first or not second:
            return {"items": []}
        shared = _block(prompt, "linked through:", ".").split(",")[0].strip() or _topic(first[0])
        return {"items": [{
            "qtype": "multi_hop",
            "question": f"How do the two documents together describe {shared}?",
            "answer": f"{first[0]} {second[0]}",
            "supporting_quotes": [first[0], second[0]],
        }]}
    sentences = _sentences(_block(prompt, "CHUNK:", None))
    if not sentences:
        return {"items": []}
    items = [_factual(sentence, engineered) for sentence in sentences[:2]]
    items.append({
        "qtype": "unanswerable",
        "question": f"What torque value in newton-metres is specified for {_topic(sentences[0])}?",
        "answer": UNANSWERABLE if engineered else "It is not given.",
        "supporting_quotes": [sentences[0]],
    })
    return {"items": items}


def _field(prompt: str, name: str) -> str:
    match = re.search(rf"^{name}: (.*)$", prompt, re.MULTILINE)
    return match.group(1).strip() if match else ""


@register(Groundedness)
def mock_groundedness(prompt: str) -> dict:
    qtype = _field(prompt, "QTYPE")
    question = _field(prompt, "QUESTION")
    answer = _field(prompt, "ANSWER")
    source = _block(prompt, "SOURCE:", None)
    source_words = content_words(source)
    if qtype == "unanswerable":
        asked = content_words(question) - source_words
        score = 0.9 if asked else 0.2
        return {"score": score, "reason": "The source does not give the asked detail." if asked
                else "The source answers this question."}
    words = content_words(answer)
    share = len(words & source_words) / len(words) if words else 0.0
    return {"score": round(share, 2), "reason": f"{round(share * 100)}% of the answer's words are in the source."}


@register(AnswerJudgment)
def mock_answer_judgment(prompt: str) -> dict:
    reference = content_words(_field(prompt, "REFERENCE"))
    answer = content_words(_field(prompt, "SYSTEM ANSWER"))
    share = len(reference & answer) / len(reference) if reference else 0.0
    return {"correct": share >= 0.6, "reason": f"{round(share * 100)}% of the reference's key words are in the answer."}
