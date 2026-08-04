"""Retrieval quality harness. Run: python eval_retrieval.py [-v]

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

from app.retriever import KnowledgeBase

# (question, heading that should answer it). Matched case-insensitively on a
# prefix so the labels survive small wording edits to the corpus.
IN_SCOPE: list[tuple[str, str]] = [
    # cards.md
    ("my card was stolen last night what do I do", "Reporting a lost or stolen card"),
    ("am I liable for purchases someone made with my stolen card", "Reporting a lost or stolen card"),
    ("how long until a new card arrives", "Replacement cards"),
    ("do I have to pay for a replacement card", "Replacement cards"),
    ("how much can I take out of an ATM in one day", "Card limits"),
    ("what does it cost to pay in euros", "Foreign transaction fees"),
    ("is there a fee for using my card overseas", "Foreign transaction fees"),
    ("when does contactless ask for a PIN", "Contactless payments"),

    # loans.md
    ("how long does a mortgage application take to decide", "Application processing times"),
    ("how quickly will I hear back about a personal loan", "Application processing times"),
    ("what does in review mean on my application", "Application statuses"),
    ("can I reapply after being declined", "Application statuses"),
    ("what paperwork do I need for a personal loan", "Required documents"),
    ("I am self employed what documents are required", "Required documents"),
    ("what rate would I get on a car loan", "Interest rates"),
    ("can I pay my loan off early without a penalty", "Early repayment"),

    # accounts-and-fees.md
    ("what do I need to bring to open an account", "Opening an account"),
    ("how do I avoid the monthly account fee", "Monthly maintenance fees"),
    ("what happens if I go overdrawn", "Overdraft"),
    ("how much is the overdraft charge", "Overdraft"),
    ("what does an international wire cost", "Transfers"),
    ("how long does a transfer to another bank take", "Transfers"),
    ("can I get an old statement from three years ago", "Statements"),
    ("are you open on saturday", "Branch and support hours"),

    # digital-and-security.md
    ("I forgot my online banking password", "Resetting an online banking password"),
    ("my account is locked after too many login attempts", "Resetting an online banking password"),
    ("why am I being asked for a code on a new laptop", "Registering a new device"),
    ("someone charged my card and I did not authorise it", "Disputing a transaction"),
    ("how long do I have to raise a dispute", "Disputing a transaction"),
    ("I got a suspicious email claiming to be from you", "Recognising phishing"),
    ("what is the mobile cheque deposit limit", "Mobile app"),

    # business-banking-schedule.docx — a converted Word document with three
    # heading levels and a fee table. The questions are worded the way a
    # customer would ask, not the way the document is written, which is where a
    # single lexical signal starts to fail.
    ("what do you charge to take card payments online", "Card acceptance pricing"),
    ("when does money from card sales reach my account", "Card acceptance pricing"),
    ("a customer disputed a payment what does that cost me", "Chargebacks and representment"),
    ("how long do I have to fight a chargeback", "Chargebacks and representment"),
    ("why is the bank holding back part of my settlement", "Chargebacks and representment"),
    ("how far in advance do I send the payroll file", "File submission windows"),
    ("can I run payroll on the same day", "File submission windows"),
    ("what happens when a salary payment bounces", "Failed and returned payments"),
    ("my business account has gone inactive how do I use it again", "Dormancy"),
    ("is there a penalty for closing the account early", "Closing an account"),

    # deposit-account-agreement.md — a Regulation DD style disclosure: a dense
    # fee table whose conditions live several paragraphs away. The hard cases
    # here are questions whose answer is a table row plus a qualifying rule.
    ("what is the fee for stopping a cheque", "Consumer deposit fee schedule"),
    ("how much do you charge for a cashier's cheque", "Consumer deposit fee schedule"),
    ("what counts as an average daily balance", "How the monthly maintenance fee is waived"),
    ("how do I get the $12 fee waived on my checking account", "How the monthly maintenance fee is waived"),
    ("which payment gets taken first if I don't have enough money", "Order in which items are paid"),
    ("am I charged if I'm only a few dollars overdrawn", "Overdraft coverage and the de minimis rule"),
    ("when can I use money from a cheque I deposited", "Funds availability"),
    ("why has my deposit been put on hold", "Funds availability"),
    ("how is interest on my savings worked out", "Interest and how it is calculated"),
    ("if I have fifty thousand saved what rate do I get", "Interest and how it is calculated"),

    # complaints-and-regulatory.md — process and rights, where the answer is a
    # deadline and the question rarely uses the document's vocabulary.
    ("how do I make a complaint and how long will it take", "Raising a complaint"),
    ("I'm unhappy with how you handled my complaint, what now",
     "If the customer is not satisfied with the outcome"),
    ("will I get my money back while you look into the transfer",
     "Electronic transfer errors"),
    ("someone used my card, how much am I on the hook for",
     "Liability for unauthorised electronic transfers"),
    ("do I have to pay a credit card charge I'm disputing",
     "Billing errors on credit accounts"),
    ("can you delete all my data", "Privacy and data rights"),
]

# Questions the corpus genuinely does not cover. Retrieval must return nothing,
# so the router escalates rather than answering from a loosely-related passage.
OUT_OF_SCOPE: list[str] = [
    "do you offer crop insurance for vineyards",
    "what is the weather in Hanoi today",
    "can I trade forex through you",
    "who won the world cup",
    "do you sell life insurance policies",
    "what is your company registration number",
    "can I buy cryptocurrency in the app",
    # "do you provide business payroll services" was here until the business
    # banking schedule was added to the corpus, which made it a question the
    # corpus genuinely answers. A labelled set is only valid against the corpus
    # it was written for — re-check it whenever documents are added.
]


def _matches(heading: str, expected: str) -> bool:
    return heading.lower().startswith(expected.lower()[:24])


def evaluate(kb: KnowledgeBase, top_k: int = 3, label: str = "",
             cases: list[tuple[str, str]] | None = None) -> dict:
    cases = cases if cases is not None else IN_SCOPE
    hits_at_1 = hits_at_k = 0
    reciprocal_ranks = 0.0
    misses: list[tuple[str, str, list[str]]] = []

    for question, expected in cases:
        headings = [r.passage.heading for r in kb.search(question, top_k=top_k)]
        rank = next((i for i, h in enumerate(headings, start=1)
                     if _matches(h, expected)), None)
        if rank == 1:
            hits_at_1 += 1
        if rank is not None:
            hits_at_k += 1
            reciprocal_ranks += 1.0 / rank
        else:
            misses.append((question, expected, headings))

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
        print("  This makes one API call per question — it will take a minute.")
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
