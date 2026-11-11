"""Checks done in code, not by the model: verbatim quotes and the groundedness threshold."""
from apps.qa_gen.models import QAPair
from apps.rules.services.text import quote_in_chunk


def check_quotes(qtype: str, quotes: list[str], chunk_texts: list[str]) -> str:
    """'' when every quote is verbatim in a source chunk, otherwise the problem in plain words.

    A multi-hop pair must quote each of its linked chunks at least once.
    """
    quotes = [quote for quote in quotes if quote and quote.strip()]
    if not quotes:
        return "No supporting quote was given."
    found_in: list[set[int]] = []
    for quote in quotes:
        hits = {index for index, text in enumerate(chunk_texts) if quote_in_chunk(quote, text)}
        if not hits:
            return f'Quote not found in the source: "{quote[:160]}"'
        found_in.append(hits)
    if qtype == QAPair.QType.MULTI_HOP and len(chunk_texts) > 1:
        covered = set().union(*found_in)
        if len(quotes) < 2 or not all(index in covered for index in range(len(chunk_texts))):
            return "A multi-hop pair needs one quote from each linked chunk."
    return ""


def groundedness_verdict(score: float, threshold: float) -> str:
    return QAPair.Verdict.KEPT if score >= threshold else QAPair.Verdict.DROPPED_GROUNDEDNESS
