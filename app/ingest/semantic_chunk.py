"""Chunking that cuts at topic boundaries instead of a fixed character count.

`app/chunker.py`'s packing fills a chunk up to `TARGET_CHARS` and cuts there,
whatever is on either side of the cut. That is fine when a section is short
enough to stay one chunk, which is most of this corpus - but a long section
gets cut wherever the character budget runs out, which is frequently mid-topic.

This reuses everything about `chunker.py` that is not the packing decision
itself: heading-path tracking (`_sections`), paragraph splitting
(`_paragraphs`), table detection and safe table splitting (`_is_table`,
`_split_table`), and the sentence-level fallback for a single oversized unit
(`_split_oversized`). Only the question "where do I cut?" is answered
differently: instead of "as soon as the budget is full", it's "at the
weakest topical link between two units, once the budget is at least
half-full" - using the TF-IDF cosine similarity the retriever already
computes elsewhere in this app, not a new dependency.

Not wired into `retriever.py` by default. See `tests/eval_chunking.py` for
the measurement this is supposed to earn its way in with.
"""

from __future__ import annotations

from dataclasses import dataclass

from .. import chunker
from ..textmodel import TfidfIndex, tokenize

TARGET_CHARS = chunker.TARGET_CHARS
MAX_CHARS = chunker.MAX_CHARS
MIN_CHARS = chunker.MIN_CHARS
OVERLAP_CHARS = chunker.OVERLAP_CHARS

# Don't even consider cutting on a topic boundary before the chunk has this
# fraction of the target size - a 200-character chunk that happens to sit
# before a mild topic shift is worse than a 900-character one that doesn't
# cut at the perfect seam.
_MIN_FRACTION_BEFORE_CUT = 0.55


def _adjacent_similarities(units: list[str]) -> list[float]:
    """Cosine similarity between unit i and unit i+1, for every adjacent pair.

    One TF-IDF index fit over all units in the section - consistent
    vocabulary weighting across the whole section, not one query at a time
    against a moving corpus.
    """
    if len(units) < 2:
        return []
    index = TfidfIndex(units)
    sims: list[float] = []
    for i in range(len(units) - 1):
        if not tokenize(units[i]) or not tokenize(units[i + 1]):
            # A unit with no scorable tokens (a lone table row, a very short
            # fragment) has no similarity signal - treat the boundary as
            # neutral (0.5) rather than as a confident topic shift, so it
            # doesn't force a cut purely because TF-IDF has nothing to say.
            sims.append(0.5)
            continue
        row = index.similarities(units[i])
        sims.append(row[i + 1])
    return sims


def _carry_overlap(finished_chunk: str) -> str:
    """Same seam logic as `chunker._pack` - cut the overlap at a line
    boundary, and drop it entirely if that would open on a bare table row."""
    tail = finished_chunk[-OVERLAP_CHARS:]
    newline = tail.find("\n")
    if newline != -1:
        tail = tail[newline + 1:]
    else:
        space = tail.find(" ")
        tail = tail[space + 1:] if space != -1 else ""
    if tail.lstrip().startswith("|") and not tail.rstrip().endswith("|"):
        tail = ""
    return tail


def _semantic_pack(paragraphs: list[str]) -> list[str]:
    """Group paragraphs into chunks, cutting at the weakest topical seam
    near the target size rather than exactly at it."""
    units: list[str] = []
    for paragraph in paragraphs:
        units.extend(chunker._split_oversized(paragraph))
    if not units:
        return []
    if len(units) == 1:
        return units

    similarities = _adjacent_similarities(units)

    chunks: list[str] = []
    current = ""
    for i, unit in enumerate(units):
        candidate = f"{current}\n\n{unit}".strip() if current else unit

        over_ceiling = len(candidate) > MAX_CHARS
        big_enough = len(candidate) >= TARGET_CHARS * _MIN_FRACTION_BEFORE_CUT
        # The seam being cut is the one *after* this unit, i.e. between unit i
        # and unit i+1 - so it's only a candidate cut point once this unit is
        # actually in the chunk.
        weak_seam = (i < len(similarities) and similarities[i] < 0.15
                    and len(candidate) >= TARGET_CHARS * _MIN_FRACTION_BEFORE_CUT)
        at_target = len(candidate) >= TARGET_CHARS

        if current and (over_ceiling or (big_enough and (weak_seam or at_target))):
            # over_ceiling means `unit` itself doesn't fit - close the current
            # chunk *before* adding it, then start a fresh one with `unit`.
            if over_ceiling:
                chunks.append(current)
                tail = _carry_overlap(current)
                current = f"{tail}\n\n{unit}".strip() if tail.strip() else unit
            else:
                chunks.append(candidate)
                current = ""
        else:
            current = candidate

    if current:
        chunks.append(current)

    if len(chunks) > 1 and len(chunks[-1]) < MIN_CHARS:
        chunks[-2] = f"{chunks[-2]}\n\n{chunks[-1]}"
        chunks.pop()

    return chunks


def semantic_chunk_document(text: str) -> list[chunker.Chunk]:
    """Same public shape as `chunker.chunk_document` - a drop-in alternative
    strategy, not a replacement API."""
    normalised = chunker._normalise(text)
    if not normalised:
        return []

    result: list[chunker.Chunk] = []
    for path, body in chunker._sections(normalised):
        leaf = path[-1] if path else "Introduction"
        for piece in _semantic_pack(chunker._paragraphs(body)):
            result.append(chunker.Chunk(
                text=piece,
                heading=leaf,
                heading_path=list(path),
                index=len(result),
            ))
    return result
