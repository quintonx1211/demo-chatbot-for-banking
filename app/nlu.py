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
            "số dư tài khoản của tôi còn bao nhiêu",
            "kiểm tra số dư",
            "xem số dư tài khoản",
            "tôi còn bao nhiêu tiền trong tài khoản",
            "số dư khả dụng của tôi",
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
        # see one - and routing it here sends the customer into an identity
        # check for information that needs no identity at all.
        "anchors": [
            r"số dư",r"\b(my|our)\b[^?.]{0,24}\bbalance",
                    r"\bbalance\b[^?.]{0,20}\b(my|our)\b",
                    r"^\s*(check|show|what.s)\b[^?.]{0,16}\bbalance",
                    r"how much (money|funds) do i have"],
    },
    "block_card": {
        "utterances": [
            "tôi làm mất thẻ",
            "thẻ của tôi bị đánh cắp",
            "báo mất thẻ",
            "khoá thẻ vĩnh viễn giúp tôi",
            "tôi bị mất ví, khoá thẻ giúp tôi",
            "block my card",
            "I lost my debit card",
            "my credit card was stolen",
            "someone stole my wallet block the card",
            "deactivate my card now",
            "I need to report my card lost",
        ],
        # "freeze" is deliberately NOT here any more. Freezing and reporting a
        # card lost are different requests with different consequences - one is
        # reversible, the other issues a replacement and can never be undone -
        # and routing both to the same flow is what left customers with a
        # blocked card and no way back.
        "anchors": [
            r"(mất|đánh cắp|thất lạc)[^?.]{0,16}thẻ",
            r"thẻ[^?.]{0,16}(bị mất|bị đánh cắp)",
            r"báo mất thẻ",
            # "khoá thẻ" on its own is a permanent block. The negative
            # lookbehind keeps it away from "tạm khoá thẻ" (freeze) and "mở
            # khoá thẻ" (unfreeze), which are different transitions with
            # different consequences - one of them irreversible.
            #
            # This anchor was simply missing after translation, so the plainest
            # instruction in the whole product - "khoá thẻ của tôi" - matched
            # no anchor, scored below threshold and went to the knowledge base.
            # The customer asking to block a card got a policy document.
            r"(?<!tạm )(?<!mở )(?<!bỏ )khoá thẻ",
            r"(?<!tạm )(?<!mở )(?<!bỏ )khóa thẻ",
            r"thẻ[^?.]{0,12}(?<!tạm )(?<!mở )khoá (ngay|lại|giúp|vĩnh viễn)",r"\b(block|deactivate)\b.*\bcard\b",
                    r"\bcard\b.*\b(lost|stolen|missing)\b",
                    r"\b(lost|stolen)\b.*\bcard\b",
                    r"\breport\b.*\bcard\b.*\b(lost|stolen)\b"],
    },
    "freeze_card": {
        "utterances": [
            "tạm khoá thẻ giúp tôi",
            "khoá tạm thời thẻ của tôi",
            "tôi để quên thẻ, tạm dừng thẻ giúp tôi",
            "tạm dừng thẻ",
            "freeze my card",
            "temporarily lock my card",
            "put a hold on my card",
            "I've misplaced my card, pause it for now",
            "can you suspend my card until I find it",
            "lock my card temporarily",
        ],
        "anchors": [
            r"(tạm khoá|tạm khóa|tạm dừng|khoá tạm|khóa tạm)",r"\b(freeze|suspend|pause|lock)\b[^?.]{0,20}\bcard\b",
                    r"\bcard\b[^?.]{0,16}\b(freeze|frozen|suspend|paused|locked)\b",
                    r"\btemporar\w+\b[^?.]{0,20}\b(block|lock|stop)\b"],
    },
    "unfreeze_card": {
        "utterances": [
            "mở khoá thẻ giúp tôi",
            "tôi tìm thấy thẻ rồi, mở lại giúp tôi",
            "bỏ tạm khoá thẻ",
            "kích hoạt lại thẻ đang tạm khoá",
            "unblock my card",
            "unfreeze my card",
            "I found my card, please turn it back on",
            "can you unlock my card again",
            "remove the freeze on my card",
            "my card is frozen, switch it back on",
            "undo the block on my card",
        ],
        # The gap that produced the bug this intent exists to fix: a customer
        # who froze a card had no phrase that reached anything. "unblock my
        # card" classified as `unknown` and fell through to retrieval, which
        # found nothing, so the assistant offered a human agent for an action
        # it was perfectly able to perform itself.
        "anchors": [
            r"(mở khoá|mở khóa|bỏ khoá|bỏ khóa|mở lại thẻ)",r"\bun(block|freeze|lock|suspend)\w*\b",
                    r"\b(undo|remove|lift|cancel)\b[^?.]{0,20}"
                    r"\b(block|freeze|hold|lock)\b",
                    r"\b(turn|switch)\b[^?.]{0,12}\b(back on|on again)\b",
                    r"\bfound\b[^?.]{0,16}\bcard\b"],
    },
    "loan_status": {
        "utterances": [
            "hồ sơ vay của tôi đến đâu rồi",
            "kiểm tra tình trạng hồ sơ vay",
            "hồ sơ vay mua nhà của tôi đã duyệt chưa",
            "khi nào có kết quả khoản vay",
            "what is the status of my loan application",
            "check my loan status",
            "has my mortgage been approved",
            "any update on my home loan",
            "when will my loan be approved",
            "loan application progress",
        ],
        "anchors": [
            r"hồ sơ vay",
            r"khoản vay[^?.]{0,16}(đến đâu|thế nào|duyệt)",r"\bloan\b.*\bstatus\b", r"\bstatus\b.*\b(loan|mortgage)\b",
                    r"\b(loan|mortgage)\b.*\b(approved|application)\b"],
    },
    "transaction_history": {
        "utterances": [
            "xem giao dịch gần đây",
            "lịch sử giao dịch của tôi",
            "tôi đã chi tiêu những gì tuần trước",
            "sao kê giao dịch gần nhất",
            "show my recent transactions",
            "list the last payments on my account",
            "what did I spend last week",
            "recent activity on my account",
            "show me my last five transactions",
        ],
        "anchors": [
            r"(giao dịch|chi tiêu)[^?.]{0,12}(gần đây|gần nhất)",
            r"lịch sử giao dịch",r"\b(recent|last|latest)\b.*\btransaction", r"\bspend\b"],
    },
    "account_summary": {
        "utterances": [
            "tôi là ai trong hệ thống",
            "thông tin của tôi",
            "tôi đang có những sản phẩm gì",
            "hồ sơ khách hàng của tôi",
            "who am I",
            "what are my details",
            "what accounts and cards do I hold with you",
            "what products do I hold",
            "tell me about my profile",
            "what is my customer number",
        ],
        "anchors": [
            r"tôi là ai",
            r"(thông tin|hồ sơ)[^?.]{0,10}của tôi",r"(?<![a-z])who am i(?![a-z])", r"my (details|profile)(?![a-z])",
                    r"what .*(accounts?|products?) do i (have|hold)"],
    },
    "activate_card": {
        "utterances": [
            "làm sao để kích hoạt thẻ mới",
            "thẻ mới về rồi tôi dùng thế nào",
            "thẻ mới về rồi tôi bắt đầu dùng thế nào",
            "thẻ mới về bắt đầu dùng như thế nào",
            "nhận thẻ rồi dùng thế nào",
            "kích hoạt thẻ",
            "thẻ chưa kích hoạt",
            "thẻ lâu rồi tôi không dùng còn dùng được không",
            "thẻ lâu không dùng thì có bị khóa không",
            "thẻ ngủ đông còn dùng được không",
            "thẻ đang ngủ đông",
            "thẻ bị ngủ đông",
            "thẻ cũ không hoạt động được nữa",
            "kích hoạt lại thẻ ngủ đông",
            "thẻ không dùng lâu còn sử dụng được không",
            "thẻ tôi để lâu không dùng",
            "how do I activate my new card",
            "my new card arrived how do I start using it",
            "I need to activate my debit card",
            "activate card",
            "my card is not working yet",
            "the card you sent me is inactive",
        ],
        "anchors": [
            r"kích hoạt",r"activat(e|ing|ion)",
                    r"new card[^?.]{0,24}(start|use|using|work)",
                    r"card[^?.]{0,20}(not|isn.t) (working|active|activated)",
                    r"thẻ[^?.]{0,30}(ngủ đông|lâu[^?.]{0,15}không dùng|lâu không dùng|không hoạt động)",
                    r"ngủ đông"],
    },
    "card_offers": {
        "utterances": [
            "có ưu đãi nào cho tôi không",
            "có khuyến mãi gì cho tôi",
            "tôi có đủ điều kiện nâng hạng thẻ không",
            "ưu đãi trên thẻ của tôi",
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
            r"(ưu đãi|khuyến mãi|khuyến mại)",
            r"nâng hạng",
            r"(offers?|deals?|promotions?|rewards?)[^?.]{0,20}"
            r"(for me|available|on my|i can get)",
            r"any[^?.]{0,12}(offers?|deals?|promotions?)",
            r"(upgrade|better card)",
            r"(am i|i.m) eligible",
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
        # "someone" on its own was catching "someone used my card without
        # permission" - a fraud report, routed to the handoff queue because it
        # contained a pronoun. Nouns alone are not intent.
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
    # Knowledge intents: recognised, but answered from the knowledge base by
    # the RAG layer rather than by a scripted flow.
    "knowledge_query": {
        "utterances": [
            "phí chuyển tiền quốc tế là bao nhiêu",
            "chi nhánh mở cửa mấy giờ",
            "mở tài khoản cần giấy tờ gì",
            "làm sao để không bị thu phí quản lý tài khoản",
            "lãi suất tiết kiệm hiện nay",
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
}


# Intents that read a specific customer's record. They all require an identity
# check, so misrouting into one costs the customer a verification step for
# information that needed none.
PERSONAL_INTENTS = frozenset({
    "balance_inquiry", "transaction_history", "loan_status",
    "account_summary", "block_card", "freeze_card", "unfreeze_card",
    "cross_sell_interest", "card_close", "card_limit_adjust", "reward_inquiry",
})

# Questions about how something *works*, as opposed to requests for a
# customer's own data. The two share almost all their vocabulary - "how is
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
    r"|how (long|much|many) (does|do|is|are) (a|an|the|it|they)"
    # Vietnamese. The English half of this pattern was carried over at
    # translation time and the Vietnamese half was not, so "số dư bình quân
    # ngày được tính thế nào?" - a policy question about how a figure is
    # derived - scored balance_inquiry at 0.77 and sent the customer into an
    # identity check to read a published rule. Same defect the English side
    # had, reintroduced in a new language.
    r"|(được )?tính (như )?thế nào|tính ra sao|cách tính"
    r"|(là )?(gì|thế nào) (mà|khi)|nghĩa là gì"
    r"|(quy định|chính sách|điều kiện|nguyên tắc) (về|áp dụng|là)"
    r"|(tại sao|vì sao|sao lại)"
    r"|(khoản nào|cái nào) (bị )?(trừ|thanh toán) (trước|sau)"
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
