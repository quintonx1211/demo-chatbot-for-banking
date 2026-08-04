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
from . import policy as _policy

HIGH_CONFIDENCE = _policy.current.high_confidence   # deterministic scripted flow
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
        # Anchored on possession, not on the word "balance". A bare \bbalance\b
        # also fires on "what counts as an average daily balance?", which is a
        # policy question about how balances are calculated, not a request to
        # see one — and routing it here sends the customer into an identity
        # check for information that needs no identity at all.
        "anchors": [r"\b(my|our)\b[^?.]{0,24}\bbalance",
                    r"\bbalance\b[^?.]{0,20}\b(my|our)\b",
                    r"^\s*(check|show|what.s)\b[^?.]{0,16}\bbalance",
                    r"how much (money|funds) do i have"],
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
    "account_summary": {
        "utterances": [
            "who am I",
            "what are my details",
            "what accounts and cards do I hold with you",
            "what products do I hold",
            "tell me about my profile",
            "what is my customer number",
        ],
        "anchors": [r"(?<![a-z])who am i(?![a-z])", r"my (details|profile)(?![a-z])",
                    r"what .*(accounts?|products?) do i (have|hold)"],
    },
    "activate_card": {
        "utterances": [
            "how do I activate my new card",
            "my new card arrived how do I start using it",
            "I need to activate my debit card",
            "activate card",
            "my card is not working yet",
            "the card you sent me is inactive",
        ],
        "anchors": [r"activat(e|ing|ion)",
                    r"new card[^?.]{0,24}(start|use|using|work)",
                    r"card[^?.]{0,20}(not|isn.t) (working|active|activated)"],
    },
    "card_offers": {
        "utterances": [
            "do you have any offers for me",
            "what deals are available on my card",
            "can I get a better card",
            "is there a card with no foreign fees",
            "am I eligible for an upgrade",
            "any promotions for me",
        ],
        # "offer" as a noun the customer wants to receive, never as the verb
        # in "do you offer travel insurance?" - that asks what the bank sells
        # and belongs in the knowledge base. Same mistake as matching
        # "someone" for a handoff: the word is not the intent, the grammar
        # around it is.
        "anchors": [
            r"(offers?|deals?|promotions?|rewards?)[^?.]{0,20}"
            r"(for me|available|on my|i can get)",
            r"any[^?.]{0,12}(offers?|deals?|promotions?)",
            r"(upgrade|better card)",
            r"(am i|i.m) eligible",
        ],
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
        # A request for a person needs a verb of asking, not just a noun.
        # "someone" on its own was catching "someone used my card without
        # permission" — a fraud report, routed to the handoff queue because it
        # contained a pronoun. Nouns alone are not intent.
        "anchors": [
            r"\b(speak|talk|chat)\b[^?.]{0,16}\b(to|with)\b[^?.]{0,16}"
            r"\b(human|person|agent|advisor|someone|somebody|representative)\b",
            r"\b(connect|transfer|put)\b[^?.]{0,12}\bme\b",
            r"\b(get|give)\s+me\b[^?.]{0,12}\b(human|person|agent|advisor)\b",
            r"\b(real|actual|live)\s+(person|human|agent)\b",
            r"\bcustomer (service|support)\s+(officer|rep|agent|advisor)\b",
        ],
    },
    "greeting": {
        "utterances": [
            "hello", "hi there", "good morning", "hey", "good evening",
        ],
        "anchors": [r"^\s*(hi|hello|hey|good (morning|afternoon|evening))\b"],
    },
    "smalltalk": {
        "utterances": [
            "ok thanks", "thank you", "got it", "understood", "great",
            "are you still there", "hello are you there", "any update",
            "sorry what", "never mind then", "cool",
        ],
        "anchors": [r"^\s*(ok(ay)?|thanks|thank you|got it|understood|great|"
                    r"cool|nice|perfect|alright|sure)\s*[.!]?\s*$",
                    r"are you (still )?(there|here)",
                    r"^\s*(hmm+|uh+|erm+|\?+)\s*$"],
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


# Intents that read a specific customer's record. They all require an identity
# check, so misrouting into one costs the customer a verification step for
# information that needed none.
PERSONAL_INTENTS = frozenset({
    "balance_inquiry", "transaction_history", "loan_status",
    "account_summary", "block_card",
})

# Questions about how something *works*, as opposed to requests for a
# customer's own data. The two share almost all their vocabulary — "how is
# interest on my savings worked out?" and "what's my savings balance?" differ
# by intent, not by nouns, and TF-IDF alone cannot tell them apart. Possession
# does not separate them either: the first is possessive and still a policy
# question. What separates them is that one asks for a mechanism.
_MECHANISM_RE = re.compile(
    r"(what counts as|what qualifies|how (is|are|do you|does the bank) [^?]*"
    r"(calculat|work(ed)? out|determin|assess|appl(y|ied)|charged|waiv)"
    r"|which [^?]*\b(gets|comes|is) (taken|paid|applied|charged) (first|last)"
    r"|what happens (if|when)|what (is|are) the (policy|rule|criteria|conditions)"
    r"|how does .* work"
    r"|when (does|do|will) .*(reach|arrive|settle|clear|post)"
    r"|how (long|much|many) (does|do|is|are) (a|an|the|it|they))",
    re.IGNORECASE,
)

# How far a mechanism question's score is knocked down. Enough to drop it below
# HIGH_CONFIDENCE and into the knowledge-base path, not so far that it becomes
# "unknown" — the intent guess is still the best available signal for the
# audit trail.
_MECHANISM_PENALTY = 0.45


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

        # A question about how something works is not a request to see an
        # account, however much vocabulary the two share.
        if _MECHANISM_RE.search(text or ""):
            for name in PERSONAL_INTENTS & scores.keys():
                scores[name] *= _MECHANISM_PENALTY

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
