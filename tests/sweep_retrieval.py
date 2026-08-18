"""Compare retrieval ranking strategies. Run: python tests/sweep_retrieval.py

Scratch harness, not part of the app. It reimplements the candidate ranking
against the live index so alternatives can be measured before any of them is
written into `retriever.py` - the first hybrid attempt scored *worse* than the
baseline, which is exactly the kind of thing that gets shipped when a change is
reasoned about instead of measured.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.retriever import (
    MIN_RELEVANCE,
    RELATIVE_CUTOFF,
    KnowledgeBase,
    reciprocal_rank_fusion,
)
from eval_retrieval import IN_SCOPE, OUT_OF_SCOPE


@dataclass
class Signals:
    coverage: list[float]
    cosine: list[float]
    bm25: list[float]


def signals_for(kb: KnowledgeBase, query: str) -> Signals:
    return Signals(
        coverage=kb._index.coverages(query),
        cosine=kb._index.similarities(query),
        bm25=kb._index.bm25_scores(query),
    )


# Each strategy takes the signals and returns indices, best first.
# The gate (max coverage >= MIN_RELEVANCE) is applied outside, identically for
# all of them, so these are compared purely on ordering and filtering.

def s_baseline(sig: Signals, top_k: int) -> list[int]:
    """Coverage order, coverage-relative floor. What shipped originally."""
    order = sorted(range(len(sig.coverage)),
                   key=lambda i: (sig.coverage[i], sig.cosine[i]), reverse=True)
    floor = max(sig.coverage) * RELATIVE_CUTOFF
    return [i for i in order[:top_k] if sig.coverage[i] >= floor]


def s_rrf_coverage_floor(sig: Signals, top_k: int) -> list[int]:
    """RRF order, coverage-relative floor. The first attempt."""
    fused = reciprocal_rank_fusion(
        {"c": sig.coverage, "s": sig.cosine, "b": sig.bm25})
    order = sorted(range(len(fused)), key=lambda i: (fused[i], sig.coverage[i]),
                   reverse=True)
    floor = max(sig.coverage) * RELATIVE_CUTOFF
    return [i for i in order[:top_k] if sig.coverage[i] >= floor]


def s_rrf_only(sig: Signals, top_k: int) -> list[int]:
    """RRF order, no second filter - trust the fusion."""
    fused = reciprocal_rank_fusion(
        {"c": sig.coverage, "s": sig.cosine, "b": sig.bm25})
    order = sorted(range(len(fused)), key=lambda i: (fused[i], sig.coverage[i]),
                   reverse=True)
    return [i for i in order[:top_k] if fused[i] > 0.0]


def s_rrf_no_cosine(sig: Signals, top_k: int) -> list[int]:
    """Cosine and BM25 are both lexical; maybe cosine only adds correlated noise."""
    fused = reciprocal_rank_fusion({"c": sig.coverage, "b": sig.bm25})
    order = sorted(range(len(fused)), key=lambda i: (fused[i], sig.coverage[i]),
                   reverse=True)
    return [i for i in order[:top_k] if fused[i] > 0.0]


def s_rrf_soft_floor(sig: Signals, top_k: int) -> list[int]:
    """RRF order with a much looser coverage floor - noise guard, not a ranker."""
    fused = reciprocal_rank_fusion(
        {"c": sig.coverage, "s": sig.cosine, "b": sig.bm25})
    order = sorted(range(len(fused)), key=lambda i: (fused[i], sig.coverage[i]),
                   reverse=True)
    floor = max(sig.coverage) * 0.25
    return [i for i in order[:top_k] if sig.coverage[i] >= floor and fused[i] > 0.0]


def s_bm25_only(sig: Signals, top_k: int) -> list[int]:
    order = sorted(range(len(sig.bm25)), key=lambda i: sig.bm25[i], reverse=True)
    return [i for i in order[:top_k] if sig.bm25[i] > 0.0]


STRATEGIES = {
    "baseline (coverage + cov floor)": s_baseline,
    "RRF3 + cov floor": s_rrf_coverage_floor,
    "RRF3 only": s_rrf_only,
    "RRF3 + soft floor": s_rrf_soft_floor,
    "RRF2 (cov+bm25) only": s_rrf_no_cosine,
    "BM25 only": s_bm25_only,
}


def run(kb: KnowledgeBase, strategy, top_k: int = 3) -> dict:
    passages = kb.passages
    hits1 = hitsk = 0
    rr = 0.0

    for question, expected in IN_SCOPE:
        sig = signals_for(kb, question)
        indices = ([] if max(sig.coverage, default=0.0) < MIN_RELEVANCE
                   else strategy(sig, top_k))
        headings = [passages[i].heading for i in indices]
        rank = next((r for r, h in enumerate(headings, 1)
                     if h.lower().startswith(expected.lower()[:24])), None)
        if rank == 1:
            hits1 += 1
        if rank:
            hitsk += 1
            rr += 1.0 / rank

    rejected = 0
    for question in OUT_OF_SCOPE:
        sig = signals_for(kb, question)
        indices = ([] if max(sig.coverage, default=0.0) < MIN_RELEVANCE
                   else strategy(sig, top_k))
        if not indices:
            rejected += 1

    n = len(IN_SCOPE)
    return {
        "p1": hits1 / n,
        "recall": hitsk / n,
        "mrr": rr / n,
        "reject": rejected / len(OUT_OF_SCOPE),
    }


def main() -> int:
    kb = KnowledgeBase()
    print(f"Corpus: {kb.stats['passages']} passages / {kb.stats['documents']} docs")
    print(f"Labelled: {len(IN_SCOPE)} in-scope, {len(OUT_OF_SCOPE)} out-of-scope\n")

    print(f"{'strategy':34} {'P@1':>7} {'R@3':>7} {'MRR':>7} {'reject':>8}")
    print("-" * 66)
    for name, strategy in STRATEGIES.items():
        r = run(kb, strategy)
        print(f"{name:34} {r['p1']:6.1%} {r['recall']:6.1%} "
              f"{r['mrr']:7.3f} {r['reject']:7.1%}")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
