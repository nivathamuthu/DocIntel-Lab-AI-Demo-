"""Chunk pairs for multi-hop questions: two chunks from different documents that the Module 4 graph links."""
from collections import Counter, defaultdict
from itertools import combinations

from apps.kg.models import KGExtraction
from apps.kg.services.normalize import entity_key, has_digit


def related_pairs(chunks, limit: int) -> list[tuple]:
    """(chunk_a, chunk_b, shared entity names), strongest links first, each chunk in at most two pairs.

    Two chunks are linked when their KG extractions name the same identifier-bearing entity
    (a part number, directive or interval) after entity resolution's normalization.
    """
    by_id = {chunk.id: chunk for chunk in chunks}
    chunks_for_key: dict[str, set[int]] = defaultdict(set)
    label_for_key: dict[str, str] = {}
    for extraction in KGExtraction.objects.filter(chunk_id__in=list(by_id)):
        for entity in extraction.entities:
            for name in [entity.get("name", "")] + list(entity.get("aliases") or []):
                key = entity_key(name)
                if key and has_digit(key):
                    chunks_for_key[key].add(extraction.chunk_id)
                    label_for_key.setdefault(key, entity.get("name", name))
    shared: dict[tuple[int, int], set[str]] = defaultdict(set)
    for key, ids in chunks_for_key.items():
        for first, second in combinations(sorted(ids), 2):
            if by_id[first].document_id != by_id[second].document_id:
                shared[(first, second)].add(label_for_key[key])
    ranked = sorted(shared.items(), key=lambda item: (-len(item[1]), item[0]))
    picked, uses = [], Counter()
    for (first, second), labels in ranked:
        if uses[first] >= 2 or uses[second] >= 2:
            continue
        picked.append((by_id[first], by_id[second], ", ".join(sorted(labels)[:3])))
        uses[first] += 1
        uses[second] += 1
        if len(picked) >= limit:
            break
    return picked
