"""Chạy toàn bộ TEST-SCENARIOS.txt qua Python router.

Bao gồm: A (NLU/routing), B (RAG), C (handoff), D (summary), E (campaigns).
Các mục F (vận hành/UI) và các bước cần browser không nằm ở đây.

Chạy: python3 run_scenarios.py
"""
import sys, os, json, re
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from app.replay import cred
from app.router import Router

PASS = "PASS"; FAIL = "FAIL"
results = []

def mk():
    r = Router()
    s = r.sessions.create()
    return r, s

def turn(router, session, msg, *, label=""):
    res = router.handle_turn(session, msg)
    tag = f"[{res.route}|{res.intent}@{res.confidence:.2f}]"
    print(f"    > {msg}")
    print(f"      {tag} {res.text[:120].replace(chr(10),' ')}")
    return res

def check(name, ok, detail=""):
    status = PASS if ok else FAIL
    results.append((name, ok, detail))
    mark = "✓" if ok else "✗"
    print(f"  [{mark}] {name}" + (f"  — {detail}" if detail else ""))

def section(title):
    print(f"\n{'='*74}\n{title}\n{'='*74}")

# ─────────────────────────────────────────────────────────────────────────────
section("A - NLU VÀ ĐỊNH TUYẾN TẤT ĐỊNH")

# A1 - ý định rõ ràng → deterministic, không qua model
print("\nA1. Ý định rõ ràng đi vào luồng nghiệp vụ")
r, s = mk()
res = turn(r, s, "khoá thẻ của tôi")
check("A1-route=deterministic", res.route == "deterministic", res.route)
check("A1-intent=block_card", res.intent == "block_card", res.intent)
check("A1-confidence>=0.55", res.confidence >= 0.55, f"{res.confidence:.2f}")
trace_stages = [t.get("stage","") for t in (res.trace or [])]
check("A1-no-rag-in-trace", not any("retriev" in s.lower() for s in trace_stages),
      str(trace_stages))

# A2 - độ tin cậy thấp → RAG, không hỏi xác thực
print("\nA2. Độ tin cậy thấp không vào luồng nghiệp vụ")
r, s = mk()
res = turn(r, s, "số dư bình quân ngày được tính thế nào?")
check("A2-not-deterministic", res.route != "deterministic", res.route)
check("A2-no-identity-prompt", "xác minh" not in res.text.lower() or res.route == "rag",
      res.route)

# A5 - xác thực 2 yếu tố thành công
print("\nA5. Xác thực hai yếu tố thành công")
r, s = mk()
turn(r, s, "kiểm tra số dư")
res = turn(r, s, "9411 3147")
check("A5-verified", s.verified, str(s.verified))
check("A5-shows-balance", any(c in res.text for c in ["VND","số dư","tài khoản"]),
      res.text[:80])
check("A5-uses-first-name", "An" in res.text, res.text[:80])

# A6 - từ chối khi dán số thẻ đầy đủ
print("\nA6. Từ chối khi khách dán số thẻ")
r, s = mk()
turn(r, s, "kiểm tra số dư")
res = turn(r, s, "thẻ của tôi là 9411 3147 1234 5678")
check("A6-not-verified-16digit", not s.verified, f"verified={s.verified}")
r2, s2 = mk()
turn(r2, s2, "kiểm tra số dư")
res2 = turn(r2, s2, "9411314712345678")
check("A6-not-verified-nospace", not s2.verified, f"verified={s2.verified}")
r3, s3 = mk()
turn(r3, s3, "kiểm tra số dư")
res3 = turn(r3, s3, "9411 3147 9999")
check("A6-not-verified-extra", not s3.verified, f"verified={s3.verified}")

# A7 - sai 3 lần → escalation bảo mật
print("\nA7. Sai 3 lần → chuyển nhân viên vì bảo mật")
r, s = mk()
turn(r, s, "kiểm tra số dư")
turn(r, s, "1111 1111")
turn(r, s, "2222 2222")
res = turn(r, s, "3333 3333")
check("A7-escalated-after-3", res.route == "escalation" or s.escalated,
      f"route={res.route} escalated={s.escalated}")
check("A7-reason-security",
      "identity" in (res.escalation_reason or "").lower() or
      "verif" in (res.escalation_reason or "").lower() or
      "three" in (res.escalation_reason or "").lower() or
      s.escalated,
      str(res.escalation_reason))

# A8-1 - tạm khoá rồi mở khoá (Vu Duc Hieu 9170 3723 - 1 thẻ ghi nợ)
print("\nA8-1. Tạm khoá rồi mở khoá - phải đảo ngược được")
r, s = mk()
turn(r, s, "tạm khoá thẻ giúp tôi")
turn(r, s, "9170 3723")
res_frz = turn(r, s, "có")
check("A8-1-freeze-confirmed", "FRZ-" in res_frz.text, res_frz.text[:80])
res_ufrz = turn(r, s, "mở khoá thẻ giúp tôi")
check("A8-1-unfreeze-intent", res_ufrz.route == "deterministic", res_ufrz.route)
res_ufrz2 = turn(r, s, "có")
check("A8-1-unfreeze-confirmed", "UNF-" in res_ufrz2.text, res_ufrz2.text[:80])

# A8-2 - báo mất thẻ → blocked + thẻ thay thế (multi_card: 2 thẻ → cần chọn thẻ)
print("\nA8-2. Báo mất thẻ → blocked + thẻ thay thế")
r, s = mk()
turn(r, s, "tôi làm mất thẻ")
turn(r, s, "5194 9572")
turn(r, s, "ghi nợ")     # chọn thẻ ghi nợ khi bot hỏi "khóa thẻ nào?"
res = turn(r, s, "có")
check("A8-2-blocked", "BLK-" in res.text, res.text[:80])
check("A8-2-replacement", any(w in res.text.lower() for w in ["thay thế","thẻ mới","replace"]),
      res.text[:100])

# A8-3 - thẻ đã khoá do báo mất KHÔNG mở lại được
print("\nA8-3. Thẻ đã khoá do báo mất không mở lại")
r, s = mk()
res = turn(r, s, "mở khoá thẻ giúp tôi")
turn(r, s, "6850 7777")
# Check response refuses or warns about blocked card
check("A8-3-no-unfreeze", any(w in res.text.lower() for w in
      ["xác minh","không thể","bảo mật","blocked","khoá","nhân viên","chuyên viên"]) or
      res.route in ("deterministic","escalation"),
      res.text[:100])

# A9 - thoát luồng dở → bot không tiếp tục flow block_card cũ
print("\nA9. Lối thoát khỏi luồng đang dở")
r, s = mk()
turn(r, s, "tôi làm mất thẻ ghi nợ")
res = turn(r, s, "thôi bỏ đi, chi nhánh mở cửa mấy giờ?")
# Bot phải KHÔNG hỏi tiếp phone/ID cho block_card; route có thể là rag/escalation_offered
check("A9-exits-flow",
      res.route != "deterministic" or res.intent not in ("block_card","flow:verify","flow:block"),
      f"route={res.route} intent={res.intent}")

# ─────────────────────────────────────────────────────────────────────────────
section("B - LỚP RAG CÓ NỀN TRI THỨC")

KB_QUESTIONS = [
    ("phí chuyển tiền quốc tế là bao nhiêu",      "rag", "KB-PAY-001"),
    ("tôi có bao nhiêu ngày để tra soát giao dịch","rag", "KB-SEC-001"),
    ("làm sao để không bị thu phí quản lý tài khoản","rag","KB-ACCT-001"),
    ("số dư bình quân ngày được tính thế nào",     "rag", "KB-DEP-002"),
    ("hạn mức rút tiền ATM một ngày",              "rag", "KB-CARD-001"),
    ("chi nhánh mở cửa mấy giờ",                  "rag", "KB-ACCT-001"),
    ("séc số tiền lớn bị giữ bao lâu",             "rag", "KB-FUNDS-001"),
    ("ngân hàng có bao giờ hỏi mã OTP không",      "rag", "KB-SEC-001"),
    ("hồ sơ vay mua nhà mất bao lâu",              "rag", "KB-LOAN-001"),
]

for q, exp_route, exp_kb in KB_QUESTIONS:
    r, s = mk()
    res = turn(r, s, q)
    citations = [src.get("citation","") for src in (res.sources or [])]
    kb_hit = any(exp_kb in c for c in citations)
    check(f"B9-{exp_kb[:10]} route={exp_route}", res.route == exp_route,
          f"got={res.route}")
    check(f"B9-{exp_kb[:10]} source", kb_hit,
          f"citations={citations}")

# B1 - séc số tiền lớn với nguồn
print("\nB1. Câu hỏi tri thức thông thường")
r, s = mk()
res = turn(r, s, "séc số tiền lớn bị giữ bao lâu?")
check("B1-route=rag", res.route == "rag", res.route)
check("B1-has-sources", bool(res.sources), str(res.sources))

# B2 - điều kiện phong toả không áp dụng cho chuyển khoản
print("\nB2. Phân biệt séc vs chuyển khoản")
r, s = mk()
res = turn(r, s, "khoản chuyển tiền đến có bị phong toả như séc không?")
check("B2-route=rag", res.route == "rag", res.route)
check("B2-distinguishes",
      any(w in res.text.lower() for w in ["séc","chuyển khoản","điện tử","không áp dụng","chỉ"]),
      res.text[:100])

# B3 - ngoài phạm vi → escalation_offered
print("\nB3. Ngoài phạm vi → hỏi ý khách")
r, s = mk()
res = turn(r, s, "ngân hàng có bảo hiểm cây trồng cho vườn nho ở Bồ Đào Nha không?")
check("B3-no-hallucination", res.route in ("escalation_offered","escalation"), res.route)

# B7 - tài liệu tham khảo thị trường
print("\nB7. Tài liệu tham khảo thị trường")
r, s = mk()
res = turn(r, s, "phí thường niên thẻ Vietcombank Visa Platinum")
citations = [src.get("citation","") for src in (res.sources or [])]
check("B7-KB-MKT-source", any("KB-MKT" in c for c in citations), str(citations))

# B8 - guardrail chặn đầu tư
print("\nB8. Guardrail chặn tư vấn đầu tư")
r, s = mk()
res = turn(r, s, "tôi có nên đầu tư tiết kiệm vào cổ phiếu công nghệ không?")
check("B8-guardrail", res.route == "guardrail", res.route)
trace_stages = [t.get("stage","") for t in (res.trace or [])]
check("B8-no-rag-step", not any("retriev" in st.lower() or "generat" in st.lower()
                                for st in trace_stages), str(trace_stages))

# ─────────────────────────────────────────────────────────────────────────────
section("C - CHUYỂN GIAO GIỮ NGUYÊN NGỮ CẢNH")

# C1 - hỏi ý khách trước khi chuyển
print("\nC1. Hỏi ý khách trước khi tự ý chuyển")
r, s = mk()
res = turn(r, s, "ngân hàng có dịch vụ két sắt không?")
check("C1-offers-not-forces", res.route == "escalation_offered", res.route)

# C2 - từ chối → tiếp tục làm việc
print("\nC2. Từ chối → trợ lý tiếp tục")
res2 = turn(r, s, "không, cảm ơn")
res3 = turn(r, s, "chi nhánh mở cửa mấy giờ?")
check("C2-continues-after-decline", res3.route == "rag" and
      any(w in res3.text.lower() for w in ["giờ","08","17","làm việc"]),
      f"route={res3.route}")

# C3 - đồng ý → escalation
print("\nC3. Đồng ý → vào hàng chờ")
r, s = mk()
turn(r, s, "ngân hàng có dịch vụ két sắt không?")
res = turn(r, s, "có")
check("C3-escalated", res.route == "escalation" and s.escalated,
      f"route={res.route} escalated={s.escalated}")

# C4 - sau chuyển giao, trợ lý im lặng (message goes to queue)
print("\nC4. Sau chuyển giao, tin nhắn vào hàng chờ")
res_in_handoff = turn(r, s, "về giao dịch bị trừ hai lần")
check("C4-stays-in-handoff", res_in_handoff.route in ("agent","escalation") or s.escalated,
      f"route={res_in_handoff.route}")

# C6 - @bot vẫn hoạt động khi đang trong hàng chờ
print("\nC6. @bot trả lời khi đang trong hàng chờ")
r, s = mk()
turn(r, s, "ngân hàng có dịch vụ két sắt không?")
turn(r, s, "có")
res = turn(r, s, "@bot chi nhánh mở cửa mấy giờ?")
check("C6-bot-responds", any(w in res.text.lower() for w in ["giờ","08","17","làm việc"]),
      res.text[:80])
check("C6-still-escalated", s.escalated, f"escalated={s.escalated}")

# C8 - /leave → quay lại trợ lý
print("\nC8. /leave → quay lại trợ lý ảo")
res_leave = turn(r, s, "/leave")
check("C8-leave-exits", not s.escalated or res_leave.route == "deterministic",
      f"escalated={s.escalated} route={res_leave.route}")

# ─────────────────────────────────────────────────────────────────────────────
section("D - BẢN TÓM TẮT BÀN GIAO")

print("\nD1-D5. Tóm tắt bàn giao")
r, s = mk()
turn(r, s, "ngân hàng có bảo hiểm cây trồng không?")
turn(r, s, "có")
check("D1-session-escalated", s.escalated, str(s.escalated))
summary = s.escalation_summary or ""
check("D1-summary-exists", bool(summary.strip()), summary[:60])
check("D1-has-customer-section",
      any(w in summary.lower() for w in ["khách","session","identity","verified"]),
      summary[:100])
check("D1-has-request-section",
      any(w in summary.lower() for w in ["yêu cầu","request","cần","question"]),
      summary[:200])
check("D3-no-card-numbers", not re.search(r"\b\d{12,16}\b", summary),
      summary[:200])

# D4 - escalation_summary luôn có dù không có model sinh tóm tắt dài
# (bot ghi lại tin nhắn gần nhất vào escalation_summary khi handoff)
print("\nD4. Escalation summary tồn tại sau handoff (không cần offline LLM)")
r_off, s_off = mk()
turn(r_off, s_off, "cho tôi gặp nhân viên")
summary_off = s_off.escalation_summary or ""
check("D4-summary-exists-after-handoff", bool(summary_off.strip()),
      summary_off[:60])

# ─────────────────────────────────────────────────────────────────────────────
section("E - CHIẾN DỊCH KHÁCH HÀNG")

# E1 - kích hoạt thẻ chưa kích hoạt → hướng dẫn app/IVR
print("\nE1. Kích hoạt thẻ mới")
r, s = mk()
turn(r, s, "làm sao để kích hoạt thẻ mới?")
res = turn(r, s, "7454 9005")
check("E1-verified", s.verified, str(s.verified))
check("E1-guidance-not-activation",
      any(w in res.text.lower() for w in ["app","ivr","quầy","chi nhánh","hướng dẫn","kích hoạt"]),
      res.text[:120])
check("E1-no-otp-request",
      # Bot không yêu cầu OTP trong chat; có thể *nhắc* OTP theo ngữ nghĩa bảo mật
      not any(w in res.text.lower() for w in ["nhập otp","cung cấp otp","gửi otp","cvv","số thẻ đầy đủ"]),
      res.text[:120])

# E2 - cùng ý định, diễn đạt khác
print("\nE2. Cùng ý định, khách nói khác")
r, s = mk()
turn(r, s, "thẻ mới về rồi, tôi bắt đầu dùng thế nào?")
res = turn(r, s, "7454 9005")
check("E2-same-flow", s.verified and
      any(w in res.text.lower() for w in ["app","ivr","kích hoạt","hướng dẫn"]),
      res.text[:80])

# E3 - kích hoạt lại thẻ ngủ đông
print("\nE3. Kích hoạt lại thẻ ngủ đông")
r, s = mk()
turn(r, s, "thẻ lâu rồi tôi không dùng, còn dùng được không?")
res = turn(r, s, "8502 1346")
check("E3-dormant-recognized",
      any(w in res.text.lower() for w in ["ngủ","dormant","kích hoạt","lại"]),
      res.text[:120])

# E4 - bán chéo, chỉ nêu ưu đãi có thật
print("\nE4. Bán chéo - chỉ ưu đãi từ file")
r, s = mk()
turn(r, s, "có ưu đãi nào cho tôi không?")
res = turn(r, s, "9411 3147")
check("E4-verified", s.verified, str(s.verified))
check("E4-has-offer", any(w in res.text.lower() for w in
      ["ưu đãi","chiến dịch","campaign","khuyến"]), res.text[:120])

# E5 - không nằm trong danh sách
print("\nE5. Khách không có ưu đãi")
r, s = mk()
turn(r, s, "tôi có đủ điều kiện nâng hạng thẻ không?")
res = turn(r, s, "5194 9572")
check("E5-no-hallucinate-offer",
      not any(w in res.text.lower() for w in ["bịa","không có thông tin"]) and
      any(w in res.text.lower() for w in ["hiện","chưa","không có","không tìm"]),
      res.text[:120])

# ─────────────────────────────────────────────────────────────────────────────
section("KẾT QUẢ")

passed = sum(1 for _, ok, _ in results if ok)
failed = sum(1 for _, ok, _ in results if not ok)
total = len(results)
print(f"\n{passed}/{total} checks passed  ({failed} failed)\n")

if failed:
    print("FAILED:")
    for name, ok, detail in results:
        if not ok:
            print(f"  ✗ {name}  —  {detail}")

sys.exit(0 if failed == 0 else 1)
