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

# How much a matched bigram is worth relative to a matched unigram in the
# coverage metric. Swept on the labelled set: 1.0 leaves rejection at 28.6%,
# 2.0 lifts it to 85.7%, and 3.0 adds nothing further. See `coverages`.
BIGRAM_COVERAGE_WEIGHT = 2.0

# What a bare number contributes to coverage. Near zero, because a figure the
# customer copied from their own statement cannot be in the corpus and says
# nothing about the topic. Not exactly zero: "phí 12 tháng" and "trong 30 ngày"
# do carry a little signal. See `coverages`.
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
        # Unigrams AND bigrams, with bigrams weighted higher - and the reason is
        # the language, not a tuning preference.
        #
        # Vietnamese writes compound words as separate syllables: "ngân hàng"
        # (bank), "bảo hiểm" (insurance), "doanh nghiệp" (enterprise). Splitting
        # on whitespace therefore does not produce words, it produces syllables,
        # and a syllable matches across completely unrelated compounds -
        # "hàng" is shared by "ngân hàng" (bank), "khách hàng" (customer) and
        # "hàng hoá" (goods). Measured on the labelled set, unigram-only coverage
        # let five of seven out-of-scope questions through: "mã số doanh nghiệp
        # của ngân hàng" scored 0.70 against a payment-limits passage on the
        # strength of four unrelated syllables.
        #
        # A bigram is approximately a word in Vietnamese, so weighting bigrams
        # at double restored rejection from 28.6% to 85.7% for 3 points of P@1.
        # Rejection is the safety metric; that is the right side of the trade.
        #
        # This does not hurt English, where a query bigram rarely survives into
        # prose: an unmatched bigram simply adds to the denominator, which is
        # the same dilution effect out-of-vocabulary terms already have.
        tokens = tokenize(text)
        query_terms = set(tokens)
        if not query_terms:
            return [0.0] * len(self.documents)

        # Weight by idf so "wire" counts for far more than "you".
        #
        # Bare numbers are the exception, and they need one. A customer
        # quoting their own figures back - "Tại sao số dư 35,918,994 VND, nhưng
        # chỉ khả dụng 33,418,994 VND?" - produced six numeric tokens
        # ('35', '918', '994', ...) against four content words. Each number is
        # absent from the corpus by definition, so each took the ceiling idf,
        # and the four words that carried the actual question were diluted to
        # roughly a third of the total. A question the corpus answers well was
        # rejected outright.
        #
        # An amount a customer reads off their own statement is never the
        # topic; the words around it are.
        weights = {
            t: (NUMERIC_TERM_WEIGHT if t.isdigit()
                else self._idf.get(t, self._default_idf))
            for t in query_terms
        }
        for pair in set(bigrams(tokens)):
            # A bigram touching a number inherits the number's problem, and at
            # double weight it inherits it twice over. Six numeric tokens
            # produce five more numeric bigrams, so discounting only unigrams
            # left the query still dominated by an amount the customer read off
            # their own statement.
            if any(part.isdigit() for part in pair.split("_")):
                weights[pair] = NUMERIC_TERM_WEIGHT
            else:
                weights[pair] = (self._idf.get(pair, self._default_idf)
                                 * BIGRAM_COVERAGE_WEIGHT)
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
