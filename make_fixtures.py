"""Generate test fixtures. Run: python make_fixtures.py

Builds a .docx with the standard library so the upload path can be exercised
without checking a binary into the repo or installing python-docx. A .docx is a
ZIP of XML parts; the three parts written here are the minimum Word and the
loader both accept.

The document is deliberately awkward: three heading levels, a fee table, and
sections whose wording differs from how a customer would ask. That is the shape
retrieval actually has to cope with, and it is where a heading-only splitter and
a single-signal ranker start to fail.
"""

from __future__ import annotations

import zipfile
from pathlib import Path
from xml.sax.saxutils import escape

FIXTURE_DIR = Path(__file__).resolve().parent / "data" / "fixtures"

_CONTENT_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>"""

_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>"""

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def _para(text: str, style: str | None = None) -> str:
    props = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
    return f"<w:p>{props}<w:r><w:t xml:space=\"preserve\">{escape(text)}</w:t></w:r></w:p>"


def _bullet(text: str) -> str:
    props = '<w:pPr><w:numPr><w:ilvl w:val="0"/><w:numId w:val="1"/></w:numPr></w:pPr>'
    return f"<w:p>{props}<w:r><w:t xml:space=\"preserve\">{escape(text)}</w:t></w:r></w:p>"


def _table(rows: list[list[str]]) -> str:
    cells = []
    for row in rows:
        tcs = "".join(
            f'<w:tc><w:tcPr><w:tcW w:w="2400" w:type="dxa"/></w:tcPr>{_para(c)}</w:tc>'
            for c in row
        )
        cells.append(f"<w:tr>{tcs}</w:tr>")
    return f"<w:tbl>{''.join(cells)}</w:tbl>"


def build_docx(blocks: list[str]) -> bytes:
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        f'<w:document xmlns:w="{W}"><w:body>{"".join(blocks)}</w:body></w:document>'
    )
    from io import BytesIO

    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", _CONTENT_TYPES)
        archive.writestr("_rels/.rels", _RELS)
        archive.writestr("word/document.xml", document)
    return buffer.getvalue()


BLOCKS = [
    _para("Business Banking Service Schedule", "Heading1"),
    _para("Effective 1 July 2026. Supersedes the schedule dated 3 March 2025."),

    _para("Merchant services", "Heading2"),
    _para("Card acceptance pricing", "Heading3"),
    _para(
        "Card acceptance is priced on an interchange-plus basis. The processing "
        "margin below is charged in addition to the scheme interchange rate, "
        "which varies by card type and is set by the card networks rather than "
        "by the bank."
    ),
    _table([
        ["Transaction type", "Margin", "Per-item fee", "Settlement"],
        ["Consumer debit, in person", "0.35%", "$0.08", "Next business day"],
        ["Consumer credit, in person", "0.60%", "$0.08", "Next business day"],
        ["Card not present", "0.95%", "$0.14", "Two business days"],
        ["International card", "1.40%", "$0.20", "Three business days"],
    ]),
    _para(
        "Settlement timing is measured from the daily cut-off of 5:00 PM ET. "
        "A batch closed after the cut-off settles on the following cycle."
    ),

    _para("Chargebacks and representment", "Heading3"),
    _para(
        "A chargeback raised against a merchant carries a $25 administration "
        "fee, charged whether or not the chargeback is ultimately upheld. The "
        "merchant has 18 calendar days from notification to submit representment "
        "evidence. Merchants whose chargeback ratio exceeds 0.9% of monthly "
        "transaction count for two consecutive months are placed in a monitoring "
        "programme and may be required to hold a rolling reserve."
    ),
    _para(
        "A rolling reserve withholds 5% to 10% of gross settlement for 180 days. "
        "The exact percentage is set case by case by the risk team and is "
        "reviewed every 90 days."
    ),

    _para("Payroll and mass payments", "Heading2"),
    _para("File submission windows", "Heading3"),
    _para(
        "Payroll files must be submitted at least two business days before the "
        "intended value date. Files received after 3:00 PM ET are treated as "
        "received on the following business day. A file containing more than "
        "5,000 payment instructions requires prior arrangement with the "
        "relationship manager."
    ),
    _bullet("Standard payroll: two business days' notice"),
    _bullet("Same-day payroll: available at $75 per file, cut-off 10:00 AM ET"),
    _bullet("Cross-border payroll: five business days, priced per corridor"),

    _para("Failed and returned payments", "Heading3"),
    _para(
        "A payment returned for an invalid account number is re-presented once "
        "automatically at no charge. Any subsequent return is charged at $12 per "
        "item. Returns caused by insufficient funds in the originating account "
        "are charged at $30 per item and are not re-presented automatically."
    ),

    _para("Account maintenance", "Heading2"),
    _para("Dormancy", "Heading3"),
    _para(
        "A business account with no customer-initiated activity for 18 months is "
        "flagged dormant. Dormant accounts continue to accrue interest but cannot "
        "originate outbound payments until reactivated. Reactivation requires a "
        "signatory to attend a branch with photo identification and takes effect "
        "the same business day."
    ),
    _para("Closing an account", "Heading3"),
    _para(
        "An account may be closed by written instruction from an authorised "
        "signatory. Closure is processed within five business days of the final "
        "outstanding item clearing. Accounts closed within 12 months of opening "
        "are charged a $50 early closure fee, waived if the closure follows a "
        "relocation outside the bank's service area."
    ),
]


def main() -> int:
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    target = FIXTURE_DIR / "business-banking-schedule.docx"
    target.write_bytes(build_docx(BLOCKS))
    print(f"Wrote {target.relative_to(Path.cwd())} ({target.stat().st_size:,} bytes)")

    # Prove it round-trips through the loader that will read it in production.
    from app import loaders

    text = loaders.read_document(target)
    headings = [line for line in text.splitlines() if line.startswith("#")]
    print(f"  extracted {len(text):,} chars, {len(headings)} headings")
    for heading in headings:
        print(f"    {heading}")
    print(f"  table rows preserved: {text.count('|') // 5}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
