"""Knowledge-base loader and retriever for the RAG layer.

Markdown files under data/kb are split on `##` headings into passages. Each
passage keeps its source file, doc_id and heading so every generated answer can
be traced back to a verified document - that traceability is the whole point of
grounding the LLM layer rather than letting it answer freely.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from threading import RLock

from . import chunker, loaders
from .textmodel import TfidfIndex

KB_DIR = Path(__file__).resolve().parent.parent / "data" / "kb"

# Retrieval cut-offs, expressed on the coverage scale (see textmodel.coverages):
# "what share of this question does the passage actually address?". Below the
# floor we return nothing, which routes the turn to escalation rather than
# letting the LLM answer without evidence. The relative cut-off then drops
# weaker passages once a strong one exists, so the model isn't handed noise
# alongside the right source.
#
# Measured on the demo corpus: in-scope questions score 0.29-1.00 against their
# correct passage, out-of-scope questions top out at 0.23. The floor sits in
# that gap. Re-measure it whenever the knowledge base changes materially.
from . import policy as _policy

MIN_RELEVANCE = _policy.current.min_relevance
RELATIVE_CUTOFF = 0.60


@dataclass
class Passage:
    passage_id: str
    doc_id: str
    source: str
    title: str
    heading: str                       # leaf heading - what gets cited
    text: str
    heading_path: list[str] = field(default_factory=list)
    chunk_index: int = 0

    @property
    def citation(self) -> str:
        return f"{self.doc_id} · {self.heading}"

    @property
    def breadcrumb(self) -> str:
        """Full heading path. In a deep document the leaf alone is ambiguous -
        three sections can all be called "Fees" under different parents."""
        return " › ".join(self.heading_path) if self.heading_path else self.heading


# Reciprocal Rank Fusion constant. 60 is the value from the original paper and
# is not sensitive here: it damps the difference between adjacent ranks so that
# agreement across rankers matters more than any single ranker's confidence.
RRF_K = 60


def reciprocal_rank_fusion(rankings: dict[str, list[float]]) -> list[float]:
    """Combine several scorings of the same corpus into one ranking.

    Fusing on *rank* rather than score is what makes this work: coverage runs
    0-1, cosine 0-0.5, and BM25 unbounded, so no weighted sum of the raw values
    is meaningful without per-corpus calibration that would drift the moment a
    document is uploaded. Ranks are unit-free, so the fusion needs no tuning and
    survives a changing corpus.
    """
    length = len(next(iter(rankings.values()), []))
    fused = [0.0] * length

    for scores in rankings.values():
        order = sorted(range(length), key=lambda i: scores[i], reverse=True)
        for rank, index in enumerate(order, start=1):
            # A zero score is an absence of evidence, not weak evidence; letting
            # it contribute would give every unmatched passage a rank bonus.
            if scores[index] > 0.0:
                fused[index] += 1.0 / (RRF_K + rank)
    return fused


@dataclass
class RetrievedPassage:
    passage: Passage
    score: float              # coverage - the relevance gate
    similarity: float = 0.0   # cosine
    bm25: float = 0.0         # Okapi BM25
    fusion: float = 0.0       # RRF of the three - decides the ordering
    rerank: float | None = None   # LLM relevance score, when reranking is on


def parse_text(raw: str, source: str, stem: str) -> list[Passage]:
    """Turn a document's text into retrievable passages."""
    title = chunker.extract_title(raw) or stem
    doc_id = chunker.extract_doc_id(raw) or stem.upper()

    return [
        Passage(
            passage_id=f"{doc_id}#{chunk.index + 1}",
            doc_id=doc_id,
            source=source,
            title=title,
            heading=chunk.heading,
            text=chunk.text,
            heading_path=chunk.heading_path,
            chunk_index=chunk.index,
        )
        for chunk in chunker.chunk_document(chunker.strip_metadata(raw))
    ]


def _parse_document(path: Path) -> list[Passage]:
    return parse_text(loaders.read_document(path), source=path.name, stem=path.stem)


class KnowledgeBase:
    """The corpus behind the RAG layer, rebuildable at runtime.

    Documents can be added and removed while the server is running (see
    `app/kbstore.py`), so the index is rebuilt behind a lock rather than being
    constructed once at import.
    """

    def __init__(self, kb_dir: Path | None = None) -> None:
        self.kb_dir = kb_dir or KB_DIR
        self.kb_dir.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self.passages: list[Passage] = []
        self._index: TfidfIndex | None = None
        self.reload()

    def reload(self) -> None:
        """Re-read every document and rebuild the index.

        An empty corpus is a legitimate state - the operator may have deleted
        every document from the console. Retrieval then returns nothing, which
        routes every knowledge question to escalation. That is the correct
        behaviour: no verified source means no grounded answer.
        """
        with self._lock:
            passages: list[Passage] = []
            for path in self.document_paths():
                try:
                    passages.extend(_parse_document(path))
                except loaders.UnsupportedDocument:
                    # A file that cannot be read must not take the whole corpus
                    # down on reload - skip it and keep serving the rest.
                    continue
            self.passages = passages
            # Index heading + body: headings carry a lot of signal in a small corpus.
            self._index = TfidfIndex(
                [f"{p.title} {p.heading} {p.text}" for p in passages]
            ) if passages else None

    def search(self, query: str, top_k: int = 3,
               gate: float | None = None) -> list[RetrievedPassage]:
        """Retrieve passages for `query`.

        `gate` overrides the rejection threshold. The reranking path lowers it
        deliberately: a semantic judge downstream can throw out what lexical
        matching let through, so stage one is free to favour recall. With no
        judge downstream the conservative default stands, because then this
        threshold is the only thing between an off-topic passage and a
        grounded-looking answer.
        """
        with self._lock:
            return self._search_locked(query, top_k, gate)

    def _search_locked(self, query: str, top_k: int,
                       gate: float | None) -> list[RetrievedPassage]:
        if self._index is None:
            return []

        coverages = self._index.coverages(query)
        similarities = self._index.similarities(query)
        bm25 = self._index.bm25_scores(query)

        # Rejection is a coverage decision, tuned separately from ranking.
        # Keeping those two jobs apart is deliberate: a ranker always produces
        # an order, even over pure noise, so it can never be what decides
        # whether there is anything worth returning.
        best_coverage = max(coverages, default=0.0)
        threshold = _policy.current.min_relevance if gate is None else gate
        if best_coverage < threshold:
            return []

        fused = reciprocal_rank_fusion({
            "coverage": coverages, "cosine": similarities, "bm25": bm25,
        })

        # Ordered by coverage, not by the fusion score. Fusion was measured
        # against this baseline on the labelled set and did not beat it - see
        # README § Retrieval quality. The fused value is still computed and
        # carried through, because it is worth showing in the inspector and is
        # what a larger corpus would likely need; it just does not decide the
        # order today, on the evidence available.
        ranked = sorted(
            (
                RetrievedPassage(
                    passage=passage,
                    score=round(coverages[i], 3),
                    similarity=round(similarities[i], 3),
                    bm25=round(bm25[i], 3),
                    fusion=round(fused[i], 5),
                )
                for i, passage in enumerate(self.passages)
            ),
            key=lambda r: (r.score, r.similarity),
            reverse=True,
        )

        floor = best_coverage * RELATIVE_CUTOFF
        return [r for r in ranked[:top_k] if r.score >= floor]

    def document_paths(self) -> list[Path]:
        """Every readable document in the knowledge-base directory, sorted."""
        return sorted(
            p for p in self.kb_dir.iterdir()
            if p.is_file() and p.suffix.lower() in loaders.SUPPORTED
        )

    @property
    def stats(self) -> dict:
        with self._lock:
            documents = {p.source for p in self.passages}
            return {"documents": len(documents), "passages": len(self.passages)}
