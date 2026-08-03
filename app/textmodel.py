"""Lightweight TF-IDF vectoriser shared by the NLU classifier and the RAG retriever.

Deliberately dependency-free: the demo must run with a bare Python install so a
reviewer can start it without provisioning anything. The maths is the standard
ltc-weighted cosine similarity used by any sklearn TfidfVectorizer.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from typing import Iterable, Sequence

_TOKEN_RE = re.compile(r"[a-z0-9']+")

# Stop list covering both function words and question scaffolding. Dropping
# "what / how / much / can" matters more than it looks: they appear in almost
# every customer question but carry no topic, and left in they inflate the
# coverage score of off-topic queries against arbitrary passages.
_STOPWORDS = {
    # articles, copulas, prepositions, conjunctions
    "a", "an", "the", "of", "to", "and", "or", "is", "are", "am", "be", "been",
    "was", "were", "in", "on", "at", "it", "this", "that", "there", "here",
    "for", "with", "from", "by", "as", "into", "about", "out", "up", "down",
    "if", "then", "than", "so", "but",
    # question scaffolding
    "what", "how", "when", "where", "who", "why", "which", "much", "many",
    "do", "does", "did", "can", "could", "would", "will", "should", "need",
    "want", "get", "got", "have", "has", "had", "any", "some", "all",
    # pronouns and pleasantries
    "i", "me", "my", "mine", "we", "us", "our", "you", "your", "they", "them",
    "please", "hi", "hello", "hey", "thanks", "thank",
}

# Very small stemmer: folds the plural/gerund forms that show up in the demo
# corpus so "payments"/"payment" and "blocking"/"block" share a term.
_SUFFIXES = ("ings", "ing", "ies", "es", "s", "ed")


def _stem(token: str) -> str:
    if len(token) <= 3:
        return token
    for suffix in _SUFFIXES:
        if token.endswith(suffix) and len(token) - len(suffix) >= 3:
            base = token[: -len(suffix)]
            if suffix == "ies":
                base += "y"
            return base
    return token


def tokenize(text: str) -> list[str]:
    tokens = [_stem(t) for t in _TOKEN_RE.findall(text.lower())]
    return [t for t in tokens if t not in _STOPWORDS]


def bigrams(tokens: Sequence[str]) -> list[str]:
    return [f"{a}_{b}" for a, b in zip(tokens, tokens[1:])]


def featurize(text: str) -> list[str]:
    tokens = tokenize(text)
    return tokens + bigrams(tokens)


class TfidfIndex:
    """Fits on a fixed corpus, then scores free-text queries against it."""

    def __init__(self, documents: Iterable[str]) -> None:
        self.documents = list(documents)
        self._doc_terms = [Counter(featurize(doc)) for doc in self.documents]

        n_docs = max(len(self.documents), 1)
        doc_freq: Counter[str] = Counter()
        for terms in self._doc_terms:
            doc_freq.update(terms.keys())
        self._idf = {
            term: math.log((n_docs + 1) / (freq + 1)) + 1.0
            for term, freq in doc_freq.items()
        }
        # A term absent from the whole corpus is maximally informative - and by
        # definition covered by no document. Weighting it at the ceiling is what
        # makes an out-of-scope question ("vineyards") fall below the coverage
        # threshold instead of scoring off its incidental common words.
        self._default_idf = math.log(n_docs + 1) + 1.0
        self._doc_vectors = [self._vectorize(terms) for terms in self._doc_terms]

    def _vectorize(self, terms: Counter[str]) -> dict[str, float]:
        vector = {
            term: (1.0 + math.log(count)) * self._idf.get(term, 0.0)
            for term, count in terms.items()
            if self._idf.get(term, 0.0) > 0.0
        }
        norm = math.sqrt(sum(w * w for w in vector.values()))
        if norm == 0.0:
            return {}
        return {term: w / norm for term, w in vector.items()}

    def vectorize_query(self, text: str) -> dict[str, float]:
        return self._vectorize(Counter(featurize(text)))

    def coverages(self, text: str) -> list[float]:
        """Per-document share of the query's information that the document contains.

        Cosine similarity alone is a poor relevance gate on a corpus of long
        passages: a query shares few terms with any single passage, so real
        matches score low in absolute terms while an off-topic query still
        scores non-zero off incidental words. Coverage asks the sharper
        question - of the meaningful terms in this question, how much does
        this passage actually address? - and is comparable across queries.
        """
        # Unigrams only. Query bigrams almost never survive verbatim into prose,
        # so including them would depress every score toward the floor and
        # destroy the discrimination this metric exists to provide.
        query_terms = set(tokenize(text))
        if not query_terms:
            return [0.0] * len(self.documents)

        # Weight by idf so "wire" counts for far more than "you".
        weights = {t: self._idf.get(t, self._default_idf) for t in query_terms}
        total = sum(weights.values())
        if total == 0.0:
            return [0.0] * len(self.documents)

        return [
            sum(w for t, w in weights.items() if t in doc_terms) / total
            for doc_terms in self._doc_terms
        ]

    def similarities(self, text: str) -> list[float]:
        """Cosine similarity of `text` against every document, in corpus order."""
        query = self.vectorize_query(text)
        if not query:
            return [0.0] * len(self.documents)
        scores = []
        for doc_vector in self._doc_vectors:
            # Iterate the shorter vector; both are already L2-normalised.
            if len(query) < len(doc_vector):
                scores.append(sum(w * doc_vector.get(t, 0.0) for t, w in query.items()))
            else:
                scores.append(sum(w * query.get(t, 0.0) for t, w in doc_vector.items()))
        return scores
