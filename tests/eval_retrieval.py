"""Retrieval quality harness. Run: python tests/eval_retrieval.py [-v]

Exists so changes to the retrieval pipeline can be argued from numbers instead
of asserted. Every question below is labelled with the passage heading that
should answer it; the harness reports how often retrieval puts that passage
first, how often it appears at all, and - separately - whether out-of-scope
questions are correctly rejected.

That second axis matters as much as the first here. A retriever tuned only for
recall will happily surface a loosely-related passage for a question the corpus
does not cover, and in this application that turns into a grounded-looking
answer to something the bank never documented. Rejection precision is a safety
property, not a metric to trade away for recall.
"""

from __future__ import annotations

import sys

# The Windows console defaults to cp1252, which cannot encode Vietnamese. Now
# that the assistant answers in Vietnamese, printing a reply raised
# UnicodeEncodeError and took the whole test run down - a test suite that dies
# on its own output reports nothing about the code it was meant to check.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.retriever import KnowledgeBase

# (question, doc_id, heading that should answer it). doc_id disambiguates -
# all 4 documents share the same template (Định vị sản phẩm / Ưu đãi chào
# mừng / Chương trình hoàn tiền và đặc quyền / Phí thường niên), so heading
# text alone is not a unique label.
IN_SCOPE: list[tuple[str, str, str]] = [
    # classic.md - KB-CARD-CLASSIC
    ("thẻ Classic phù hợp với ai", "KB-CARD-CLASSIC", "Định vị sản phẩm"),
    ("ưu đãi chào mừng của thẻ Classic là gì", "KB-CARD-CLASSIC", "Ưu đãi chào mừng"),
    ("thẻ Classic hoàn tiền bao nhiêu phần trăm cho ăn uống", "KB-CARD-CLASSIC", "Chương trình hoàn tiền và đặc quyền"),
    ("phí thường niên thẻ Classic là bao nhiêu", "KB-CARD-CLASSIC", "Phí thường niên"),

    # platinum.md - KB-CARD-PLATINUM
    ("thẻ Platinum dành cho ai", "KB-CARD-PLATINUM", "Định vị sản phẩm"),
    ("mở thẻ Platinum có ưu đãi chào mừng gì", "KB-CARD-PLATINUM", "Ưu đãi chào mừng"),
    ("thẻ Platinum hoàn tiền bao nhiêu cho di chuyển", "KB-CARD-PLATINUM", "Chương trình hoàn tiền và đặc quyền"),
    ("phí thường niên thẻ Platinum là bao nhiêu", "KB-CARD-PLATINUM", "Phí thường niên"),

    # signature.md - KB-CARD-SIGNATURE
    ("thẻ Signature dành cho khách hàng như thế nào", "KB-CARD-SIGNATURE", "Định vị sản phẩm"),
    ("thẻ Signature có tặng gì khi mở thẻ không", "KB-CARD-SIGNATURE", "Ưu đãi chào mừng"),
    ("thẻ Signature có tự chọn danh mục hoàn tiền không", "KB-CARD-SIGNATURE", "Chương trình hoàn tiền và đặc quyền"),
    ("phí thường niên thẻ Signature là bao nhiêu", "KB-CARD-SIGNATURE", "Phí thường niên"),

    # infinite.md - KB-CARD-INFINITE
    ("thẻ Infinite phù hợp với phân khúc khách hàng nào", "KB-CARD-INFINITE", "Định vị sản phẩm"),
    ("mở thẻ Infinite có được tặng thẻ kim loại không", "KB-CARD-INFINITE", "Ưu đãi chào mừng"),
    ("thẻ Infinite có ưu đãi golf không", "KB-CARD-INFINITE", "Chương trình hoàn tiền và đặc quyền"),
    ("phí thường niên thẻ Infinite là bao nhiêu", "KB-CARD-INFINITE", "Phí thường niên"),
]

OUT_OF_SCOPE: list[str] = [
    "ngân hàng có bán bảo hiểm nhân thọ không",
    "hôm nay Hà Nội thời tiết thế nào",
    "ngân hàng có cho vay mua đất nông nghiệp trồng nho ở Bồ Đào Nha không",
    "đội nào vô địch world cup",
    "mã số doanh nghiệp của ngân hàng là gì",
    "tôi mua tiền điện tử trên app được không",
    "ngân hàng có dịch vụ két sắt an toàn không",
]


def _matches(doc_id: str, heading: str, expected_doc: str, expected_heading: str) -> bool:
    return (doc_id == expected_doc
            and heading.lower().startswith(expected_heading.lower()[:24]))


def evaluate(kb: KnowledgeBase, top_k: int = 3, label: str = "",
             cases: list[tuple[str, str, str]] | None = None) -> dict:
    cases = cases if cases is not None else IN_SCOPE
    hits_at_1 = hits_at_k = 0
    reciprocal_ranks = 0.0
    misses: list[tuple[str, str, list[str]]] = []

    for question, expected_doc, expected_heading in cases:
        results = kb.search(question, top_k=top_k)
        citations = [f"{r.passage.doc_id} · {r.passage.heading}" for r in results]
        rank = next((i for i, r in enumerate(results, start=1)
                     if _matches(r.passage.doc_id, r.passage.heading,
                                expected_doc, expected_heading)), None)
        if rank == 1:
            hits_at_1 += 1
        if rank is not None:
            hits_at_k += 1
            reciprocal_ranks += 1.0 / rank
        else:
            misses.append((question, f"{expected_doc} · {expected_heading}", citations))

    rejected = 0
    false_positives: list[tuple[str, str]] = []
    for question in OUT_OF_SCOPE:
        results = kb.search(question, top_k=top_k)
        if not results:
            rejected += 1
        else:
            false_positives.append((question, results[0].passage.heading))

    total = len(cases) or 1
    return {
        "label": label,
        "precision_at_1": hits_at_1 / total,
        "recall_at_k": hits_at_k / total,
        "mrr": reciprocal_ranks / total,
        "rejection_rate": rejected / (len(OUT_OF_SCOPE) or 1),
        "misses": misses,
        "false_positives": false_positives,
        "top_k": top_k,
        "n": total,
    }


def print_report(report: dict, verbose: bool = False) -> None:
    print(f"\n{'=' * 72}")
    print(f"{report['label'] or 'retrieval'}   (n={report['n']}, top_k={report['top_k']})")
    print("=" * 72)
    print(f"  P@1         {report['precision_at_1']:6.1%}   right passage ranked first")
    print(f"  Recall@{report['top_k']}    {report['recall_at_k']:6.1%}   right passage retrieved at all")
    print(f"  MRR         {report['mrr']:6.3f}")
    print(f"  Rejection   {report['rejection_rate']:6.1%}   out-of-scope correctly returning nothing")

    if verbose and report["misses"]:
        print(f"\n  Missed ({len(report['misses'])}):")
        for question, expected, got in report["misses"]:
            print(f"    - {question}")
            print(f"        want: {expected}")
            print(f"        got : {got or '(nothing)'}")

    if verbose and report["false_positives"]:
        print(f"\n  False positives ({len(report['false_positives'])}):")
        for question, heading in report["false_positives"]:
            print(f"    - {question}  ->  {heading}")


class _RerankedKB:
    """Adapter presenting the reranked pipeline behind the same `search` call.

    Lets the harness measure the reranking path without duplicating the metric
    code, and without the metrics knowing which pipeline produced the results.
    """

    def __init__(self, kb: KnowledgeBase) -> None:
        self.kb = kb
        self.stats = kb.stats

    def search(self, query: str, top_k: int = 3):
        from app.llm import rerank
        candidates = self.kb.search(query, top_k=rerank.CANDIDATE_POOL,
                                    gate=rerank.RECALL_GATE)
        results, _ = rerank.rerank(query, candidates, top_k=top_k)
        return results


def main() -> int:
    verbose = "-v" in sys.argv or "--verbose" in sys.argv
    with_rerank = "--rerank" in sys.argv

    kb = KnowledgeBase()
    print(f"Corpus   : {kb.stats['passages']} passages / {kb.stats['documents']} documents")
    print(f"Labelled : {len(IN_SCOPE)} in-scope, {len(OUT_OF_SCOPE)} out-of-scope")
    print_report(evaluate(kb, top_k=3, label="lexical pipeline"), verbose=verbose)

    if with_rerank:
        from app import llm
        from app.llm import rerank

        info = llm.describe()
        if info["mode"] != "live":
            print(f"\n  --rerank needs a provider: {info['detail']}")
            return 1
        print(f"\n  Reranking with {info['provider']} / {info['model']} "
              f"(pool={rerank.CANDIDATE_POOL}, gate={rerank.RECALL_GATE}, "
              f"min_score={rerank.MIN_SCORE})")
        print("  This makes one API call per question - it will take a minute.")
        print_report(
            evaluate(_RerankedKB(kb), top_k=3, label="+ LLM reranking"),
            verbose=verbose,
        )
    else:
        print("\n  Pass --rerank (with a provider configured) to measure the "
              "reranking stage.")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
