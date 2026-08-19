"""Text extraction for the ingest pipeline.

.md/.txt/.docx are delegated straight to `app/loaders.py` - that module
already handles them correctly (docx tables, heading styles), and this file
has no reason to know how a docx is zipped.

.pdf is new: `pdfplumber` reads page text and page tables separately, so a
fee schedule that lives in a table survives as a table instead of being
flattened into unaligned prose. `pdfplumber` is an optional dependency, same
posture as the LLM provider SDKs - importing it only happens inside
`extract_pdf`, so a repo without it installed is unaffected until a PDF is
actually uploaded.
"""

from __future__ import annotations

from pathlib import Path

from .. import loaders

SUPPORTED = loaders.SUPPORTED + (".pdf",)


class UnsupportedDocument(loaders.UnsupportedDocument):
    """Re-exported so callers only need to catch one exception type from
    this package, regardless of which format tripped it."""


def extract_text(filename: str, data: bytes) -> str:
    """Bytes in, markdown-ish text out - the same contract as
    `loaders.decode_upload`, extended to PDF."""
    suffix = Path(filename).suffix.lower()
    if suffix == ".pdf":
        return extract_pdf(data)
    try:
        return loaders.decode_upload(filename, data)
    except loaders.UnsupportedDocument as exc:
        raise UnsupportedDocument(str(exc)) from exc


def _row_to_markdown(row: list) -> str:
    cells = [(cell or "").strip().replace("\n", " ") for cell in row]
    return "| " + " | ".join(cells) + " |"


def _table_to_markdown(rows: list[list]) -> str:
    """Same shape as `loaders._table_to_markdown` - a header row, a
    separator, then body rows - so `chunker._is_table`/`_split_table`
    recognise it exactly like a table extracted from .docx."""
    if not rows:
        return ""
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    lines = [_row_to_markdown(rows[0]), _row_to_markdown(["---"] * width)]
    lines += [_row_to_markdown(r) for r in rows[1:]]
    return "\n".join(lines)


def extract_pdf(data: bytes) -> str:
    """Page text and page tables, concatenated per page.

    Table position within the page text is not tracked - pdfplumber reports
    tables and running text as separate extractions, and recovering their
    original interleaving means matching bounding boxes, which is more
    precision than a demo-grade ingester needs. Putting the table after the
    page's prose is a reasonable approximation: the table is still attributed
    to the right page and heading context, just not to the exact paragraph
    that introduced it.
    """
    try:
        import pdfplumber
    except ImportError as exc:
        raise UnsupportedDocument(
            "PDF support needs pdfplumber - run: pip install pdfplumber"
        ) from exc

    from io import BytesIO

    blocks: list[str] = []
    try:
        with pdfplumber.open(BytesIO(data)) as pdf:
            for page in pdf.pages:
                text = (page.extract_text() or "").strip()
                if text:
                    blocks.append(text)
                for table in page.extract_tables() or []:
                    rendered = _table_to_markdown(table)
                    if rendered:
                        blocks.append(rendered)
    except Exception as exc:  # pdfplumber raises assorted parser errors on
        # malformed PDFs - none of them are actionable beyond "this file
        # could not be read", so they collapse to the one exception this
        # package promises to raise.
        raise UnsupportedDocument(f"Could not read that PDF: {exc}") from exc

    return "\n\n".join(blocks)
