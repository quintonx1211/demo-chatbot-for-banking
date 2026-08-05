"""Reading uploaded documents into plain text.

Markdown and plain text are read directly. `.docx` is unzipped and its XML
walked with the standard library - a `.docx` is a ZIP of XML parts, so no
third-party package is needed to pull the text out of one.

PDF is deliberately not supported: extracting text from PDF means reimplementing
font encodings and content-stream parsing, which is a library's job, not a
demo's. `SUPPORTED` is the single source of truth for what is accepted, and
anything else is rejected with a message that names what is allowed.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
import zipfile
from io import BytesIO
from pathlib import Path

SUPPORTED = (".md", ".txt", ".docx")

# WordprocessingML namespace. Every text-bearing element lives under it.
_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

# Word's built-in heading styles, mapped to markdown levels so a converted
# document keeps the structure the chunker relies on.
_HEADING_STYLE_RE = re.compile(r"^Heading([1-6])$", re.IGNORECASE)


class UnsupportedDocument(ValueError):
    """Rejected file type. The message is safe to show in the UI."""


def read_document(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in (".md", ".txt"):
        return path.read_text(encoding="utf-8", errors="replace")
    if suffix == ".docx":
        return docx_to_markdown(path.read_bytes())
    raise UnsupportedDocument(
        f"{path.name}: only {', '.join(SUPPORTED)} are supported"
    )


def decode_upload(filename: str, data: bytes) -> str:
    """Convert uploaded bytes into the markdown-ish text the chunker expects."""
    suffix = Path(filename).suffix.lower()
    if suffix in (".md", ".txt"):
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError:
            # Word and Excel exports on Windows are frequently cp1252.
            return data.decode("cp1252", errors="replace")
    if suffix == ".docx":
        return docx_to_markdown(data)
    raise UnsupportedDocument(
        f"Unsupported file type '{suffix or filename}'. "
        f"Accepted: {', '.join(SUPPORTED)}."
    )


# -- docx -----------------------------------------------------------------

def _paragraph_text(paragraph: ET.Element) -> str:
    """Concatenate the runs in a paragraph, honouring explicit breaks and tabs."""
    parts: list[str] = []
    for node in paragraph.iter():
        if node.tag == f"{_W}t":
            parts.append(node.text or "")
        elif node.tag == f"{_W}tab":
            parts.append("\t")
        elif node.tag == f"{_W}br":
            parts.append("\n")
    return "".join(parts).strip()


def _paragraph_level(paragraph: ET.Element) -> int:
    """Markdown heading level for a paragraph, or 0 if it is body text."""
    style = paragraph.find(f"{_W}pPr/{_W}pStyle")
    if style is None:
        return 0
    match = _HEADING_STYLE_RE.match(style.get(f"{_W}val", "") or "")
    return int(match.group(1)) if match else 0


def _table_to_markdown(table: ET.Element) -> str:
    """Render a table as a markdown table.

    Worth the effort rather than flattening to prose: in bank documentation the
    numbers that answer a question - fee, limit, timeframe - live in tables, and
    a row stripped of its header is a number with no meaning.
    """
    rows: list[list[str]] = []
    for row in table.findall(f"{_W}tr"):
        cells = [
            " ".join(
                _paragraph_text(p) for p in cell.findall(f"{_W}p")
            ).strip()
            for cell in row.findall(f"{_W}tc")
        ]
        if any(cells):
            rows.append(cells)

    if not rows:
        return ""

    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]

    lines = ["| " + " | ".join(rows[0]) + " |",
             "| " + " | ".join(["---"] * width) + " |"]
    lines += ["| " + " | ".join(r) + " |" for r in rows[1:]]
    return "\n".join(lines)


def docx_to_markdown(data: bytes) -> str:
    """Extract a .docx into markdown, preserving headings, lists and tables."""
    try:
        archive = zipfile.ZipFile(BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise UnsupportedDocument(
            "That .docx could not be opened - it may be corrupt, or it may be "
            "an older .doc renamed rather than converted."
        ) from exc

    try:
        xml = archive.read("word/document.xml")
    except KeyError as exc:
        raise UnsupportedDocument(
            "That file is a ZIP but not a Word document (no word/document.xml)."
        ) from exc

    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        raise UnsupportedDocument(f"Malformed .docx XML: {exc}") from exc

    body = root.find(f"{_W}body")
    if body is None:
        return ""

    blocks: list[str] = []
    # Iterate direct children only: a paragraph inside a table must not also be
    # emitted on its own, or every cell would appear twice.
    for element in body:
        if element.tag == f"{_W}p":
            text = _paragraph_text(element)
            if not text:
                continue
            level = _paragraph_level(element)
            if level:
                blocks.append(f"{'#' * level} {text}")
            elif element.find(f"{_W}pPr/{_W}numPr") is not None:
                blocks.append(f"- {text}")
            else:
                blocks.append(text)
        elif element.tag == f"{_W}tbl":
            table = _table_to_markdown(element)
            if table:
                blocks.append(table)

    return "\n\n".join(blocks)
