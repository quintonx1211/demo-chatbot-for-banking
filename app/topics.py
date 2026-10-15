"""Clustering the questions the assistant could not answer.

The client named this as a required dashboard metric, and it is the one that
pays for itself: every escalation is a question the knowledge base does not
cover, and the clusters are a ranked list of what to write next. A count of
escalations tells you the assistant is failing; a cluster tells you what to do
about it.

Similarity is Jaccard overlap of stemmed content words, not the idf-weighted
cosine the retriever uses. That was the first attempt and it failed for an
instructive reason: idf is computed over the unanswered questions themselves,
so a word appearing in every member of a cluster - "insurance", across five
people asking about travel insurance - gets an idf near zero and contributes
almost nothing. The very term that defines the cluster is the one the metric
discounts. Worse, at a threshold low enough to group those five, "crop
insurance for vineyards" merged in with them too.

Jaccard has no such inversion: shared words count, and a question sharing one
generic noun stays separate from one sharing three specific ones.

No LLM call. This runs on every dashboard refresh in a few milliseconds.
"""

from __future__ import annotations

from dataclasses import dataclass

from .session import Session
from .textmodel import tokenize

SIMILARITY_THRESHOLD = 0.30

MIN_CLUSTER_SIZE = 2

_UNRESOLVED = {"escalation", "escalation_offered"}


@dataclass
class Topic:
    label: str
    size: int
    questions: list[str]
    reasons: list[str]

    def to_dict(self) -> dict:
        return {
            "label": self.label,
            "size": self.size,
            "questions": self.questions[:6],
            "reasons": self.reasons[:3],
        }


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _label(questions: list[str]) -> str:
    """Name a cluster by the content words its members share most often."""
    counts: dict[str, int] = {}
    for question in questions:
        for term in set(tokenize(question)):
            counts[term] = counts.get(term, 0) + 1
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    top = [term for term, count in ranked[:3] if count > 1] or [
        term for term, _ in ranked[:3]
    ]
    return " / ".join(top) if top else "unclassified"


def cluster_unresolved(sessions: list[Session]) -> dict:
    """Group everything the assistant failed to answer, most common first."""
    entries: list[tuple[str, str]] = []
    for session in sessions:
        for entry in session.audit:
            if entry.route in _UNRESOLVED and entry.utterance.strip():
                entries.append((entry.utterance.strip(), entry.note or entry.route))

    tally: dict[str, list[str]] = {}
    for text, reason in entries:
        tally.setdefault(text.lower(), []).append(reason)

    unique = list(tally.keys())
    if not unique:
        return {"topics": [], "singletons": [], "total_unresolved": 0}

    token_sets = [set(tokenize(q)) for q in unique]
    assigned: list[int | None] = [None] * len(unique)
    clusters: list[list[int]] = []

    for i in range(len(unique)):
        if assigned[i] is not None:
            continue
        members = [i]
        assigned[i] = len(clusters)
        for j in range(i + 1, len(unique)):
            if assigned[j] is not None:
                continue
            if _jaccard(token_sets[i], token_sets[j]) >= SIMILARITY_THRESHOLD:
                members.append(j)
                assigned[j] = len(clusters)
        clusters.append(members)

    topics, singletons = [], []
    for members in clusters:
        questions = [unique[j] for j in members]
        size = sum(len(tally[q]) for q in questions)
        reasons = sorted({r for q in questions for r in tally[q]})
        topic = Topic(
            label=_label(questions), size=size, questions=questions, reasons=reasons
        )
        (topics if len(questions) >= MIN_CLUSTER_SIZE else singletons).append(topic)

    topics.sort(key=lambda t: -t.size)
    singletons.sort(key=lambda t: -t.size)

    return {
        "topics": [t.to_dict() for t in topics],
        "singletons": [t.to_dict() for t in singletons[:8]],
        "total_unresolved": len(entries),
        "distinct_questions": len(unique),
    }
