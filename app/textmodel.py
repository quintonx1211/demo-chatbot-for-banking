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

# Unicode-aware, and it has to be. `[a-z0-9']+` silently shredded every
# non-ASCII script: "phí thường niên thẻ tín dụng" tokenised to
# ['ph', 'th', 'ng', 'ni', 'n', 'th', 't', 'n', 'd', 'ng'] - fragments split at
# each accented character, matching nothing and carrying no meaning. A question
# asked in Vietnamese retrieved zero passages and escalated, which looks like
# the knowledge base is missing rather than like the tokeniser is broken.
#
# `\w` under Python's default Unicode semantics keeps letters, digits and
# underscore in any script, so ASCII behaviour is unchanged.
_TOKEN_RE = re.compile(r"[\w']+", re.UNICODE)

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
    # Temporal scaffolding. Same category as the question words above: they
    # appear in a question without being what it is about. "today" was letting
    # "what is the weather in Hanoi today" clear the relevance floor against a
    # document whose provenance note happens to say "a rate correct today may
    # not be correct next quarter" - one shared word, no shared subject.
    "today", "tomorrow", "yesterday", "now", "currently", "still", "yet",
    "already", "soon", "recently",
    # pronouns and pleasantries
    "i", "me", "my", "mine", "we", "us", "our", "you", "your", "they", "them",
    "please", "hi", "hello", "hey", "thanks", "thank",
    # Vietnamese. The same job as the English list above and for the same
    # reason: without it "là", "của" and "bao nhiêu" appear in nearly every
    # question, so an off-topic query still shares terms with every passage
    # and clears the coverage floor on function words alone.
    "là", "và", "của", "cho", "với", "các", "những", "một", "này", "đó",
    "thì", "mà", "ở", "tại", "trong", "ngoài", "khi", "nếu", "hoặc", "hay",
    "được", "bị", "có", "không", "chưa", "đã", "sẽ", "đang", "rồi",
    "tôi", "mình", "bạn", "em", "anh", "chị", "chúng", "ta", "họ",
    "bao", "nhiêu", "gì", "nào", "sao", "đâu", "ai", "thế", "vậy",
    "làm", "muốn", "cần", "phải", "nên", "để", "về", "từ", "đến", "theo",
    "xin", "chào", "cảm", "ơn", "ạ", "nhé", "vui", "lòng",
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

        # BM25 state. Kept alongside the cosine vectors rather than in a second
        # class because both score the same tokenisation of the same corpus;
        # duplicating that would be two places to drift.
        self._doc_lengths = [sum(terms.values()) for terms in self._doc_terms]
        self._avg_length = (
            sum(self._doc_lengths) / len(self._doc_lengths) if self._doc_lengths else 0.0
        )
        # BM25's idf is a different curve from the tf-idf one above: it is
        # probabilistic, and goes slightly negative for terms in most documents,
        # which is the behaviour that makes it discount near-stopwords properly.
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
        # Only known terms can contribute to a dot product; the rest have done
        # their job by inflating the norm above.
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
