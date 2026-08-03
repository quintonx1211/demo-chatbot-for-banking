"""Intent classifier with confidence scoring.

This is the first layer of the hybrid architecture. High-confidence intents are
routed into deterministic scripted flows; everything else falls through to the
RAG-grounded LLM layer. The classifier is intentionally transparent - every
decision reports the score and the runner-up so it can be audited.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .textmodel import TfidfIndex, tokenize

# Confidence bands. Tuned against the utterance set below; the router treats
# them as the only policy knob it needs.
HIGH_CONFIDENCE = 0.55   # deterministic scripted flow
LOW_CONFIDENCE = 0.28    # below this the intent is not trusted at all


# Each intent lists example utterances (training data for the TF-IDF centroid)
# and optional regex "anchors". An anchor match is a strong signal that lifts
# the score, which is how a rule-based layer and a statistical one coexist.
INTENTS: dict[str, dict] = {
    "balance_inquiry": {
        "utterances": [
            "what is my account balance",
            "how much money do I have",
            "check my balance",
            "current balance please",
            "show me my available funds",
            "balance on my checking account",
            "how much is in my savings",
        ],
        "anchors": [r"\bbalance\b", r"how much .*(money|funds)"],
    },
    "block_card": {
        "utterances": [
            "block my card",
            "I lost my debit card",
            "my credit card was stolen",
            "freeze my card immediately",
            "someone stole my wallet block the card",
            "deactivate my card now",
            "I need to report my card lost",
        ],
        "anchors": [r"\b(block|freeze|deactivate|cancel)\b.*\bcard\b",
                    r"\bcard\b.*\b(lost|stolen|missing)\b",
                    r"\b(lost|stolen)\b.*\bcard\b"],
    },
    "loan_status": {
        "utterances": [
            "what is the status of my loan application",
            "check my loan status",
            "has my mortgage been approved",
            "any update on my home loan",
            "when will my loan be approved",
            "loan application progress",
        ],
        "anchors": [r"\bloan\b.*\bstatus\b", r"\bstatus\b.*\b(loan|mortgage)\b",
                    r"\b(loan|mortgage)\b.*\b(approved|application)\b"],
    },
    "transaction_history": {
        "utterances": [
            "show my recent transactions",
            "list the last payments on my account",
            "what did I spend last week",
            "recent activity on my account",
            "show me my last five transactions",
        ],
        "anchors": [r"\b(recent|last|latest)\b.*\btransaction", r"\bspend\b"],
    },
    "human_agent": {
        "utterances": [
            "I want to speak to a human",
            "connect me to an agent",
            "transfer me to a representative",
            "let me talk to a real person",
            "this is not helping give me support",
            "I need a customer service officer",
        ],
        "anchors": [r"\b(human|agent|representative|real person|someone)\b",
                    r"\bspeak (to|with)\b"],
    },
    "greeting": {
        "utterances": [
            "hello", "hi there", "good morning", "hey", "good evening",
        ],
        "anchors": [r"^\s*(hi|hello|hey|good (morning|afternoon|evening))\b"],
    },
    "goodbye": {
        "utterances": [
            "thanks that is all", "goodbye", "bye bye", "that's everything thanks",
        ],
        "anchors": [r"^\s*(bye|goodbye)\b", r"that('s| is) all"],
    },
    # Knowledge intents: recognised, but answered from the knowledge base by
    # the RAG layer rather than by a scripted flow.
    "knowledge_query": {
        "utterances": [
            "what are your fees for international transfers",
            "how do I reset my online banking password",
            "what documents do I need to open an account",
            "what are your branch opening hours",
            "how long does a wire transfer take",
            "how do I dispute a transaction",
            "what is the overdraft policy",
            "how do I set up direct debit",
            "what interest rate do you offer on savings",
        ],
        "anchors": [],
    },
}


@dataclass
class IntentPrediction:
    intent: str
    confidence: float
    runner_up: str | None = None
    runner_up_confidence: float = 0.0
    scores: dict[str, float] = field(default_factory=dict)

    @property
    def is_high_confidence(self) -> bool:
        return self.confidence >= HIGH_CONFIDENCE

    @property
    def is_unknown(self) -> bool:
        return self.confidence < LOW_CONFIDENCE


class IntentClassifier:
    def __init__(self, intents: dict[str, dict] | None = None) -> None:
        self.intents = intents or INTENTS
        # One TF-IDF document per utterance, so short intents aren't penalised
        # by being averaged into a single long centroid.
        self._labels: list[str] = []
        corpus: list[str] = []
        for name, spec in self.intents.items():
            for utterance in spec["utterances"]:
                self._labels.append(name)
                corpus.append(utterance)
        self._index = TfidfIndex(corpus)
        self._anchors = {
            name: [re.compile(p, re.IGNORECASE) for p in spec.get("anchors", [])]
            for name, spec in self.intents.items()
        }

    def predict(self, text: str) -> IntentPrediction:
        scores: dict[str, float] = {}

        # Statistical signal. A short utterance can tokenize to nothing (e.g.
        # "hi" is a stop word), in which case only the anchors below speak.
        if tokenize(text):
            similarities = self._index.similarities(text)
            # Max-pool per intent: the best-matching example utterance wins.
            for label, score in zip(self._labels, similarities):
                if score > scores.get(label, 0.0):
                    scores[label] = score

        # Anchor hits push a match over the deterministic threshold. A phrase
        # like "block card" is unambiguous even when the wording is unusual.
        for name, patterns in self._anchors.items():
            if any(p.search(text) for p in patterns):
                scores[name] = min(1.0, max(scores.get(name, 0.0), 0.62) + 0.15)

        if not scores:
            return IntentPrediction(intent="unknown", confidence=0.0)

        ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
        top_intent, top_score = ranked[0]
        runner_up, runner_up_score = (ranked[1] if len(ranked) > 1 else (None, 0.0))

        # Ambiguity penalty: two intents scoring alike means we are not really
        # confident in either, so we push the query toward the RAG layer.
        if runner_up and top_score - runner_up_score < 0.08:
            top_score *= 0.75

        if top_score < LOW_CONFIDENCE:
            return IntentPrediction(
                intent="unknown", confidence=round(top_score, 3),
                runner_up=top_intent, runner_up_confidence=round(top_score, 3),
                scores={k: round(v, 3) for k, v in ranked[:4]},
            )

        return IntentPrediction(
            intent=top_intent,
            confidence=round(top_score, 3),
            runner_up=runner_up,
            runner_up_confidence=round(runner_up_score, 3),
            scores={k: round(v, 3) for k, v in ranked[:4]},
        )
