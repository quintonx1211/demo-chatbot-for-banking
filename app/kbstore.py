"""Knowledge-base document management: list, read, upload, delete.

This is the operator-facing side of the RAG corpus. It accepts file content
from an HTTP request, so validation is the substance of the module rather than
an afterthought: an unchecked filename here is a write-anywhere primitive, and
an unparseable document silently contributes nothing to retrieval.

Every mutation rebuilds the index, so a document is searchable the moment it
finishes uploading.
"""

from __future__ import annotations

import base64
import binascii
import re
from dataclasses import dataclass
from pathlib import Path

from . import loaders
from .retriever import KnowledgeBase, _parse_document, parse_text

# Filenames are restricted to a conservative character set and must be a bare
# name - no directory component at all. Checking for ".." is not enough on its
# own (absolute paths, alternate separators, and NTFS streams all bypass it),
# so this is an allowlist rather than a denylist.
_EXTENSIONS = "|".join(ext.lstrip(".") for ext in loaders.SUPPORTED)
FILENAME_RE = re.compile(
    rf"^[A-Za-z0-9][A-Za-z0-9._-]{{0,63}}\.({_EXTENSIONS})$", re.IGNORECASE
)

# Windows resolves these as device names even with an extension, so `con.md`
# is not a file - writing to it opens the console. The demo runs on Windows,
# so this is a real case rather than a theoretical one.
_RESERVED_STEMS = {
    "con", "prn", "aux", "nul",
    *(f"com{n}" for n in range(1, 10)),
    *(f"lpt{n}" for n in range(1, 10)),
}

MAX_BYTES = 256 * 1024        # a knowledge-base article, not a data dump
MAX_DOCUMENTS = 200


class KBError(ValueError):
    """Rejected operator input. The message is safe to show in the UI."""


@dataclass
class DocumentInfo:
    filename: str
    doc_id: str
    title: str
    passages: int
    bytes: int
    headings: list[str]
    fmt: str = "md"

    def to_dict(self) -> dict:
        return {
            "filename": self.filename,
            "doc_id": self.doc_id,
            "title": self.title,
            "passages": self.passages,
            "bytes": self.bytes,
            "headings": self.headings,
            "format": self.fmt,
        }


class KnowledgeBaseStore:
    def __init__(self, kb: KnowledgeBase) -> None:
        self.kb = kb

    # -- validation -------------------------------------------------------

    def _safe_path(self, filename: str) -> Path:
        """Resolve `filename` to a path inside the knowledge-base directory.

        Two independent checks: the name must match the allowlist, and the
        resolved path must still sit inside kb_dir. The second catches anything
        the first missed - symlinks in particular, which pass a name check and
        still escape.
        """
        name = (filename or "").strip()
        if not FILENAME_RE.match(name):
            raise KBError(
                "Filename must be 1-64 characters of letters, digits, dot, "
                f"dash or underscore, and end in {' / '.join(loaders.SUPPORTED)}"
            )
        if name.rsplit(".", 1)[0].split(".")[0].lower() in _RESERVED_STEMS:
            raise KBError(f"{name} is a reserved device name on Windows")

        root = self.kb.kb_dir.resolve()
        candidate = (root / name).resolve()
        if candidate.parent != root:
            raise KBError("Filename must not contain a path")
        return candidate

    def _check_size(self, size: int) -> None:
        if size > MAX_BYTES:
            raise KBError(
                f"Document is {size // 1024} KB; the limit is {MAX_BYTES // 1024} KB"
            )

    def _check_retrievable(self, text: str, name: str) -> None:
        """Reject anything that would produce no passages.

        The old rule required a literal `## Section` heading. That was a proxy
        for the real requirement, and a wrong one now the chunker also splits on
        paragraphs — a heading-free .txt of policy prose chunks perfectly well.
        So ask the pipeline directly instead of guessing: parse it, and reject
        only if nothing retrievable came out. A document that yields no passages
        would sit in the directory looking installed while contributing nothing.
        """
        if not text.strip():
            raise KBError("Document is empty")
        if not parse_text(text, source=name, stem=Path(name).stem):
            raise KBError(
                "This document produced no retrievable passages — it appears to "
                "contain no body text the chunker can index."
            )

    # -- read -------------------------------------------------------------

    def list_documents(self) -> list[DocumentInfo]:
        documents: list[DocumentInfo] = []
        for path in self.kb.document_paths():
            try:
                passages = _parse_document(path)
            except loaders.UnsupportedDocument:
                continue
            documents.append(DocumentInfo(
                filename=path.name,
                doc_id=passages[0].doc_id if passages else path.stem.upper(),
                title=passages[0].title if passages else path.stem,
                passages=len(passages),
                bytes=path.stat().st_size,
                headings=[p.heading for p in passages],
                fmt=path.suffix.lower().lstrip("."),
            ))
        return documents

    def read_document(self, filename: str) -> dict:
        path = self._safe_path(filename)
        if not path.exists():
            raise KBError(f"No document named {filename}")

        try:
            content = loaders.read_document(path)
        except loaders.UnsupportedDocument as exc:
            raise KBError(str(exc)) from exc

        passages = _parse_document(path)
        return {
            "filename": path.name,
            "format": path.suffix.lower().lstrip("."),
            # For a .docx this is the extracted markdown, not the original
            # bytes — which is exactly what the reviewer needs to see, since
            # it is what retrieval actually indexes.
            "content": content,
            "editable": path.suffix.lower() in (".md", ".txt"),
            "passages": [
                {"heading": p.heading, "breadcrumb": p.breadcrumb,
                 "citation": p.citation, "text": p.text}
                for p in passages
            ],
        }

    # -- mutate -----------------------------------------------------------

    def _check_capacity(self, path: Path) -> None:
        if not path.exists() and len(self.kb.document_paths()) >= MAX_DOCUMENTS:
            raise KBError(f"Knowledge base is limited to {MAX_DOCUMENTS} documents")

    def _finish(self, path: Path) -> DocumentInfo:
        self.kb.reload()
        info = next((d for d in self.list_documents() if d.filename == path.name), None)
        if info is None:  # pragma: no cover — the write above just succeeded
            raise KBError("Document was written but could not be read back")
        return info

    def save_document(self, filename: str, content: str) -> DocumentInfo:
        """Save text typed into the editor. Text formats only."""
        path = self._safe_path(filename)
        if path.suffix.lower() not in (".md", ".txt"):
            raise KBError(
                f"{path.suffix} documents are uploaded as files, not edited as text"
            )
        self._check_size(len(content.encode("utf-8")))
        # Normalise line endings so documents written on Windows and on a POSIX
        # box chunk identically — the section splitter is line-anchored.
        normalised = content.replace("\r\n", "\n").replace("\r", "\n")
        self._check_retrievable(normalised, path.name)
        self._check_capacity(path)

        path.write_text(normalised, encoding="utf-8")
        return self._finish(path)

    def save_upload(self, filename: str, data: bytes) -> DocumentInfo:
        """Save an uploaded file, verifying it parses before it is written.

        Order matters: decode and parse first, write second. A .docx that
        cannot be extracted must not land in the corpus directory, where every
        subsequent reload would have to skip it.
        """
        path = self._safe_path(filename)
        self._check_size(len(data))

        try:
            text = loaders.decode_upload(path.name, data)
        except loaders.UnsupportedDocument as exc:
            raise KBError(str(exc)) from exc

        self._check_retrievable(text, path.name)
        self._check_capacity(path)

        if path.suffix.lower() in (".md", ".txt"):
            path.write_text(text.replace("\r\n", "\n").replace("\r", "\n"),
                            encoding="utf-8")
        else:
            path.write_bytes(data)
        return self._finish(path)

    @staticmethod
    def decode_base64(payload: str) -> bytes:
        """Decode the browser's base64 upload, rejecting anything malformed.

        `validate=True` matters: without it Python silently discards characters
        outside the alphabet, so a truncated or corrupted upload would decode to
        plausible-looking garbage instead of failing here.
        """
        if not payload:
            raise KBError("No file content received")
        try:
            return base64.b64decode(payload, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise KBError(f"File content was not valid base64: {exc}") from exc

    def delete_document(self, filename: str) -> None:
        path = self._safe_path(filename)
        if not path.exists():
            raise KBError(f"No document named {filename}")
        path.unlink()
        self.kb.reload()
