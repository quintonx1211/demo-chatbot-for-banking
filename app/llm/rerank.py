"""LLM reranking — the semantic stage of the retrieval pipeline.

Lexical retrieval on this corpus has a measurable ceiling. Questions like "what
happens when a salary payment bounces" fail against a document that says
"returned payments" and "payroll", and no amount of BM25 or rank fusion fixes
that: the words genuinely do not overlap. Coverage and BM25 scores for those
questions sit inside the range occupied by out-of-scope questions, so no
threshold separates them either.

A model reading the candidates does not have that problem, which is why this
stage is worth an extra call. It does two jobs:

  1. Reorders candidates by actual relevance.
  2. Acts as the rejection gate. When it is enabled, stage one runs with a
     deliberately loose lexical threshold and this stage decides what was
     really relevant — including deciding that nothing was.

Off by default (LLM_RERANK=1 to enable) because it adds a round trip to every
knowledge question, and the demo's latency budget matters.
"""

from __future__ import annotations

import json
import os
import re

from ..retriever import RetrievedPassage
from .base import LLMRequest

# Candidates handed to the judge. Measured stage-one recall@10 on the labelled
# set is 90.2%, and every extra candidate is tokens spent on a passage that is
# very unlikely to be the answer.
CANDIDATE_POOL = 10

# Lexical threshold used when this stage will run. Well below the standalone
# gate (0.28) because the judge below is what actually decides relevance.
RECALL_GATE = 0.10

# A candidate must score at least this to be returned. If nothing clears it the
# turn escalates, exactly as an empty lexical retrieval would.
MIN_SCORE = 5

RERANK_SYSTEM_PROMPT = """You score how well each passage answers a customer's \
question for a retail bank's assistant.

For every passage you are given, output a score from 0 to 10:
  0-2   unrelated to the question
  3-4   same general topic, does not answer the question
  5-7   contains part of the answer
  8-10  directly and fully answers the question

Judge only whether the passage answers THIS question. Ignore how well written \
it is, and do not reward a passage for being about banking. A passage on a \
neighbouring topic — a different fee, a different product, a different process \
— scores low even though it looks relevant.

Return only a JSON array of objects, one per passage, in the order given:
[{"i": 1, "score": 8}, {"i": 2, "score": 0}]
No prose, no explanation, no markdown fence."""


def enabled() -> bool:
    return bool(os.environ.get("LLM_RERANK"))


def _build_request(query: str, candidates: list[RetrievedPassage]) -> LLMRequest:
    blocks = []
    for position, item in enumerate(candidates, start=1):
        # Truncated: the judge needs enough to tell what the passage covers, not
        # the whole passage, and this runs over ten of them per question.
        text = item.passage.text[:600]
        blocks.append(
            f"[{position}] ({item.passage.breadcrumb})\n{text}"
        )
    return LLMRequest(
        system=RERANK_SYSTEM_PROMPT,
        user=(
            f"Customer question: {query}\n\n"
            f"Passages:\n\n" + "\n\n".join(blocks) +
            f"\n\nScore all {len(candidates)} passages."
        ),
        max_tokens=1200,
    )


def _parse_scores(text: str, expected: int) -> dict[int, int] | None:
    """Pull the score array out of the model's reply.

    Tolerant of a markdown fence or a sentence of preamble, because a model
    that ignores "no prose" should degrade to unreranked results rather than
    take the turn down. Returns None when nothing usable came back.
    """
    match = re.search(r"\[.*]", text, re.DOTALL)
    if not match:
        return None
    try:
        parsed = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(parsed, list):
        return None

    scores: dict[int, int] = {}
    for entry in parsed:
        if not isinstance(entry, dict):
            continue
        try:
            index = int(entry.get("i"))
            score = float(entry.get("score"))
        except (TypeError, ValueError):
            continue
        if 1 <= index <= expected:
            scores[index] = max(0, min(10, int(round(score))))
    return scores or None


def rerank(query: str, candidates: list[RetrievedPassage],
           top_k: int = 3) -> tuple[list[RetrievedPassage], str]:
    """Reorder and filter candidates. Returns (results, note-for-the-audit-trail).

    Any failure — no provider, a refusal, unparseable output — falls back to the
    lexical order rather than dropping the turn. The note records which
    happened, so the audit trail never implies a rerank that did not occur.
    """
    from . import active_provider, _effort  # local: avoids an import cycle

    if not candidates:
        return [], "rerank:no-candidates"

    provider = active_provider()
    if provider is None:
        return candidates[:top_k], "rerank:skipped-no-provider"

    result = provider.complete(_build_request(query, candidates), _effort())
    if not result.text.strip():
        return candidates[:top_k], f"rerank:failed({result.error or 'empty'})"

    scores = _parse_scores(result.text, len(candidates))
    if scores is None:
        return candidates[:top_k], "rerank:unparseable"

    for position, item in enumerate(candidates, start=1):
        item.rerank = scores.get(position, 0)

    kept = [c for c in candidates if (c.rerank or 0) >= MIN_SCORE]
    if not kept:
        # The judge saw the candidates and found none of them relevant. That is
        # a real answer, not a failure: the turn escalates.
        return [], f"rerank:rejected-all(best={max(scores.values(), default=0)})"

    kept.sort(key=lambda c: (c.rerank or 0, c.score), reverse=True)
    return kept[:top_k], f"rerank:ok({len(kept)}/{len(candidates)} kept)"
