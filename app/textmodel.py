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

_TOKEN_RE = re.compile(r"[\w']+", re.UNICODE)

_STOPWORDS = {
    "a",
    "an",
    "the",
    "of",
    "to",
    "and",
    "or",
    "is",
    "are",
    "am",
    "be",
    "been",
    "was",
    "were",
    "in",
    "on",
    "at",
    "it",
    "this",
    "that",
    "there",
    "here",
    "for",
    "with",
    "from",
    "by",
    "as",
    "into",
    "about",
    "out",
    "up",
    "down",
    "if",
    "then",
    "than",
    "so",
    "but",
    "what",
    "how",
    "when",
    "where",
    "who",
    "why",
    "which",
    "much",
    "many",
    "do",
    "does",
    "did",
    "can",
    "could",
    "would",
    "will",
    "should",
    "need",
    "want",
    "get",
    "got",
    "have",
    "has",
    "had",
    "any",
    "some",
    "all",
    "today",
    "tomorrow",
    "yesterday",
    "now",
    "currently",
    "still",
    "yet",
    "already",
    "soon",
    "recently",
    "i",
    "me",
    "my",
    "mine",
    "we",
    "us",
    "our",
    "you",
    "your",
    "they",
    "them",
    "please",
    "hi",
    "hello",
    "hey",
    "thanks",
    "thank",
    "là",
    "và",
    "của",
    "cho",
    "với",
    "các",
    "những",
    "một",
    "này",
    "đó",
    "thì",
    "mà",
    "ở",
    "tại",
    "trong",
    "ngoài",
    "khi",
    "nếu",
    "hoặc",
    "hay",
    "được",
    "bị",
    "có",
    "không",
    "chưa",
    "đã",
    "sẽ",
    "đang",
    "rồi",
    "tôi",
    "mình",
    "bạn",
    "em",
    "anh",
    "chị",
    "chúng",
    "ta",
    "họ",
    "bao",
    "nhiêu",
    "gì",
    "nào",
    "sao",
    "đâu",
    "ai",
    "thế",
    "vậy",
    "làm",
    "muốn",
    "cần",
    "phải",
    "nên",
    "để",
    "về",
    "từ",
    "đến",
    "theo",
    "xin",
    "chào",
    "cảm",
    "ơn",
    "ạ",
    "nhé",
    "vui",
    "lòng",
}

_SUFFIXES = ("ings", "ing", "ies", "es", "s", "ed")

BIGRAM_COVERAGE_WEIGHT = 2.0

NUMERIC_TERM_WEIGHT = 0.15


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
        self._default_idf = math.log(n_docs + 1) + 1.0
        self._doc_vectors = [self._vectorize(terms) for terms in self._doc_terms]

        self._doc_lengths = [sum(terms.values()) for terms in self._doc_terms]
        self._avg_length = (
            sum(self._doc_lengths) / len(self._doc_lengths)
            if self._doc_lengths
            else 0.0
        )
        self._bm25_idf = {
            term: math.log(1.0 + (n_docs - freq + 0.5) / (freq + 0.5))
            for term, freq in doc_freq.items()
        }

    def bm25_scores(self, text: str, k1: float = 1.2, b: float = 0.75) -> list[float]:
        """Okapi BM25 over the corpus.

        Complements cosine similarity rather than replacing it: BM25 saturates
        term frequency and normalises by document length, so a long passage no
        longer wins simply by containing a query term many times, and a short
        passage that is squarely on topic can outrank a long one that mentions
        the topic in passing. On this corpus, where chunk lengths vary from ~200
        to ~700 characters, that length normalisation is the point.
        """
        query_terms = Counter(tokenize(text))
        if not query_terms or not self._doc_lengths:
            return [0.0] * len(self.documents)

        scores = []
        for terms, length in zip(self._doc_terms, self._doc_lengths):
            score = 0.0
            for term, query_count in query_terms.items():
                frequency = terms.get(term, 0)
                if not frequency:
                    continue
                idf = self._bm25_idf.get(term, 0.0)
                denominator = frequency + k1 * (
                    1.0 - b + b * length / (self._avg_length or 1.0)
                )
                score += idf * query_count * frequency * (k1 + 1.0) / denominator
            scores.append(score)
        return scores

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
        """Vectorise a query, letting unknown terms dilute the match.

        `_vectorize` drops out-of-vocabulary terms, which is right for indexing
        a document but badly wrong for a query. It made a question of five
        content words, four of them unknown, collapse to the single word the
        corpus recognised - and then match an example containing only that word
        at cosine 1.00. "Do you offer crop insurance for vineyards in Portugal"
        scored a perfect match against "do you have any offers for me".

        Unknown terms are kept here at the ceiling idf. They can never match
        anything, so they contribute nothing to the numerator, but they do
        contribute to the norm - which is exactly the intended effect: a query
        mostly made of words the corpus has never seen should match weakly.
        """
        terms = Counter(featurize(text))
        vector = {
            term: (1.0 + math.log(count)) * self._idf.get(term, self._default_idf)
            for term, count in terms.items()
        }
        norm = math.sqrt(sum(w * w for w in vector.values()))
        if norm == 0.0:
            return {}
        return {term: w / norm for term, w in vector.items() if term in self._idf}

    def coverages(self, text: str) -> list[float]:
        """Per-document share of the query's information that the document contains.

        Cosine similarity alone is a poor relevance gate on a corpus of long
        passages: a query shares few terms with any single passage, so real
        matches score low in absolute terms while an off-topic query still
        scores non-zero off incidental words. Coverage asks the sharper
        question - of the meaningful terms in this question, how much does
        this passage actually address? - and is comparable across queries.
        """
        tokens = tokenize(text)
        query_terms = set(tokens)
        if not query_terms:
            return [0.0] * len(self.documents)

        weights = {
            t: (
                NUMERIC_TERM_WEIGHT
                if t.isdigit()
                else self._idf.get(t, self._default_idf)
            )
            for t in query_terms
        }
        for pair in set(bigrams(tokens)):
            if any(part.isdigit() for part in pair.split("_")):
                weights[pair] = NUMERIC_TERM_WEIGHT
            else:
                weights[pair] = (
                    self._idf.get(pair, self._default_idf) * BIGRAM_COVERAGE_WEIGHT
                )
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
            if len(query) < len(doc_vector):
                scores.append(sum(w * doc_vector.get(t, 0.0) for t, w in query.items()))
            else:
                scores.append(sum(w * query.get(t, 0.0) for t, w in doc_vector.items()))
        return scores
