"""Knowledge-base loader and retriever for the RAG layer.

Markdown files under data/kb are split on `##` headings into passages. Each
passage keeps its source file, doc_id and heading so every generated answer can
be traced back to a verified document - that traceability is the whole point of
grounding the LLM layer rather than letting it answer freely.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from threading import RLock

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
MIN_RELEVANCE = 0.28
RELATIVE_CUTOFF = 0.60


@dataclass
class Passage:
    passage_id: str
    doc_id: str
    source: str
    title: str
    heading: str
    text: str

    @property
    def citation(self) -> str:
        return f"{self.doc_id} · {self.heading}"


@dataclass
class RetrievedPassage:
    passage: Passage
    score: float          # coverage - the relevance gate
    similarity: float = 0.0   # cosine - tie-breaker only


def _parse_document(path: Path) -> list[Passage]:
    raw = path.read_text(encoding="utf-8")

    title_match = re.search(r"^#\s+(.+)$", raw, re.MULTILINE)
    title = title_match.group(1).strip() if title_match else path.stem

    doc_match = re.search(r"^\*\*doc_id:\*\*\s*(\S+)", raw, re.MULTILINE)
    doc_id = doc_match.group(1) if doc_match else path.stem.upper()

    passages: list[Passage] = []
    sections = re.split(r"^##\s+(.+)$", raw, flags=re.MULTILINE)
    # re.split with one capture group yields [preamble, heading, body, ...].
    for index in range(1, len(sections), 2):
        heading = sections[index].strip()
        body = re.sub(r"\s+", " ", sections[index + 1]).strip()
        if not body:
            continue
        passages.append(
            Passage(
                passage_id=f"{doc_id}#{len(passages) + 1}",
                doc_id=doc_id,
                source=path.name,
                title=title,
                heading=heading,
                text=body,
            )
        )
    return passages


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
            for path in sorted(self.kb_dir.glob("*.md")):
                passages.extend(_parse_document(path))
            self.passages = passages
            # Index heading + body: headings carry a lot of signal in a small corpus.
            self._index = TfidfIndex(
                [f"{p.title} {p.heading} {p.text}" for p in passages]
            ) if passages else None

    def search(self, query: str, top_k: int = 3) -> list[RetrievedPassage]:
        with self._lock:
            return self._search_locked(query, top_k)

    def _search_locked(self, query: str, top_k: int) -> list[RetrievedPassage]:
        if self._index is None:
            return []
        # Coverage decides relevance; cosine similarity breaks ties between
        # passages that cover the question equally well.
        coverages = self._index.coverages(query)
        similarities = self._index.similarities(query)
        ranked = sorted(
            (RetrievedPassage(passage=p, score=round(c, 3), similarity=round(s, 3))
             for p, c, s in zip(self.passages, coverages, similarities)),
            key=lambda r: (r.score, r.similarity),
            reverse=True,
        )
        if not ranked or ranked[0].score < MIN_RELEVANCE:
            return []
        floor = max(MIN_RELEVANCE, ranked[0].score * RELATIVE_CUTOFF)
        return [r for r in ranked[:top_k] if r.score >= floor]

    @property
    def stats(self) -> dict:
        with self._lock:
            documents = {p.source for p in self.passages}
            return {"documents": len(documents), "passages": len(self.passages)}
