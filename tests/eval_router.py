"""Compare the lexical intent classifier against LLM routing.

Answers the question that decides whether to switch: on this traffic, how often
do the two disagree, and when they disagree, which one is right?

    python eval_router.py              # lexical only - no provider needed
    python eval_router.py --llm        # both, needs a configured provider

The labelled set is small and hand-written, which is the honest description of
it. It is enough to catch a regression and enough to show a direction; it is
not enough to certify a router for production. Say so if asked.
"""

from __future__ import annotations

import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.llm import route                       # noqa: E402
from app.nlu import HIGH_CONFIDENCE, IntentClassifier  # noqa: E402

# (message, intent that should win). "knowledge_query" means the turn belongs
# in the RAG layer - the customer is asking about policy, not asking for an
# action on their account.
#
# The action/policy pairs are the point of this file. Each pair shares almost
# all its vocabulary and differs only in what the customer wants, which is
# exactly where a lexical classifier has no signal to work with.
CASES: list[tuple[str, str]] = [
    # --- unambiguous actions ---
    ("khoá thẻ của tôi", "block_card"),
    ("tôi làm mất thẻ ghi nợ rồi, khoá giúp tôi", "block_card"),
    ("tạm khoá thẻ giúp tôi", "freeze_card"),
    ("mở khoá thẻ giúp tôi", "unfreeze_card"),
    ("kiểm tra số dư", "balance_inquiry"),
    ("xem giao dịch gần đây", "transaction_history"),
    ("hồ sơ vay của tôi đến đâu rồi", "loan_status"),
    ("tôi muốn gặp nhân viên", "human_agent"),
    ("làm sao để kích hoạt thẻ mới", "activate_card"),
    ("có ưu đãi nào cho tôi không", "card_offers"),
    ("tôi là ai trong hệ thống", "account_summary"),

    # --- policy questions that LOOK like actions ---
    ("thẻ của tôi bị mất tối qua giờ phải làm sao", "knowledge_query"),
    ("báo mất thẻ muộn thì tôi chịu trách nhiệm bao nhiêu", "knowledge_query"),
    ("tôi có phải chịu trách nhiệm giao dịch người khác thực hiện không",
     "knowledge_query"),
    ("hồ sơ vay mua nhà bao lâu mới có kết quả", "knowledge_query"),
    ("số dư bình quân ngày được tính thế nào", "knowledge_query"),
    ("tại sao số dư khả dụng thấp hơn số dư tài khoản", "knowledge_query"),
    ("quy trình khoá thẻ mất bao lâu", "knowledge_query"),
    ("thẻ thay thế mất phí bao nhiêu", "knowledge_query"),
    ("điều kiện để được miễn phí thường niên là gì", "knowledge_query"),

    # --- plain knowledge ---
    ("phí chuyển tiền quốc tế là bao nhiêu", "knowledge_query"),
    ("chi nhánh mở cửa mấy giờ", "knowledge_query"),
    ("ngân hàng có bao giờ hỏi mã OTP không", "knowledge_query"),

    # --- social ---
    ("xin chào", "greeting"),
    ("cảm ơn", "smalltalk"),
    ("tạm biệt", "goodbye"),
]


def effective(intent: str, confidence: float) -> str:
    """What the router will actually do with this prediction.

    Below the threshold every intent behaves as a knowledge question, so
    scoring the raw intent name would credit or blame the classifier for a
    label the router never acted on.
    """
    if confidence < HIGH_CONFIDENCE:
        return "knowledge_query"
    return intent


def main(argv: list[str]) -> int:
    use_llm = "--llm" in argv
    classifier = IntentClassifier()

    if use_llm:
        from app.llm import active_provider
        if active_provider() is None:
            print("  No provider configured - cannot measure LLM routing.")
            print("  Set a key, or run without --llm for the lexical baseline.")
            return 1

    lex_right = llm_right = agree = 0
    rows = []
    for message, expected in CASES:
        prediction = classifier.predict(message)
        lexical = effective(prediction.intent, prediction.confidence)
        lex_ok = lexical == expected
        lex_right += lex_ok

        llm_label = llm_ok = None
        if use_llm:
            verdict = route.classify(message, classifier.intents)
            if verdict and not verdict.get("error"):
                llm_label = effective(verdict["intent"], verdict["confidence"])
                llm_ok = llm_label == expected
                llm_right += llm_ok
                agree += llm_label == lexical
            else:
                llm_label = f"(lỗi: {verdict.get('error') if verdict else 'no provider'})"

        rows.append((message, expected, lexical, lex_ok, llm_label, llm_ok))

    width = max(len(m) for m, *_ in rows)
    header = f"  {'CÂU':<{width}}  {'KỲ VỌNG':<18} {'LEXICAL':<18}"
    if use_llm:
        header += f" {'LLM':<18}"
    print(f"\n{header}")
    print("  " + "-" * (width + (58 if use_llm else 38)))
    for message, expected, lexical, lex_ok, llm_label, llm_ok in rows:
        line = (f"  {message:<{width}}  {expected:<18} "
                f"{('OK ' if lex_ok else 'SAI') + ' ' + lexical:<18}")
        if use_llm:
            mark = "" if llm_ok is None else ("OK " if llm_ok else "SAI")
            line += f" {mark + ' ' + str(llm_label):<18}"
        print(line)

    total = len(CASES)
    print(f"\n  Lexical : {lex_right}/{total}  ({lex_right / total * 100:.0f}%)")
    if use_llm:
        print(f"  LLM     : {llm_right}/{total}  ({llm_right / total * 100:.0f}%)")
        print(f"  Trùng ý : {agree}/{total}  ({agree / total * 100:.0f}%)")
        if llm_right <= lex_right:
            print("\n  LLM routing did NOT beat the lexical classifier on this set.")
            print("  Do not switch router_mode to 'llm' on the strength of this run.")
    else:
        print("\n  Chạy lại với --llm (và một provider) để so sánh.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
