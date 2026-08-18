"""Chunking quality harness. Run: python tests/eval_chunking.py [-v] [--pdf FILE]

Compares the size-bounded chunker (`app/chunker.py`, what the app actually
runs today) against the semantic chunker
(`app/ingest/semantic_chunk.py`, topic-boundary cuts) on the SAME
labelled question set `eval_retrieval.py` uses - reused, not duplicated, so
the two scripts can never quietly drift onto different definitions of "right
answer".

Two questions, kept separate rather than folded into one score:

  1. Retrieval quality - does one chunking strategy answer more of the
     labelled questions correctly? This is what actually matters; a chunker
     that produces prettier boundaries but retrieves worse is not an
     improvement.
  2. Chunk shape - size distribution, how many chunks land outside the
     intended bounds, how many look like they were cut mid-sentence or
     mid-table. Diagnostic, not a verdict on its own - a chunker can have
     ideal shape statistics and still retrieve worse, which is exactly why
     (1) is measured too rather than assumed from (2).

Neither strategy is switched to a default here or anywhere else based on
this script's output - see `app/ingest/semantic_chunk.py`'s docstring.
"""

from __future__ import annotations

import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from app import chunker
from app.ingest.semantic_chunk import semantic_chunk_document
from app.retriever import KnowledgeBase
from eval_retrieval import IN_SCOPE, OUT_OF_SCOPE, evaluate, print_report

STRATEGIES = {
    "baseline (size-bounded, live default)": chunker.chunk_document,
    "candidate (semantic boundary)": semantic_chunk_document,
}


# -- chunk shape ------------------------------------------------------------

def _looks_mid_cut(text: str) -> bool:
    """Heuristic, not proof: a chunk that opens on a lowercase letter after
    something other than a list dash, or opens on a bare table row with no
    header above it, reads like it was cut mid-sentence or mid-table."""
    first_line = text.strip().split("\n", 1)[0].strip()
    if not first_line:
        return False
    if first_line.startswith("|") and "---" not in text.split("\n", 2)[:2]:
        # A table fragment: starts with a pipe row, but the separator row
        # ("|---|---|") is not among the first two lines, so there's no
        # header in this piece.
        header_present = len(text.splitlines()) > 1 and "---" in text.splitlines()[1]
        if not header_present:
            return True
    first_char = first_line.lstrip("|- ").strip()[:1]
    return first_char.islower()


def chunk_shape_report(kb_dir: Path, chunk_fn) -> dict:
    sizes: list[int] = []
    suspect = 0
    for path in sorted(p for p in kb_dir.iterdir() if p.suffix.lower() == ".md"):
        raw = chunker.strip_metadata(path.read_text(encoding="utf-8"))
        for chunk in chunk_fn(raw):
            sizes.append(len(chunk.text))
            if _looks_mid_cut(chunk.text):
                suspect += 1

    if not sizes:
        return {"count": 0}
    in_bounds = sum(1 for s in sizes if chunker.MIN_CHARS <= s <= chunker.MAX_CHARS)
    return {
        "count": len(sizes),
        "mean": statistics.mean(sizes),
        "stdev": statistics.stdev(sizes) if len(sizes) > 1 else 0.0,
        "in_bounds_pct": in_bounds / len(sizes),
        "suspect_cuts": suspect,
    }


def print_shape_report(label: str, report: dict) -> None:
    print(f"\n  {label}")
    if not report["count"]:
        print("    (no chunks)")
        return
    print(f"    chunks           {report['count']}")
    print(f"    mean size        {report['mean']:.0f} chars")
    print(f"    size stdev       {report['stdev']:.0f} chars")
    print(f"    within bounds    {report['in_bounds_pct']:.1%}  "
          f"[{chunker.MIN_CHARS}, {chunker.MAX_CHARS}]")
    print(f"    suspect cuts     {report['suspect_cuts']}  "
          f"(looks mid-sentence or mid-table - heuristic, verify by eye)")


# -- PDF smoke test -----------------------------------------------------

def show_pdf(path: Path) -> None:
    from app.ingest import ingest

    data = path.read_bytes()
    result = ingest(path.name, data, semantic=True)
    print(f"\n{'=' * 72}\nPDF ingest: {path.name}\n{'=' * 72}")
    print(f"  doc_id: {result.doc_id}   title: {result.title}")
    if result.warnings:
        print(f"  warnings: {result.warnings}")
    for chunk in result.chunks:
        print(f"\n  [{chunk.index}] {chunk.breadcrumb}  ({len(chunk.text)} chars)")
        preview = chunk.text.replace("\n", " ")[:160]
        print(f"      {preview}{'...' if len(chunk.text) > 160 else ''}")


def main() -> int:
    verbose = "-v" in sys.argv or "--verbose" in sys.argv
    kb_dir = Path(__file__).resolve().parent.parent / "data" / "kb"

    print(f"Labelled: {len(IN_SCOPE)} in-scope, {len(OUT_OF_SCOPE)} out-of-scope")
    print("Comparing on the SAME questions eval_retrieval.py uses - see that")
    print("file if a number here looks wrong before assuming this script is.")

    reports = {}
    for label, chunk_fn in STRATEGIES.items():
        kb = KnowledgeBase(kb_dir=kb_dir, chunk_fn=chunk_fn)
        report = evaluate(kb, top_k=3, label=label)
        print_report(report, verbose=verbose)
        reports[label] = report

    baseline_label, candidate_label = STRATEGIES.keys()
    baseline, candidate = reports[baseline_label], reports[candidate_label]

    print(f"\n{'=' * 72}\nretrieval: candidate vs. baseline\n{'=' * 72}")
    for metric, fmt in (("precision_at_1", "{:.1%}"), ("recall_at_k", "{:.1%}"),
                        ("mrr", "{:.3f}"), ("rejection_rate", "{:.1%}")):
        b, c = baseline[metric], candidate[metric]
        verdict = "BETTER" if c > b else ("WORSE" if c < b else "tied")
        print(f"  {metric:<16} baseline {fmt.format(b):>7}   candidate {fmt.format(c):>7}   {verdict}")

    print(f"\n{'=' * 72}\nchunk shape\n{'=' * 72}")
    for label, chunk_fn in STRATEGIES.items():
        print_shape_report(label, chunk_shape_report(kb_dir, chunk_fn))

    print(f"\n{'=' * 72}")
    if candidate["precision_at_1"] > baseline["precision_at_1"]:
        print("  Candidate scored higher P@1 than the live default on this set.")
        print("  Still not a reason to switch on its own - see eval_retrieval.py's")
        print("  own caution about a 36-question labelled set, and re-run this")
        print("  after adding real documents before treating it as settled.")
    else:
        print("  Candidate did NOT beat the live default on P@1 here.")
        print("  Do not switch retriever.py's default chunker on the strength")
        print("  of this run.")
    print("=" * 72)

    pdf_arg = next((a for a in sys.argv if a.startswith("--pdf")), None)
    if pdf_arg:
        idx = sys.argv.index(pdf_arg)
        pdf_path = Path(sys.argv[idx + 1]) if "=" not in pdf_arg else Path(pdf_arg.split("=", 1)[1])
        if not pdf_path.exists():
            print(f"\n  --pdf: {pdf_path} not found")
            return 1
        show_pdf(pdf_path)

    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
