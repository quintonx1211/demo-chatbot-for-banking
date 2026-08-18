"""Ingest pipeline: bytes in, retrievable chunks out.

    extract_text  -> clean_text -> chunk (size-bounded or semantic)

One entry point (`ingest`) so a caller - the upload endpoint, a script, this
package's own eval tool - doesn't have to know the three-step shape. Each
step stays in its own module because each is independently useful: `extract`
alone is what the upload endpoint needs today for PDF support, without
committing to semantic chunking as the default.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .. import chunker
from . import clean as clean_mod
from . import extract as extract_mod
from .semantic_chunk import semantic_chunk_document

UnsupportedDocument = extract_mod.UnsupportedDocument


@dataclass
class IngestResult:
    doc_id: str
    title: str
    text: str                      # cleaned, chunked-from text
    chunks: list[chunker.Chunk]
    warnings: list[str] = field(default_factory=list)


def ingest(filename: str, data: bytes, *, semantic: bool = False,
          stem: str | None = None) -> IngestResult:
    """Run the full pipeline on uploaded bytes.

    `semantic=False` by default - matches `retriever.py`'s default chunker,
    so a document ingested through this function and one loaded the normal
    way chunk identically unless the caller opts in. See
    `tests/eval_chunking.py` before flipping this on for real documents.
    """
    raw = extract_mod.extract_text(filename, data)
    cleaned = clean_mod.clean_text(raw)

    stem = stem or filename.rsplit(".", 1)[0]
    title = chunker.extract_title(cleaned) or stem
    doc_id = chunker.extract_doc_id(cleaned) or stem.upper()
    body = chunker.strip_metadata(cleaned)

    chunk_fn = semantic_chunk_document if semantic else chunker.chunk_document
    chunks = chunk_fn(body)

    warnings: list[str] = []
    if not chunks:
        warnings.append("No retrievable content found after cleaning.")

    return IngestResult(doc_id=doc_id, title=title, text=cleaned,
                        chunks=chunks, warnings=warnings)
