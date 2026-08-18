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
    # Recognised but answered from the knowledge base by the RAG layer, not by
    # a scripted flow - same role "knowledge_query" played before the pivot.
    "product_faq": {
        "utterances": [
            "phí thường niên thẻ tín dụng là bao nhiêu",
            "lãi suất vay mua nhà hiện nay bao nhiêu",
            "mở tài khoản lương cần giấy tờ gì",
            "hạn mức thẻ premier là bao nhiêu",
            "tôi cần giấy tờ gì để vay tiêu dùng",
            "phí quản lý quỹ đầu tư là bao nhiêu",
            "what is the annual fee on the premier credit card",
            "how do I apply for a personal loan",
            "what documents do I need for a mortgage",
            "what is the interest rate on savings",
            "how does the travel insurance bundle work",
            "what are the requirements for the mutual fund",
        ],
        "anchors": [],
    },
    # ">=2 product names in one message" is detected in code (router.py), not
    # here - no single utterance pattern reliably distinguishes "so sánh X và Y"
    # from "hỏi về X". The anchors below catch the explicit ask for a
    # comparison; the router's product-name count catches the implicit one.
    "product_comparison": {
        "utterances": [
            "so sánh thẻ classic và thẻ premier",
            "nên chọn vay tiêu dùng hay vay thế chấp",
            "khác nhau giữa tiết kiệm thường và tiết kiệm cao cấp",
            "compare the classic card and the premier card",
            "which is better, personal loan or mortgage",
            "what's the difference between basic and high-yield savings",
        ],
        "anchors": [
            r"(so sánh|khác nhau|khác gì)[^?.]{0,40}(và|với)",
            r"\b(compare|difference between|vs\.?|versus)\b",
            r"nên chọn[^?.]{0,20}(hay|hoặc)",
        ],
    },
    # A customer stating what they're interested in (Scenario 2). Dispatches
    # to rules_engine.py::explain_fit, not the RAG layer - segment already
    # decided which card, this is "why does it fit", a personalisation
    # question, not a documentation lookup.
    "cross_sell_interest": {
        "utterances": [
            "tôi hay mua sắm online, có ưu đãi gì không",
            "tôi thích đi du lịch nước ngoài",
            "tôi hay chơi golf, thẻ có ưu đãi gì cho golf không",
            "tôi hay ăn uống nhà hàng, có hoàn tiền không",
            "thẻ của tôi có ưu đãi gì phù hợp với tôi không",
            "có ưu đãi nào phù hợp với sở thích của tôi không",
            "I shop online a lot, any offers for that",
            "I travel abroad frequently, what does my card offer",
            "I play golf, are there any golf perks",
            "I eat out a lot, is there cashback for that",
            "what offers on my card fit my interests",
        ],
        "anchors": [
            r"(tôi (hay|thích|thường))[^?.]{0,30}(mua sắm|du lịch|golf|ăn uống|giải trí)",
            r"(ưu đãi|hoàn tiền)[^?.]{0,20}(phù hợp|cho (tôi|việc))",
            r"\bi (shop|travel|play golf|eat out)\b",
            r"\b(offers?|cashback|perks?)[^?.]{0,20}(fit|for) my\b",
        ],
    },
    # Scenario 3 - close the card. One-way transition (app/cards.py::close_card).
    "card_close": {
        "utterances": [
            "tôi muốn đóng thẻ",
            "hủy thẻ tín dụng của tôi",
            "cho tôi ngừng sử dụng thẻ này",
            "tôi không muốn dùng thẻ nữa, đóng giúp tôi",
            "I want to close my card",
            "cancel my credit card",
            "please close this card for me",
            "I don't want to use this card anymore, close it",
        ],
        "anchors": [
            r"(đóng|hủy|huỷ)[^?.]{0,10}thẻ",
            r"\b(close|cancel)\b[^?.]{0,12}\b(my |this )?card\b",
        ],
    },
    # Scenario 3 - request a different credit limit. Logged only, never
    # auto-approved (app/cards.py::request_limit_adjustment).
    "card_limit_adjust": {
        "utterances": [
            "tôi muốn tăng hạn mức thẻ",
            "xin điều chỉnh hạn mức tín dụng",
            "tôi muốn xin nâng hạn mức lên",
            "giảm hạn mức thẻ của tôi xuống",
            "I want to increase my credit limit",
            "please adjust my card's credit limit",
            "can I request a higher limit",
            "lower my credit limit please",
        ],
        "anchors": [
            r"(tăng|giảm|điều chỉnh|nâng)[^?.]{0,12}hạn mức",
            r"\b(increase|adjust|raise|lower|request a (higher|lower))\b[^?.]{0,16}\b(limit)\b",
        ],
    },
    # Scenario 3 - "what rewards do I get" without a stated new interest, as
    # opposed to cross_sell_interest which names a specific interest to match
    # against. Both call explain_fit; this one just skips straight to it.
    "reward_inquiry": {
        "utterances": [
            "thẻ của tôi có ưu đãi gì",
            "tôi được hoàn tiền bao nhiêu",
            "đặc quyền của thẻ tôi là gì",
            "quyền lợi thẻ của tôi gồm những gì",
            "what rewards does my card have",
            "how much cashback do I get",
            "what perks come with my card",
            "what benefits does my card include",
        ],
        "anchors": [
            r"(ưu đãi|đặc quyền|quyền lợi|hoàn tiền)[^?.]{0,16}(của tôi|thẻ tôi)",
            r"\b(rewards?|perks?|benefits?|cashback)\b[^?.]{0,16}\b(my card|do i get|include)\b",
        ],
    },
    "human_agent": {
        "utterances": [
            "tôi muốn gặp nhân viên",
            "cho tôi gặp người thật",
            "chuyển tôi cho tổng đài viên",
            "nối máy cho chuyên viên",
            "I want to speak to a human",
            "connect me to an agent",
            "transfer me to a representative",
            "let me talk to a real person",
            "this is not helping give me support",
            "I need a customer service officer",
        ],
        # A request for a person needs a verb of asking, not just a noun.
        # Nouns alone are not intent.
        "anchors": [
            r"(gặp|nói chuyện với)[^?.]{0,16}(nhân viên|người thật|chuyên viên|tổng đài)",
            r"chuyển[^?.]{0,10}(tôi|máy)",
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
            "xin chào",
            "chào bạn",
            "alo",
            "hello", "hi there", "good morning", "hey", "good evening",
        ],
        "anchors": [
            r"^\s*(xin chào|chào|alo)",r"^\s*(hi|hello|hey|good (morning|afternoon|evening))\b"],
    },
    "smalltalk": {
        "utterances": [
            "cảm ơn",
            "ok cảm ơn",
            "hiểu rồi",
            "vâng ạ",
            "còn đó không",
            "ok thanks", "thank you", "got it", "understood", "great",
            "are you still there", "hello are you there", "any update",
            "sorry what", "never mind then", "cool",
        ],
        "anchors": [
            r"^\s*(cảm ơn|cám ơn|ok|hiểu rồi|vâng|dạ)\s*[.!]?\s*$",r"^\s*(ok(ay)?|thanks|thank you|got it|understood|great|"
                    r"cool|nice|perfect|alright|sure)\s*[.!]?\s*$",
                    r"are you (still )?(there|here)",
                    r"^\s*(hmm+|uh+|erm+|\?+)\s*$"],
    },
    "goodbye": {
        "utterances": [
            "tạm biệt",
            "cảm ơn, vậy thôi ạ",
            "hết rồi cảm ơn",
            "thanks that is all", "goodbye", "bye bye", "that's everything thanks",
        ],
        "anchors": [
            r"^\s*(tạm biệt|chào nhé)",
            r"vậy thôi",r"^\s*(bye|goodbye)\b", r"that('s| is) all"],
    },
}


# Intents that need to know which customer is asking - they read a profile
# (segment, income, existing products) to answer, so misrouting into one costs
# the customer a verification step for a question that needed none.
PERSONAL_INTENTS = frozenset({
    "cross_sell_interest", "card_close", "card_limit_adjust", "reward_inquiry",
})

# Questions about how something *works*, as opposed to a personal request tied
# to the asker's own profile. "how is the annual fee waiver calculated?" is a
# policy question (product_faq); "what rewards do I get?" is personal
# (reward_inquiry). The two share vocabulary, which is why this needs its own
# regex rather than relying on TF-IDF to separate them.
_MECHANISM_RE = re.compile(
    r"(what counts as|what qualifies|how (is|are|do you|does the bank) [^?]*"
    r"(calculat|work(ed)? out|determin|assess|appl(y|ied)|charged|waiv)"
    r"|which [^?]*\b(gets|comes|is) (taken|paid|applied|charged) (first|last)"
    r"|what happens (if|when)|what (is|are) the (policy|rule|criteria|conditions)"
    r"|how does .* work"
    r"|when (does|do|will) .*(reach|arrive|settle|clear|post)"
    r"|how (long|much|many) (does|do|is|are) (a|an|the|it|they)"
    r"|(được )?tính (như )?thế nào|tính ra sao|cách tính"
    r"|(là )?(gì|thế nào) (mà|khi)|nghĩa là gì"
    r"|(quy định|chính sách|điều kiện|nguyên tắc) (về|áp dụng|là)"
    r"|(tại sao|vì sao|sao lại)"
    r"|(mất|trong) bao lâu|bao lâu thì"
    r"|(có|bị) (thu|tính) phí (không|thế nào))",
    re.IGNORECASE,
)

# How far a mechanism question's score is knocked down. Enough to drop it below
# HIGH_CONFIDENCE and into the knowledge-base path, not so far that it becomes
# "unknown" - the intent guess is still the best available signal for the
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
        # like "am I eligible" is unambiguous even when the wording is unusual.
        for name, patterns in self._anchors.items():
            if any(p.search(text) for p in patterns):
                scores[name] = min(1.0, max(scores.get(name, 0.0), 0.62) + 0.15)

        # A question about how something works is not a request tied to the
        # asker's own profile, however much vocabulary the two share.
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
