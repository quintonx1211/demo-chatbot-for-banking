"""Document chunking.

The original loader split documents on `##` headings and stopped there, which
works only because the starter corpus was hand-written with one topic per
section. Real documents - a policy PDF exported to text, a Word file from the
compliance team - have deep heading trees, sections that run for pages, and
paragraphs that carry the actual answer. Feeding those in whole produces
passages too long to rank precisely and too broad to cite usefully.

So chunking runs in three passes, coarse to fine:

  1. split on headings, keeping the full heading path for citation
  2. split each section into paragraphs
  3. pack paragraphs into size-bounded chunks, with overlap so an answer that
     straddles a boundary survives in at least one chunk

A section that already fits stays a single chunk, so the existing corpus
chunks exactly as it did before - the upgrade costs nothing on documents that
were already well-formed.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Sizes are in characters, not tokens: the retriever is lexical, so characters
# are the honest unit and avoid pretending to a precision we don't have.
TARGET_CHARS = 900     # preferred chunk size
MAX_CHARS = 1400       # hard ceiling before a chunk is split regardless
MIN_CHARS = 120        # below this a chunk is merged into its neighbour
OVERLAP_CHARS = 150    # tail of the previous chunk repeated into the next

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*$", re.MULTILINE)
# Markdown table rows and list bullets are kept intact: splitting a table
# mid-row loses the column meaning that made the numbers readable.
_PARA_SPLIT_RE = re.compile(r"\n\s*\n")


@dataclass
class Chunk:
    text: str
    heading: str                       # leaf heading - what gets cited
    heading_path: list[str] = field(default_factory=list)
    index: int = 0                     # position within the document

    @property
    def breadcrumb(self) -> str:
        return " › ".join(self.heading_path) if self.heading_path else self.heading


def _normalise(text: str) -> str:
    """Collapse whitespace without destroying paragraph or table structure."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    # Trailing spaces make paragraph detection unreliable.
    text = re.sub(r"[ \t]+\n", "\n", text)
    # Three or more blank lines add nothing and inflate the character budget.
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _sections(text: str) -> list[tuple[list[str], str]]:
    """Split into (heading_path, body) pairs, tracking heading depth.

    Text before the first heading is emitted under an empty path rather than
    dropped - in a converted document that preamble is often the summary.
    """
    matches = list(_HEADING_RE.finditer(text))
    if not matches:
        return [([], text)] if text.strip() else []

    sections: list[tuple[list[str], str]] = []

    preamble = text[: matches[0].start()].strip()
    if preamble:
        sections.append(([], preamble))

    stack: list[tuple[int, str]] = []
    for position, match in enumerate(matches):
        level = len(match.group(1))
        heading = match.group(2).strip()

        # Pop headings at the same or deeper level; what remains is the parent
        # path. This is what turns a flat regex scan into a real tree walk.
        while stack and stack[-1][0] >= level:
            stack.pop()
        stack.append((level, heading))

        end = matches[position + 1].start() if position + 1 < len(matches) else len(text)
        body = text[match.end():end].strip()
        if body:
            sections.append(([h for _, h in stack], body))

    return sections


def _paragraphs(body: str) -> list[str]:
    parts = [p.strip() for p in _PARA_SPLIT_RE.split(body)]
    return [p for p in parts if p]


def _is_table(paragraph: str) -> bool:
    """A markdown table: most lines start with a pipe."""
    lines = [line for line in paragraph.splitlines() if line.strip()]
    if len(lines) < 2:
        return False
    piped = sum(1 for line in lines if line.lstrip().startswith("|"))
    return piped >= max(2, len(lines) - 1)


def _split_table(table: str) -> list[str]:
    """Split an oversized table on row boundaries, repeating the header.

    Sentence splitting mangles tables. A table has no sentence-ending
    punctuation between rows, so `re.split` on `[.!?]` cut wherever a full stop
    happened to fall inside a cell - one passage in this corpus began

        hệ thống, viễn thông hoặc sự kiện tương tự |

    which is the tail of a row, with no header above it and no way for a reader
    to know what column they are looking at. The assistant then served that as
    the opening line of an answer.

    Repeating the header on each piece costs a few characters and is what makes
    the second piece readable at all.
    """
    lines = [line for line in table.splitlines() if line.strip()]
    header = lines[:2] if len(lines) > 2 and set(lines[1].strip()) <= set("|-: ") \
        else lines[:1]
    body = lines[len(header):]

    pieces: list[str] = []
    current = list(header)
    size = sum(len(line) for line in header)
    for row in body:
        if size + len(row) > TARGET_CHARS and len(current) > len(header):
            pieces.append("\n".join(current))
            current = list(header)
            size = sum(len(line) for line in header)
        current.append(row)
        size += len(row)
    if len(current) > len(header):
        pieces.append("\n".join(current))
    return pieces or [table]


def _split_oversized(paragraph: str) -> list[str]:
    """Break a single paragraph that exceeds MAX_CHARS on sentence boundaries."""
    if len(paragraph) <= MAX_CHARS:
        return [paragraph]
    if _is_table(paragraph):
        return _split_table(paragraph)

    sentences = re.split(r"(?<=[.!?])\s+", paragraph)
    pieces: list[str] = []
    current = ""
    for sentence in sentences:
        if current and len(current) + len(sentence) + 1 > TARGET_CHARS:
            pieces.append(current)
            current = sentence
        else:
            current = f"{current} {sentence}".strip()
    if current:
        pieces.append(current)

    # A single sentence longer than the ceiling (a dense table row, say) is
    # cut on width as a last resort rather than dropped.
    output: list[str] = []
    for piece in pieces:
        while len(piece) > MAX_CHARS:
            output.append(piece[:MAX_CHARS])
            piece = piece[MAX_CHARS - OVERLAP_CHARS:]
        if piece:
            output.append(piece)
    return output


def _pack(paragraphs: list[str]) -> list[str]:
    """Group paragraphs into chunks near TARGET_CHARS, overlapping the seams."""
    units: list[str] = []
    for paragraph in paragraphs:
        units.extend(_split_oversized(paragraph))

    chunks: list[str] = []
    current = ""
    for unit in units:
        if current and len(current) + len(unit) + 2 > TARGET_CHARS:
            chunks.append(current)
            # Carry the tail of the finished chunk into the next one so a fact
            # split across the boundary is still retrievable from one chunk.
            # Start the overlap at a LINE boundary, not a word boundary.
            #
            # Cutting at the first space landed inside a table row, so a chunk
            # could open with "hệ thống, viễn thông hoặc sự kiện tương tự |" -
            # the tail of a row, no header, no way to tell what column it is.
            # That fragment then became the first line of a customer-facing
            # answer. Half a word helps nobody; half a table row is worse,
            # because it looks like content rather than like damage.
            tail = current[-OVERLAP_CHARS:]
            newline = tail.find("\n")
            if newline != -1:
                tail = tail[newline + 1:]
            else:
                space = tail.find(" ")
                tail = tail[space + 1:] if space != -1 else ""
            # An overlap that is itself a partial table is not worth carrying.
            if tail.lstrip().startswith("|") and not tail.rstrip().endswith("|"):
                tail = ""
            current = (tail + "\n\n" + unit).strip() if tail.strip() else unit
        else:
            current = f"{current}\n\n{unit}".strip()
    if current:
        chunks.append(current)

    # Fold a runt trailing chunk back into its predecessor.
    if len(chunks) > 1 and len(chunks[-1]) < MIN_CHARS:
        chunks[-2] = f"{chunks[-2]}\n\n{chunks[-1]}"
        chunks.pop()

    return chunks


def chunk_document(text: str) -> list[Chunk]:
    """Split a document into retrievable chunks, preserving heading context."""
    normalised = _normalise(text)
    if not normalised:
        return []

    chunks: list[Chunk] = []
    for path, body in _sections(normalised):
        leaf = path[-1] if path else "Introduction"
        for piece in _pack(_paragraphs(body)):
            chunks.append(Chunk(
                text=piece,
                heading=leaf,
                heading_path=list(path),
                index=len(chunks),
            ))
    return chunks


_METADATA_LINE_RE = re.compile(r"^\*\*[\w ]+:\*\*.*$", re.MULTILINE)


def strip_metadata(text: str) -> str:
    """Remove `**key:** value` header lines from the body.

    They are extracted separately as document metadata, and left in place they
    become a chunk of their own - a passage made of a doc id, an owner and a
    review date, which can never answer a customer question but can still be
    retrieved for one.
    """
    return _METADATA_LINE_RE.sub("", text)


def extract_title(text: str) -> str | None:
    """First level-1 heading, used as the document title."""
    match = re.search(r"^#\s+(.+?)\s*#*$", text, re.MULTILINE)
    return match.group(1).strip() if match else None


def extract_doc_id(text: str) -> str | None:
    """`**doc_id:** KB-XXX-001` metadata line, if the document carries one."""
    match = re.search(r"^\*\*doc_id:\*\*\s*(\S+)", text, re.MULTILINE)
    return match.group(1) if match else None
