"""Knowledge-base document management: list, read, upload, delete.

This is the operator-facing side of the RAG corpus. It accepts file content
from an HTTP request, so validation is the substance of the module rather than
an afterthought: an unchecked filename here is a write-anywhere primitive, and
an unparseable document silently contributes nothing to retrieval.

Every mutation rebuilds the index, so a document is searchable the moment it
finishes uploading.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .retriever import KnowledgeBase, _parse_document

# Filenames are restricted to a conservative character set and must be a bare
# name - no directory component at all. Checking for ".." is not enough on its
# own (absolute paths, alternate separators, and NTFS streams all bypass it),
# so this is an allowlist rather than a denylist.
FILENAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}\.md$")

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

    def to_dict(self) -> dict:
        return {
            "filename": self.filename,
            "doc_id": self.doc_id,
            "title": self.title,
            "passages": self.passages,
            "bytes": self.bytes,
            "headings": self.headings,
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
                "dash or underscore, and end in .md"
            )
        if name.rsplit(".", 1)[0].split(".")[0].lower() in _RESERVED_STEMS:
            raise KBError(f"{name} is a reserved device name on Windows")

        root = self.kb.kb_dir.resolve()
        candidate = (root / name).resolve()
        if candidate.parent != root:
            raise KBError("Filename must not contain a path")
        return candidate

    @staticmethod
    def _validate_content(content: str) -> None:
        if not content.strip():
            raise KBError("Document is empty")

        size = len(content.encode("utf-8"))
        if size > MAX_BYTES:
            raise KBError(
                f"Document is {size // 1024} KB; the limit is {MAX_BYTES // 1024} KB"
            )

        # A document with no `##` heading parses to zero passages: it would sit
        # in the directory looking installed while contributing nothing to
        # retrieval. Rejecting it up front beats debugging that later.
        if not re.search(r"^##\s+\S", content, re.MULTILINE):
            raise KBError(
                "No '## Section' headings found - the retriever splits documents "
                "on them, so a document without any would never be searchable"
            )

    # -- read -------------------------------------------------------------

    def list_documents(self) -> list[DocumentInfo]:
        documents: list[DocumentInfo] = []
        for path in sorted(self.kb.kb_dir.glob("*.md")):
            passages = _parse_document(path)
            documents.append(DocumentInfo(
                filename=path.name,
                doc_id=passages[0].doc_id if passages else path.stem.upper(),
                title=passages[0].title if passages else path.stem,
                passages=len(passages),
                bytes=path.stat().st_size,
                headings=[p.heading for p in passages],
            ))
        return documents

    def read_document(self, filename: str) -> dict:
        path = self._safe_path(filename)
        if not path.exists():
            raise KBError(f"No document named {filename}")
        content = path.read_text(encoding="utf-8")
        passages = _parse_document(path)
        return {
            "filename": path.name,
            "content": content,
            "passages": [
                {"heading": p.heading, "citation": p.citation, "text": p.text}
                for p in passages
            ],
        }

    # -- mutate -----------------------------------------------------------

    def save_document(self, filename: str, content: str) -> DocumentInfo:
        path = self._safe_path(filename)
        self._validate_content(content)

        if not path.exists() and len(list(self.kb.kb_dir.glob("*.md"))) >= MAX_DOCUMENTS:
            raise KBError(f"Knowledge base is limited to {MAX_DOCUMENTS} documents")

        # Normalise line endings so documents uploaded from Windows and from a
        # POSIX box parse identically - the passage splitter is line-anchored.
        normalised = content.replace("\r\n", "\n").replace("\r", "\n")
        path.write_text(normalised, encoding="utf-8")

        self.kb.reload()

        info = next((d for d in self.list_documents() if d.filename == path.name), None)
        if info is None:  # pragma: no cover - the write above just succeeded
            raise KBError("Document was written but could not be read back")
        return info

    def delete_document(self, filename: str) -> None:
        path = self._safe_path(filename)
        if not path.exists():
            raise KBError(f"No document named {filename}")
        path.unlink()
        self.kb.reload()
