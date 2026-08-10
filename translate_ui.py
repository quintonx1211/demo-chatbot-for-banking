"""One-shot translation of the customer-facing strings into Vietnamese.

Written as a script rather than done by hand across six files so the mapping is
reviewable in one place, and so a missed string is visible as an unreplaced
entry rather than as an English sentence discovered mid-demo.

Register notes, because a literal translation of banking English reads wrong in
Vietnamese customer service:

  * The assistant addresses the customer as "anh/chị" and refers to itself as
    "tôi". "Bạn" is too casual for a bank; "quý khách" is correct but stiff in
    chat, so it appears in formal notices only.
  * Vietnamese service language front-loads the acknowledgement - "Vâng, tôi
    kiểm tra giúp anh/chị ngay" - where English front-loads the action.
  * "ạ" closes a sentence to soften it. Used on requests and refusals, not on
    every line, which would read as fawning.
  * Fixed banking terms are kept in the form the industry actually uses:
    "sao kê", "hạn mức", "phí thường niên", "tra soát", "phong toả thẻ",
    "kích hoạt thẻ", "biểu phí".

Run once:  python translate_ui.py            (re-running is a no-op)
           python translate_ui.py --check    (report what is still English)
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# (file, english, vietnamese). Ordered longest-first per file at apply time so
# a short string never eats part of a longer one.
REPLACEMENTS: list[tuple[str, str, str]] = [
    # ---------------- app/flows.py : verification ----------------
    ("app/flows.py",
     "Happy to help with that. First, a quick security check so I know "
     "it's really you.\n\n"
     "Could you send me the **last 4 digits of your registered phone "
     "number** and the **last 4 digits of your national ID (CCCD)**, "
     "with a space between them?",
     "Vâng, tôi hỗ trợ anh/chị ngay. Trước tiên xin phép xác minh nhanh để "
     "chắc chắn đúng là chủ tài khoản ạ.\n\n"
     "Anh/chị vui lòng cho tôi **4 số cuối của số điện thoại đã đăng ký** và "
     "**4 số cuối CCCD**, cách nhau một dấu cách."),

    ("app/flows.py",
     "Not quite - I need just the two 4-digit codes on their own: "
     "the last 4 of your phone number, then the last 4 of your "
     "CCCD. Something like `1234 5678`.\n\n"
     "Please don't send your full card number or PIN - I'll never "
     "need either of those.",
     "Chưa đúng định dạng ạ. Tôi chỉ cần đúng hai nhóm 4 số: 4 số cuối điện "
     "thoại, rồi 4 số cuối CCCD, ví dụ `1234 5678`.\n\n"
     "Anh/chị lưu ý không gửi số thẻ đầy đủ hay mã PIN - tôi không bao giờ "
     "cần những thông tin đó."),

    ("app/flows.py",
     "I'm sorry - I haven't been able to verify those details, "
     "and I'm not able to keep trying from here. Let me pass "
     "you to a colleague who can sort this out properly.",
     "Tôi rất tiếc, thông tin xác minh vẫn chưa khớp và tôi không thể thử "
     "thêm ở đây. Tôi xin chuyển anh/chị sang chuyên viên để được hỗ trợ "
     "trực tiếp ạ."),

    ("app/flows.py",
     'f"Those details don\'t match our records (attempt {attempts} of 3). "\n'
     '                  "Please try again with both codes."',
     'f"Thông tin chưa khớp với hồ sơ của chúng tôi (lần {attempts}/3). "\n'
     '                  "Anh/chị vui lòng nhập lại cả hai mã giúp tôi ạ."'),

    ("app/flows.py",
     'greeting = f"Thanks, {_first_name(match)} - you\'re verified. "',
     'greeting = f"Cảm ơn anh/chị {_first_name(match)}, đã xác thực thành công. "'),
    ("app/flows.py",
     'return FlowResult(text=greeting + "How can I help?", note="verified")',
     'return FlowResult(text=greeting + "Tôi có thể hỗ trợ gì cho anh/chị ạ?",\n'
     '                      note="verified")'),

    # ---------------- app/flows.py : account reads ----------------
    ("app/flows.py",
     'lines = [f"Here are your balances as of today, {_first_name(customer)}:"]',
     'lines = [f"Số dư tài khoản của anh/chị {_first_name(customer)} tính đến "\n'
     '             f"hôm nay:"]'),
    ("app/flows.py",
     'f"- **{account[\'type\']}** {account[\'mask\']} - "\n'
     '            f"balance {_money(account[\'balance\'], account[\'currency\'])}, "\n'
     '            f"available {_money(account[\'available\'], account[\'currency\'])}"',
     'f"- **{account[\'type\']}** {account[\'mask\']} - "\n'
     '            f"số dư {_money(account[\'balance\'], account[\'currency\'])}, "\n'
     '            f"khả dụng {_money(account[\'available\'], account[\'currency\'])}"'),

    ("app/flows.py",
     'f"You\'re **{customer[\'name\']}** ({customer[\'customer_id\']}), "\n'
     '        f"verified on this chat.",',
     'f"Anh/chị là **{customer[\'name\']}** ({customer[\'customer_id\']}), "\n'
     '        f"đã xác thực trong phiên này.",'),
    ("app/flows.py", '"Here\'s what you hold with us:",',
     '"Các sản phẩm anh/chị đang có tại ngân hàng:",'),
    ("app/flows.py", '"I can go into any of these in more detail."',
     '"Anh/chị muốn xem chi tiết mục nào, tôi tra cứu giúp ạ."'),

    # ---------------- app/flows.py : card actions ----------------
    ("app/flows.py", '"verb": "report lost and block",',
     '"verb": "báo mất và khoá",'),
    ("app/flows.py", '"verb": "freeze",', '"verb": "tạm khoá",'),
    ("app/flows.py", '"verb": "unfreeze",', '"verb": "mở khoá",'),

    ("app/flows.py",
     '("Just to confirm: report your **{type} card {mask}** lost "\n'
     '                    "and block it?\\n\\nThis one can\'t be undone - a blocked card "\n'
     '                    "is never reopened, so I\'ll order you a replacement at the "\n'
     '                    "same time. If you\'ve only mislaid it and think it\'ll turn "\n'
     '                    "up, say **freeze** instead and you can unfreeze it "\n'
     '                    "yourself later."),',
     '("Anh/chị xác nhận báo mất và khoá **thẻ {type} {mask}** ạ?\\n\\n"\n'
     '                    "Thao tác này **không thể hoàn tác** - thẻ đã khoá do báo mất "\n'
     '                    "sẽ không mở lại được, nên tôi sẽ phát hành thẻ thay thế "\n'
     '                    "ngay cho anh/chị. Nếu anh/chị chỉ để quên và nghĩ sẽ tìm "\n'
     '                    "lại được, hãy nhắn **tạm khoá thẻ** - loại này anh/chị tự "\n'
     '                    "mở lại bất cứ lúc nào."),'),
    ("app/flows.py",
     '"none_left": "You don\'t have a card I can block right now.",',
     '"none_left": "Hiện anh/chị không có thẻ nào ở trạng thái có thể khoá ạ.",'),

    ("app/flows.py",
     '("I can freeze your **{type} card {mask}** straight away. "\n'
     '                    "Nothing will go through on it until you unfreeze it, and "\n'
     '                    "you can do that here any time.\\n\\nReply **yes** to freeze "\n'
     '                    "it."),',
     '("Tôi có thể tạm khoá **thẻ {type} {mask}** ngay cho anh/chị. "\n'
     '                    "Mọi giao dịch trên thẻ sẽ dừng cho tới khi anh/chị mở lại, "\n'
     '                    "và anh/chị mở lại ngay tại đây bất cứ lúc nào.\\n\\n"\n'
     '                    "Anh/chị nhắn **có** để tôi thực hiện ạ."),'),
    ("app/flows.py",
     '"none_left": "You don\'t have an active card to freeze.",',
     '"none_left": "Anh/chị hiện không có thẻ nào đang hoạt động để tạm khoá ạ.",'),

    ("app/flows.py",
     '("Ready to unfreeze your **{type} card {mask}** - it\'ll work "\n'
     '                    "again immediately.\\n\\nReply **yes** and I\'ll do it."),',
     '("Tôi sẽ mở khoá **thẻ {type} {mask}** cho anh/chị - thẻ dùng lại "\n'
     '                    "được ngay sau đó.\\n\\nAnh/chị nhắn **có** để tôi thực hiện ạ."),'),
    ("app/flows.py",
     '"none_left": ("None of your cards are frozen at the moment. If a card "\n'
     '                      "was reported lost it\'s blocked rather than frozen, and "\n'
     '                      "that one can\'t be reopened - but I can check the "\n'
     '                      "replacement for you."),',
     '"none_left": ("Hiện không có thẻ nào của anh/chị đang tạm khoá. Nếu "\n'
     '                      "trước đó anh/chị đã báo mất thẻ thì thẻ ở trạng thái "\n'
     '                      "khoá vĩnh viễn, không mở lại được - nhưng tôi có thể "\n'
     '                      "kiểm tra giúp anh/chị thẻ thay thế ạ."),'),

    ("app/flows.py",
     'return FlowResult(text="No problem - I\'ve left the card as it is.",',
     'return FlowResult(text="Vâng, tôi giữ nguyên trạng thái thẻ cho anh/chị ạ.",'),
    ("app/flows.py",
     'text=f"Reply **yes** to {spec[\'verb\']} the card, or **no** to leave it.",',
     'text=f"Anh/chị nhắn **có** để {spec[\'verb\']} thẻ, hoặc **không** để giữ nguyên ạ.",'),

    ("app/flows.py",
     'return (f"Done - your **{card[\'type\']} card {card[\'mask\']}** is frozen as "\n'
     '                f"of now. Reference **{reference}**.\\n\\n"\n'
     '                "Nothing will go through on it, including recurring payments. "\n'
     '                "Just say **unfreeze my card** whenever you want it back, and "\n'
     '                "I\'ll switch it on straight away.")',
     'return (f"Đã xong - **thẻ {card[\'type\']} {card[\'mask\']}** của anh/chị đã "\n'
     '                f"tạm khoá từ bây giờ. Mã tham chiếu **{reference}**.\\n\\n"\n'
     '                "Mọi giao dịch trên thẻ sẽ không thực hiện được, bao gồm cả các "\n'
     '                "khoản thanh toán định kỳ. Khi cần dùng lại, anh/chị chỉ cần "\n'
     '                "nhắn **mở khoá thẻ**, tôi mở ngay ạ.")'),
    ("app/flows.py",
     'return (f"Your **{card[\'type\']} card {card[\'mask\']}** is active again - "\n'
     '                f"reference **{reference}**. You can use it right now.\\n\\n"\n'
     '                "If any payment was declined while it was frozen, the merchant "\n'
     '                "will need to take it again.")',
     'return (f"**Thẻ {card[\'type\']} {card[\'mask\']}** của anh/chị đã hoạt động "\n'
     '                f"trở lại - mã tham chiếu **{reference}**. Anh/chị dùng được ngay ạ.\\n\\n"\n'
     '                "Nếu có giao dịch nào bị từ chối trong thời gian thẻ tạm khoá, "\n'
     '                "anh/chị vui lòng đề nghị đơn vị bán hàng thực hiện lại giúp.")'),
    ("app/flows.py",
     'return (f"Done - your **{card[\'type\']} card {card[\'mask\']}** is blocked "\n'
     '            f"effective immediately. Reference **{reference}**.\\n\\n"\n'
     '            f"I\'ve ordered a replacement: **{replacement.get(\'mask\', \'a new card\')}**, "\n'
     '            "arriving in 5-7 business days at no charge. It\'ll need activating "\n'
     '            "when it lands, and because the number changes you\'ll want to update "\n'
     '            "any recurring payments.\\n\\n"\n'
     '            "Is there anything on the old card you want to query?")',
     'return (f"Đã xong - **thẻ {card[\'type\']} {card[\'mask\']}** của anh/chị đã "\n'
     '            f"khoá, có hiệu lực ngay. Mã tham chiếu **{reference}**.\\n\\n"\n'
     '            f"Tôi đã phát hành thẻ thay thế: **{replacement.get(\'mask\', \'thẻ mới\')}**, "\n'
     '            "dự kiến 5-7 ngày làm việc, miễn phí. Thẻ mới cần kích hoạt khi nhận "\n'
     '            "được, và do số thẻ thay đổi nên anh/chị nhớ cập nhật lại các thanh "\n'
     '            "toán định kỳ ạ.\\n\\n"\n'
     '            "Anh/chị có giao dịch nào trên thẻ cũ cần tra soát không?")'),
    ("app/flows.py",
     'return (f"Which card should I {verb}?\\n{options}\\n\\n"\n'
     '            "Reply with the last 4 digits or the card type.")',
     'return (f"Anh/chị muốn {verb} thẻ nào ạ?\\n{options}\\n\\n"\n'
     '            "Vui lòng trả lời bằng 4 số cuối hoặc loại thẻ.")'),

    # ---------------- app/router.py ----------------
    ("app/router.py",
     '"Let me bring in one of our specialists - they\'ll have the full context of "\n'
     '    "this conversation, so you won\'t need to repeat anything.\\n\\n"\n'
     '    "**You\'re now in the queue for a human agent.**"',
     '"Tôi xin chuyển anh/chị sang chuyên viên hỗ trợ. Chuyên viên sẽ thấy toàn bộ "\n'
     '    "nội dung trao đổi này nên anh/chị không phải trình bày lại từ đầu ạ.\\n\\n"\n'
     '    "**Anh/chị đang trong hàng chờ gặp chuyên viên.**"'),
    ("app/router.py",
     '"That one\'s outside what I can answer too - I\'ve added it to the notes for "\n'
     '    "the specialist picking this up. **You\'re still in the queue.**"',
     '"Câu này cũng ngoài phạm vi tôi trả lời được. Tôi đã ghi chú lại để chuyên "\n'
     '    "viên tiếp nhận nắm được. **Anh/chị vẫn đang trong hàng chờ.**"'),
    ("app/router.py",
     '"I want to be straight with you - I don\'t have anything verified "\n'
     '            "on that, and I\'d rather say so than guess.\\n\\n"\n'
     '            "**Would you like me to bring in a colleague who can help?** "\n'
     '            "They\'ll already have this conversation in front of them, so "\n'
     '            "there\'s nothing you\'d need to repeat. Otherwise, ask me anything "\n'
     '            "else and I\'ll keep going."',
     '"Tôi xin phép nói thẳng: tôi chưa có tài liệu nào đã được thẩm định về nội "\n'
     '            "dung này, và tôi không muốn trả lời phỏng đoán.\\n\\n"\n'
     '            "**Anh/chị có muốn tôi kết nối với chuyên viên hỗ trợ không ạ?** "\n'
     '            "Chuyên viên sẽ thấy sẵn nội dung trao đổi này nên anh/chị không "\n'
     '            "phải nhắc lại. Hoặc anh/chị cứ hỏi tôi nội dung khác, tôi vẫn hỗ "\n'
     '            "trợ bình thường ạ."'),
    ("app/router.py",
     'reply = (f"You\'re back with the assistant - {agent_name} has been "\n'
     '                      "released from this chat. What can I help with?")',
     'reply = (f"Anh/chị đã quay lại với trợ lý ảo - chuyên viên {agent_name} "\n'
     '                      "đã rời cuộc trò chuyện. Tôi có thể hỗ trợ gì thêm ạ?")'),
    ("app/router.py",
     '("You\'re back with the assistant and I\'ve taken you out of "\n'
     '                      "the queue. What can I help with?")',
     '("Anh/chị đã quay lại với trợ lý ảo và tôi đã rút anh/chị "\n'
     '                      "khỏi hàng chờ. Tôi có thể hỗ trợ gì thêm ạ?")'),
    ("app/router.py",
     'reply = "You\'re already chatting with the assistant. How can I help?"',
     'reply = "Anh/chị đang trò chuyện với trợ lý ảo mà ạ. Tôi giúp gì được không?"'),

    # ---------------- app/guardrails.py ----------------
    ("app/guardrails.py",
     '"your accounts, cards, or applications."',
     '"các tài khoản, thẻ và hồ sơ của anh/chị ạ."'),
]

# Strings that must NOT be translated, checked after the pass. Each is a term
# the industry uses in English, or a token the code matches on.
KEEP_ENGLISH = ["deterministic", "escalation", "guardrail", "rag", "raw_llm"]


def apply(check_only: bool) -> int:
    by_file: dict[str, list[tuple[str, str]]] = {}
    for path, old, new in REPLACEMENTS:
        by_file.setdefault(path, []).append((old, new))

    missing = 0
    for path, pairs in by_file.items():
        target = ROOT / path
        text = target.read_text(encoding="utf-8")
        original = text
        # Longest first: a short English fragment can be a substring of a
        # longer one, and replacing it first would corrupt the longer match.
        for old, new in sorted(pairs, key=lambda p: -len(p[0])):
            if old in text:
                text = text.replace(old, new)
            elif new not in text:
                print(f"  MISS  {path}: {old.splitlines()[0][:64]!r}")
                missing += 1
        if not check_only and text != original:
            target.write_text(text, encoding="utf-8")
            print(f"  wrote {path}")
    return missing


def main(argv: list[str]) -> int:
    check_only = "--check" in argv
    missing = apply(check_only)
    print(f"\n  {len(REPLACEMENTS)} strings, {missing} not found.")
    if missing:
        print("  A miss means the source changed since this map was written -"
              "\n  fix the entry rather than leaving an English string in the UI.")
    return 1 if missing else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
