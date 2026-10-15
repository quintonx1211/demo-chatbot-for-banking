"""Clean-up pass for extracted text, before it reaches the chunker.

Markdown source files rarely need any of this - a human wrote them without
page breaks. PDF extraction is where it earns its keep: a page-based
extractor hands back running headers, footers, page numbers and words the
PDF's line-wrapping cut in half, none of which survive translation into a
retrievable passage without visibly damaging the answer.
"""

from __future__ import annotations

import re
from collections import Counter

_HYPHEN_WRAP_RE = re.compile(r"(\w)-\n([a-zà-ỹ])")

_PAGE_NUMBER_RE = re.compile(
    r"^\s*(page\s+\d+(\s*(of|/)\s*\d+)?|trang\s+\d+(\s*/\s*\d+)?|\d{1,4})\s*$",
    re.IGNORECASE,
)

_BULLET_RE = re.compile(r"^[ \t]*[•◦▪●‣∙][ \t]+", re.MULTILINE)

_CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def _dehyphenate(text: str) -> str:
    return _HYPHEN_WRAP_RE.sub(r"\1\2", text)


def _strip_repeated_lines(text: str, min_repeats: int = 3) -> str:
    """Drop lines that repeat identically many times across the document.

    A running header or footer appears once per page - three or more
    identical repeats of a short line is not a coincidence in body text, it's
    the same header/footer being re-emitted by the page-based extractor.
    Short-circuited on length so a real repeated sentence (a section that
    legitimately restates something) is never at risk: this only touches
    lines under 80 characters, the range mastheads and footers live in.
    """
    lines = text.split("\n")
    counts = Counter(line.strip() for line in lines if 0 < len(line.strip()) < 80)
    repeated = {line for line, count in counts.items() if count >= min_repeats}
    if not repeated:
        return text
    return "\n".join(line for line in lines if line.strip() not in repeated)


def _normalise_whitespace(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def clean_text(text: str) -> str:
    """The full clean-up pass, in the order that matters.

    Repeated-line stripping runs before dehyphenation and page-number
    removal, because a header/footer that happens to end mid-word or be a
    lone number should be gone before those narrower passes look at it.
    """
    text = _CONTROL_CHARS_RE.sub("", text)
    text = _strip_repeated_lines(text)
    text = "\n".join(
        line for line in text.split("\n") if not _PAGE_NUMBER_RE.match(line)
    )
    text = _dehyphenate(text)
    text = _BULLET_RE.sub("- ", text)
    text = _normalise_whitespace(text)
    return text
