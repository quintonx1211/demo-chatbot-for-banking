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

from . import policy as _policy

MIN_RELEVANCE = _policy.current.min_relevance
RELATIVE_CUTOFF = 0.60


@dataclass
class Passage:
    passage_id: str
    doc_id: str
    source: str
    title: str
    heading: str
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
            if scores[index] > 0.0:
                fused[index] += 1.0 / (RRF_K + rank)
    return fused


_ISSUER_ALIASES: list[tuple[str, tuple[str, ...]]] = [
    ("KB-MKT-TCB", ("techcombank", "tcb")),
    ("KB-MKT-VCB", ("vietcombank", "vcb")),
    ("KB-MKT-VPB", ("vpbank", "vp bank", "vpb")),
    ("KB-MKT-MBB", ("mbbank", "mb bank", "mbb", "quân đội")),
]

MARKET_DOC_PREFIX = "KB-MKT-"

MARKET_DOC_DEMOTION = 0.6


def issuers_mentioned(query: str) -> set[str]:
    """doc_ids of any issuers the query names explicitly."""
    text = f" {query.lower()} "
    found = set()
    for doc_id, aliases in _ISSUER_ALIASES:
        for alias in aliases:
            if f" {alias} " in text or f" {alias}?" in text or f" {alias}," in text:
                found.add(doc_id)
                break
            if alias in ("mbb", "vcb", "tcb", "vpb") and alias in text:
                found.add(doc_id)
                break
            if len(alias) > 4 and alias in text:
                found.add(doc_id)
                break
    return found


@dataclass
class RetrievedPassage:
    passage: Passage
    score: float
    similarity: float = 0.0
    bm25: float = 0.0
    fusion: float = 0.0
    rerank: float | None = None


def parse_text(raw: str, source: str, stem: str, chunk_fn=None) -> list[Passage]:
    """Turn a document's text into retrievable passages.

    `chunk_fn` defaults to `chunker.chunk_document` - the strategy every
    passage in this app has always been scored and cited against. It exists
    as a parameter so `tests/eval_chunking.py` can build a second index with
    `app.ingest.semantic_chunk.semantic_chunk_document` on the same source
    files and compare, without duplicating anything else in this module.
    """
    chunk_fn = chunk_fn or chunker.chunk_document
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
        for chunk in chunk_fn(chunker.strip_metadata(raw))
    ]


def _parse_document(path: Path, chunk_fn=None) -> list[Passage]:
    if path.suffix.lower() == ".pdf":
        from .ingest import clean as _ingest_clean
        from .ingest import extract as _ingest_extract

        raw = _ingest_clean.clean_text(_ingest_extract.extract_pdf(path.read_bytes()))
    else:
        raw = loaders.read_document(path)
    return parse_text(raw, source=path.name, stem=path.stem, chunk_fn=chunk_fn)


class KnowledgeBase:
    """The corpus behind the RAG layer, rebuildable at runtime.

    Documents can be added and removed while the server is running (see
    `app/kbstore.py`), so the index is rebuilt behind a lock rather than being
    constructed once at import.
    """

    def __init__(self, kb_dir: Path | None = None, chunk_fn=None) -> None:
        self.kb_dir = kb_dir or KB_DIR
        self.kb_dir.mkdir(parents=True, exist_ok=True)
        self.chunk_fn = chunk_fn
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
                    passages.extend(_parse_document(path, chunk_fn=self.chunk_fn))
                except loaders.UnsupportedDocument:
                    continue
            self.passages = passages
            self._index = (
                TfidfIndex([f"{p.title} {p.breadcrumb} {p.text}" for p in passages])
                if passages
                else None
            )

    def search(
        self, query: str, top_k: int = 3, gate: float | None = None
    ) -> list[RetrievedPassage]:
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

    def _search_locked(
        self, query: str, top_k: int, gate: float | None
    ) -> list[RetrievedPassage]:
        if self._index is None:
            return []

        coverages = self._index.coverages(query)
        similarities = self._index.similarities(query)
        bm25 = self._index.bm25_scores(query)

        named = issuers_mentioned(query)
        for index, passage in enumerate(self.passages):
            if not passage.doc_id.startswith(MARKET_DOC_PREFIX):
                continue
            if named and passage.doc_id not in named:
                coverages[index] = 0.0
                similarities[index] = 0.0
                bm25[index] = 0.0
            elif not named:
                coverages[index] *= MARKET_DOC_DEMOTION
                similarities[index] *= MARKET_DOC_DEMOTION
                bm25[index] *= MARKET_DOC_DEMOTION

        best_coverage = max(coverages, default=0.0)
        threshold = _policy.current.min_relevance if gate is None else gate
        if best_coverage < threshold:
            return []

        fused = reciprocal_rank_fusion(
            {
                "coverage": coverages,
                "cosine": similarities,
                "bm25": bm25,
            }
        )

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
            p
            for p in self.kb_dir.iterdir()
            if p.is_file() and p.suffix.lower() in (*loaders.SUPPORTED, ".pdf")
        )

    @property
    def stats(self) -> dict:
        with self._lock:
            documents = {p.source for p in self.passages}
            return {"documents": len(documents), "passages": len(self.passages)}
