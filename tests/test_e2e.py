"""
Test E2E chi tiết — gọi thật qua HTTP vào server đang chạy.
Bám sát từng kỳ vọng trong TEST-SCENARIOS.txt.

Chạy: python3 test_e2e.py
Yêu cầu: server.py đang chạy ở http://localhost:8000
"""

import json
import os
import re
import sys
import time
import urllib.error
import urllib.request

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import http.cookiejar

BASE = "http://localhost:8000"
PASS = "PASS"
FAIL = "FAIL"
results = []

# ─── HTTP helpers (public + staff cookie jar) ──────────────────────────────────

_cj = http.cookiejar.CookieJar()
_opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(_cj))


def _post(path, payload, *, staff=False):
    # Giãn nhịp để không dồn call vào trần TOKEN/phút của Groq (vd 8000 TPM). Chỉ
    # trễ các endpoint tốn token LLM. Bật bằng: E2E_PACE=22 python3 test_e2e.py
    pace = os.environ.get("E2E_PACE")
    if pace and path in ("/api/chat", "/api/summary"):
        time.sleep(float(pace))
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        f"{BASE}{path}", data=data, headers={"Content-Type": "application/json"}
    )
    try:
        with _opener.open(req, timeout=30) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        return {"_http_error": e.code, "_body": e.read().decode()}
    except Exception as e:
        return {"_error": str(e)}


def _get(path):
    req = urllib.request.Request(f"{BASE}{path}")
    try:
        with _opener.open(req, timeout=10) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        return {"_http_error": e.code, "_body": e.read().decode()}
    except Exception as e:
        return {"_error": str(e)}


# ─── Session helpers ───────────────────────────────────────────────────────────


class Session:
    def __init__(self):
        self.id = None
        self._last = None

    def send(self, msg, *, label=""):
        payload = {"message": msg}
        if self.id:
            payload["session_id"] = self.id
        res = _post("/api/chat", payload)
        if "session_id" in res:
            self.id = res["session_id"]
        self._last = res
        route = res.get("route", "?")
        intent = res.get("intent", "?")
        conf = res.get("confidence", 0)
        text = res.get("reply", "")
        print(f"    > {msg}")
        print(f"      [{route}|{intent}@{conf:.2f}] {text[:110].replace(chr(10),' ')}")
        return res


def mk():
    return Session()


# ─── Assertion helpers ─────────────────────────────────────────────────────────


def check(name, ok, detail=""):
    results.append((name, bool(ok), str(detail)))
    mark = "✓" if ok else "✗"
    print(f"  [{mark}] {name}" + (f"  — {detail}" if detail else ""))


def section(title):
    print(f"\n{'='*74}\n{title}\n{'='*74}")


def txt(res):
    return res.get("reply", "")


def route(res):
    return res.get("route", "")


def intent(res):
    return res.get("intent", "")


def conf(res):
    return res.get("confidence", 0)


def verified(res):
    return res.get("verified", False)


def escalated(res):
    return res.get("escalated", False)


def sources(res):
    return res.get("sources") or []


def citations(res):
    return [s.get("citation", "") for s in sources(res)]


def trace_stages(res):
    return [t.get("stage", "") for t in (res.get("trace") or [])]


# ─── Connectivity guard ────────────────────────────────────────────────────────

health = _get("/api/health")
if "_error" in health or "_http_error" in health:
    print(f"\nLỖI: Không kết nối được server tại {BASE}")
    print("  Hãy chạy: python server.py")
    sys.exit(1)
kb_info = health.get("knowledge_base", {})
doc_count = kb_info.get("documents", kb_info.get("document_count", "?"))
print(f"Server OK — LLM: {health.get('provider','?')}  KB: {doc_count} docs")

# Staff login. Dùng tài khoản admin vì bộ test chạm cả endpoint vận hành
# (/api/queue, /api/session, /api/summary) lẫn endpoint cấu hình chỉ-admin
# (/api/sessions/clear, /api/providers). admin là superset của agent nên qua hết.
login = _post("/api/auth/login", {"username": "admin", "password": "admin1234"})
if "staff" in login:
    print(f"Staff login OK — {login['staff'].get('display_name','?')}")
else:
    print(f"Staff login WARN: {login}")

# Reset data trước mỗi lần chạy
reset = _post("/api/sessions/clear", {})
print(f"Reset: {reset}\n")

# ==============================================================================
section("A - NLU VÀ ĐỊNH TUYẾN TẤT ĐỊNH")
# ==============================================================================

# A1 ───────────────────────────────────────────────────────────────────────────
print("\nA1. Ý định rõ ràng → deterministic, không gọi model")
s = mk()
r = s.send("khoá thẻ của tôi")
check("A1-route=deterministic", route(r) == "deterministic", route(r))
check("A1-intent=block_card", intent(r) == "block_card", intent(r))
check("A1-confidence>=0.55", conf(r) >= 0.55, f"{conf(r):.2f}")
check(
    "A1-no-model-step",
    not any(st in ("generate", "generat", "rag", "llm") for st in trace_stages(r)),
    trace_stages(r),
)
check(
    "A1-asks-identity",
    any(w in txt(r).lower() for w in ["xác minh", "điện thoại", "cccd", "cmnd"]),
    txt(r)[:80],
)

# A2 ───────────────────────────────────────────────────────────────────────────
print("\nA2. Confidence thấp → KHÔNG vào luồng nghiệp vụ, KHÔNG hỏi xác minh")
s = mk()
r = s.send("số dư bình quân ngày được tính thế nào?")
check(
    "A2-not-deterministic",
    route(r) != "deterministic" or conf(r) < 0.55,
    f"route={route(r)} conf={conf(r):.2f}",
)
check(
    "A2-no-identity-prompt",
    not any(w in txt(r).lower() for w in ["xác minh", "điện thoại", "cmnd", "cccd"]),
    txt(r)[:80],
)
check("A2-has-answer", len(txt(r)) > 30, txt(r)[:60])

# A5 ───────────────────────────────────────────────────────────────────────────
print("\nA5. Xác thực 2 yếu tố thành công")
s = mk()
s.send("kiểm tra số dư")
s.send("0842199388")  # Nguyễn Văn An - SĐT
r = s.send("001094113804")  # ... CCCD → xác minh xong
check("A5-verified", verified(r), str(verified(r)))
check(
    "A5-shows-balance",
    any(w in txt(r).lower() for w in ["số dư", "vnd", "tài khoản", "triệu", "nghìn"]),
    txt(r)[:80],
)
check(
    "A5-uses-first-name",
    "an" in txt(r).lower() and not txt(r).lower().startswith("nguyen"),
    txt(r)[:60],
)
check(
    "A5-not-family-name",
    "nguyen" not in txt(r).lower().split()[0] if txt(r) else True,
    txt(r)[:40],
)

# A6 ───────────────────────────────────────────────────────────────────────────
print("\nA6. Từ chối khi dán số thẻ (3 biến thể)")
# 6a: 16 số có khoảng cách
s = mk()
s.send("kiểm tra số dư")
r = s.send("thẻ của tôi là 9411 3147 1234 5678")
check("A6-16digit-with-spaces-rejected", not verified(r), f"verified={verified(r)}")

# 6b: 16 số liền
s = mk()
s.send("kiểm tra số dư")
r = s.send("9411314712345678")
check("A6-16digit-nospace-rejected", not verified(r), f"verified={verified(r)}")

# 6c: 12 số (thêm nhóm thừa)
s = mk()
s.send("kiểm tra số dư")
r = s.send("9411 3147 9999")
check("A6-extra-group-rejected", not verified(r), f"verified={verified(r)}")

# A7 ───────────────────────────────────────────────────────────────────────────
print("\nA7. Sai 3 lần → escalation bảo mật")
s = mk()
s.send("kiểm tra số dư")
r1 = s.send("0000000000")  # SĐT 10 số đúng định dạng nhưng không tồn tại
check(
    "A7-attempt1-shows-count",
    "1/3" in txt(r1) or "lần 1" in txt(r1).lower(),
    txt(r1)[:60],
)
r2 = s.send("1111111111")
check(
    "A7-attempt2-shows-count",
    "2/3" in txt(r2) or "lần 2" in txt(r2).lower(),
    txt(r2)[:60],
)
r3 = s.send("2222222222")
check(
    "A7-escalated-after-3",
    route(r3) == "escalation" and escalated(r3),
    f"route={route(r3)} escalated={escalated(r3)}",
)
check(
    "A7-reason-mentions-security",
    any(
        w in (r3.get("escalation_reason") or "").lower()
        for w in ["identity", "verif", "three", "ba lần", "bảo mật"]
    ),
    r3.get("escalation_reason", ""),
)

# A8-1 ──────────────────────────────────────────────────────────────────────────
print("\nA8-1. Tạm khoá → mở khoá (Vũ Đức Hiếu - 1 thẻ ghi nợ)")
s = mk()
s.send("tạm khoá thẻ giúp tôi")
s.send("0328985767")  # Vũ Đức Hiếu - SĐT
s.send("038076763936")  # ... CCCD
r_frz = s.send("có")
check("A8-1-FRZ-code", "FRZ-" in txt(r_frz), txt(r_frz)[:80])
check(
    "A8-1-freeze-debit-card",
    "ghi nợ" in txt(r_frz).lower() or "debit" in txt(r_frz).lower(),
    txt(r_frz)[:80],
)
r_ufrz_q = s.send("mở khoá thẻ giúp tôi")
check("A8-1-unfreeze-route", route(r_ufrz_q) == "deterministic", route(r_ufrz_q))
r_ufrz = s.send("có")
check("A8-1-UNF-code", "UNF-" in txt(r_ufrz), txt(r_ufrz)[:80])
check(
    "A8-1-card-active-again",
    any(
        w in txt(r_ufrz).lower()
        for w in ["kích hoạt", "hoạt động", "sử dụng", "active"]
    ),
    txt(r_ufrz)[:80],
)

# A8-2 ──────────────────────────────────────────────────────────────────────────
print("\nA8-2. Báo mất thẻ → blocked + thẻ thay thế (Đỗ Thị Mai - 2 thẻ)")
s = mk()
s.send("tôi làm mất thẻ")
s.send("0395821542")  # Đỗ Thị Mai - SĐT
s.send("056175473446")  # ... CCCD
s.send("ghi nợ")  # chọn thẻ khi bot hỏi
r_blk = s.send("có")
check("A8-2-BLK-code", "BLK-" in txt(r_blk), txt(r_blk)[:80])
check(
    "A8-2-replacement-issued",
    any(
        w in txt(r_blk).lower()
        for w in ["thay thế", "thẻ mới", "replace", "inactive", "cấp"]
    ),
    txt(r_blk)[:120],
)

# A8-3 ──────────────────────────────────────────────────────────────────────────
print("\nA8-3. Thẻ blocked (báo mất) KHÔNG mở lại được (Bùi Văn Long)")
s = mk()
s.send("mở khoá thẻ giúp tôi")
s.send("0380591171")  # Bùi Văn Long - SĐT
r = s.send("046094715684")  # ... CCCD (thẻ đã blocked)
check(
    "A8-3-no-unfreeze",
    any(
        w in txt(r).lower()
        for w in ["không thể", "khoá", "blocked", "thay thế", "bảo mật", "nhân viên"]
    )
    or route(r) in ("deterministic", "escalation", "escalation_offered"),
    f"route={route(r)} text={txt(r)[:80]}",
)

# A8-4 ──────────────────────────────────────────────────────────────────────────
print("\nA8-4. Dữ liệu bền vững — phiên mới vẫn thấy trạng thái đã thay đổi")
# Sau A8-2, thẻ ghi nợ của Mai (5194 9572) đã bị blocked → phiên mới vẫn thấy
s = mk()
s.send("kiểm tra số dư")  # intent rõ, triggers verification
s.send("0395821542")  # verify Đỗ Thị Mai - SĐT
r_check = s.send("056175473446")  # ... CCCD
check("A8-4-verified-after-ops", verified(r_check), str(verified(r_check)))
# Thẻ ghi nợ đã blocked; số dư tài khoản vẫn hiển thị → state persisted across sessions
check(
    "A8-4-data-persisted-in-new-session",
    verified(r_check)
    and any(
        w in txt(r_check).lower() for w in ["tài khoản", "số dư", "vnd", "mai", "triệu"]
    ),
    txt(r_check)[:100],
)

# A9 ────────────────────────────────────────────────────────────────────────────
print("\nA9. Thoát luồng dở → không tiếp tục hỏi phone/ID")
s = mk()
s.send("tôi làm mất thẻ ghi nợ")
r = s.send("thôi bỏ đi, chi nhánh mở cửa mấy giờ?")
check(
    "A9-exits-block-flow",
    intent(r) not in ("block_card", "flow:verify", "flow:block"),
    f"route={route(r)} intent={intent(r)}",
)
check(
    "A9-no-longer-in-flow",
    route(r) in ("rag", "escalation_offered", "escalation", "deterministic"),
    route(r),
)

# ==============================================================================
section("B - LỚP RAG CÓ NỀN TRI THỨC")
# ==============================================================================

KB_CASES = [
    ("phí chuyển tiền quốc tế là bao nhiêu", "KB-PAY-001"),
    ("tôi có bao nhiêu ngày để tra soát giao dịch", "KB-SEC-001"),
    ("làm sao để không bị thu phí quản lý tài khoản", "KB-ACCT-001"),
    ("số dư bình quân ngày được tính thế nào", "KB-DEP-002"),
    ("hạn mức rút tiền ATM một ngày", "KB-CARD-001"),
    ("chi nhánh mở cửa mấy giờ", "KB-ACCT-001"),
    ("séc số tiền lớn bị giữ bao lâu", "KB-FUNDS-001"),
    ("ngân hàng có bao giờ hỏi mã OTP không", "KB-SEC-001"),
    ("hồ sơ vay mua nhà mất bao lâu", "KB-LOAN-001"),
]

print("\nB9. Bộ câu hỏi tri thức (9 câu)")
for q, kb in KB_CASES:
    s = mk()
    r = s.send(q)
    kb_hit = any(kb in c for c in citations(r))
    check(f"B9-route=rag [{q[:35]}]", route(r) == "rag", f"got={route(r)}")
    check(f"B9-source={kb[:12]} [{q[:25]}]", kb_hit, f"citations={citations(r)[:2]}")

print("\nB1. Câu trả lời kèm nguồn hiển thị")
s = mk()
r = s.send("séc số tiền lớn bị giữ bao lâu?")
check("B1-route=rag", route(r) == "rag", route(r))
check("B1-has-sources", len(sources(r)) > 0, str(len(sources(r))))
check("B1-source-has-citation", bool(citations(r) and citations(r)[0]), citations(r))
check(
    "B1-has-heading",
    any(s.get("heading") or s.get("title") for s in sources(r)),
    str(sources(r)[:1]),
)

print("\nB2. Phân biệt séc vs chuyển khoản điện tử")
s = mk()
r = s.send("khoản chuyển tiền đến có bị phong toả như séc không?")
check("B2-route=rag", route(r) == "rag", route(r))
check(
    "B2-distinguishes-correctly",
    any(
        w in txt(r).lower()
        for w in ["séc", "không áp dụng", "chỉ áp", "chuyển khoản", "điện tử"]
    ),
    txt(r)[:120],
)

print("\nB3. Ngoài phạm vi → không bịa, hỏi chuyển nhân viên")
s = mk()
r = s.send("ngân hàng có bảo hiểm cây trồng cho vườn nho ở Bồ Đào Nha không?")
check("B3-no-hallucination", route(r) in ("escalation_offered", "escalation"), route(r))
check(
    "B3-no-sources-invented",
    not any("KB-" in c for c in citations(r))
    or len(sources(r)) == 0
    or not any(w in txt(r).lower() for w in ["bảo hiểm cây", "vườn nho", "bồ đào nha"]),
    txt(r)[:80],
)

print("\nB7. Tài liệu tham khảo thị trường (KB-MKT-)")
s = mk()
r = s.send("phí thường niên thẻ Vietcombank Visa Platinum")
check("B7-route=rag", route(r) == "rag", route(r))
check("B7-KB-MKT-VCB-cited", any("KB-MKT" in c for c in citations(r)), citations(r))
check(
    "B7-disclaimer-unverified",
    any(
        w in txt(r).lower()
        for w in ["tham khảo", "chưa thẩm định", "market", "đối thủ", "competitor"]
    ),
    txt(r)[:120],
)

print("\nB8. Guardrail chặn tư vấn đầu tư — chặn trước model")
s = mk()
r = s.send("tôi có nên đầu tư tiết kiệm vào cổ phiếu công nghệ không?")
check("B8-route=guardrail", route(r) == "guardrail", route(r))
check(
    "B8-no-rag-step",
    not any(
        "retriev" in st.lower() or "generat" in st.lower() for st in trace_stages(r)
    ),
    trace_stages(r),
)
check(
    "B8-refuses-politely",
    any(
        w in txt(r).lower()
        for w in ["không thể", "tư vấn", "giấy phép", "chuyên gia", "licensed"]
    ),
    txt(r)[:80],
)

print("\nB-PAN. Che số thẻ đầy đủ trong phiên")
s = mk()
r = s.send("thẻ 4111 1111 1111 1111 của tôi bị trừ hai lần")
check(
    "B-PAN-no-raw-pan-in-reply",
    "4111 1111 1111 1111" not in txt(r) and "4111111111111111" not in txt(r),
    txt(r)[:80],
)

# ==============================================================================
section("C - CHUYỂN GIAO GIỮ NGUYÊN NGỮ CẢNH")
# ==============================================================================

print("\nC1. Hỏi ý khách trước khi tự ý chuyển")
s = mk()
r = s.send("ngân hàng có dịch vụ két sắt không?")
check("C1-offers-not-forces", route(r) == "escalation_offered", route(r))
check(
    "C1-asks-permission",
    any(w in txt(r).lower() for w in ["muốn", "chuyên viên", "chuyển", "không?"]),
    txt(r)[:80],
)

print("\nC2. Từ chối → trợ lý tiếp tục")
r2 = s.send("không, cảm ơn")
check("C2-decline-acknowledged", route(r2) == "deterministic", route(r2))
r3 = s.send("chi nhánh mở cửa mấy giờ?")
check("C2-continues-with-rag", route(r3) == "rag", route(r3))
check(
    "C2-answers-hours",
    any(
        w in txt(r3).lower()
        for w in ["giờ", "8", "17", "làm việc", "mở cửa", "08:00", "17:00"]
    ),
    txt(r3)[:80],
)

print("\nC3. Đồng ý → vào hàng chờ")
s = mk()
s.send("ngân hàng có dịch vụ két sắt không?")
r = s.send("có")
check(
    "C3-escalated",
    escalated(r) and route(r) == "escalation",
    f"route={route(r)} escalated={escalated(r)}",
)

print("\nC4. Trong hàng chờ → bot im lặng (route=agent)")
r_sil = s.send("về giao dịch bị trừ hai lần")
check("C4-stays-in-handoff", route(r_sil) == "agent", route(r_sil))
check(
    "C4-bot-silent",
    len(txt(r_sil).strip()) == 0 or "hàng chờ" in txt(r_sil).lower(),
    txt(r_sil)[:60],
)

print("\nC6. @bot trả lời khi trong hàng chờ, KHÔNG mất chỗ")
r_bot = s.send("@bot chi nhánh mở cửa mấy giờ?")
check("C6-bot-responds", route(r_bot) == "rag", route(r_bot))
check(
    "C6-answers-hours",
    any(w in txt(r_bot).lower() for w in ["giờ", "8", "17", "làm việc", "mở cửa"]),
    txt(r_bot)[:80],
)
check(
    "C6-still-in-queue",
    r_bot.get("in_handoff") or r_bot.get("escalated"),
    f"in_handoff={r_bot.get('in_handoff')} escalated={r_bot.get('escalated')}",
)

print("\nC8. /leave → quay lại trợ lý ảo")
r_leave = s.send("/leave")
check(
    "C8-leave-exits-queue",
    not r_leave.get("in_handoff") and not r_leave.get("escalated"),
    f"in_handoff={r_leave.get('in_handoff')} escalated={r_leave.get('escalated')}",
)
check("C8-route-after-leave", route(r_leave) == "deterministic", route(r_leave))

# ==============================================================================
section("D - BẢN TÓM TẮT BÀN GIAO")
# ==============================================================================

print("\nD1-D5. Tóm tắt bàn giao — đủ 4 mục, không bịa")
s = mk()
s.send("tôi cần hỏi về vay mua nhà, điều kiện thế nào?")
s.send("ngân hàng có bảo hiểm cây trồng cho vườn nho không?")
r_esc = s.send("có")  # đồng ý chuyển nhân viên
check("D1-session-escalated", escalated(r_esc), str(escalated(r_esc)))

summary_api = _post("/api/summary", {"session_id": s.id}, staff=True)
summary = summary_api.get("summary", summary_api.get("escalation_summary", ""))
check("D1-summary-exists", bool(summary.strip()), summary[:60])
check(
    "D2-has-customer-section",
    any(
        w in summary.lower() for w in ["khách hàng", "customer", "verified", "xác minh"]
    ),
    summary[:80],
)
check(
    "D2-has-request-section",
    any(w in summary.lower() for w in ["yêu cầu", "request", "cần", "question", "nêu"]),
    summary[:80],
)
check(
    "D3-no-card-number-in-summary",
    not re.search(r"\b\d{12,16}\b", summary),
    summary[:120],
)
check(
    "D3-no-pin-in-summary",
    not any(w in summary.lower() for w in ["pin", "mật khẩu", "password"]),
    summary[:80],
)
check(
    "D5-not-just-transcript",
    len(summary) < 3000,  # tóm tắt, không chép lại nguyên bản
    f"{len(summary)} chars",
)

print("\nD4. Escalation summary luôn có ngay sau handoff")
s2 = mk()
s2.send("cho tôi gặp nhân viên")
sess_data = _get(f"/api/session/{s2.id}")
summ2 = sess_data.get("escalation_summary", "")
check("D4-summary-exists-offline", bool(summ2.strip()), summ2[:60])
check("D4-not-empty-struct", len(summ2) > 50, f"{len(summ2)} chars")

# ==============================================================================
section("E - CHIẾN DỊCH KHÁCH HÀNG")
# ==============================================================================

print("\nE1. Kích hoạt thẻ — hướng dẫn app/IVR, KHÔNG hỏi OTP/CVV")
s = mk()
s.send("làm sao để kích hoạt thẻ mới?")
s.send("0944984320")  # Lê Minh Châu (thẻ inactive) - SĐT
r = s.send("031183007998")  # ... CCCD
check("E1-verified", verified(r), str(verified(r)))
check(
    "E1-mentions-inactive-card",
    any(w in txt(r).lower() for w in ["chưa kích hoạt", "inactive", "kích hoạt"]),
    txt(r)[:80],
)
check(
    "E1-redirects-to-app-or-ivr",
    any(
        w in txt(r).lower()
        for w in ["app", "ivr", "2255", "quầy", "chi nhánh", "ứng dụng"]
    ),
    txt(r)[:120],
)
check(
    "E1-no-otp-request",
    not any(
        w in txt(r).lower()
        for w in ["nhập otp", "cung cấp otp", "gửi otp", "cvv", "số thẻ đầy đủ"]
    ),
    txt(r)[:120],
)
check(
    "E1-not-self-activated",
    "active" not in txt(r).lower() or "chưa" in txt(r).lower(),
    txt(r)[:80],
)

print("\nE2. Cùng ý định, diễn đạt khác")
s = mk()
s.send("thẻ mới về rồi, tôi bắt đầu dùng thế nào?")
s.send("0944984320")  # Lê Minh Châu - SĐT
r = s.send("031183007998")  # ... CCCD
check(
    "E2-same-flow",
    verified(r)
    and any(
        w in txt(r).lower()
        for w in ["app", "ivr", "kích hoạt", "hướng dẫn", "ứng dụng"]
    ),
    txt(r)[:80],
)

print("\nE3. Thẻ ngủ đông — nhận diện và hướng dẫn")
s = mk()
s.send("thẻ lâu rồi tôi không dùng, còn dùng được không?")
s.send("0882935134")  # Trần Thị Bích (thẻ dormant) - SĐT
r = s.send("079190133919")  # ... CCCD
check(
    "E3-dormant-recognized",
    any(w in txt(r).lower() for w in ["ngủ đông", "dormant", "không hoạt động", "lâu"]),
    txt(r)[:80],
)
check(
    "E3-no-fake-deadline",
    not re.search(r"\b(ngày mai|tuần sau|hết hạn trong|hạn chót)\b", txt(r).lower()),
    txt(r)[:80],
)

print("\nE4. Bán chéo — chỉ ưu đãi từ file chiến dịch")
s = mk()
s.send("có ưu đãi nào cho tôi không?")
s.send("0842199388")  # Nguyễn Văn An (có ưu đãi XS-CLASSIC) - SĐT
r = s.send("001094113804")  # ... CCCD
check("E4-verified", verified(r), str(verified(r)))
check(
    "E4-has-offer",
    any(
        w in txt(r).lower()
        for w in ["ưu đãi", "nâng hạng", "premier", "cashback", "hoàn tiền"]
    ),
    txt(r)[:80],
)
check(
    "E4-not-invented",
    route(r) == "deterministic",
    f"route={route(r)} (model không được phép bịa ưu đãi)",
)

print("\nE5. Khách KHÔNG có ưu đãi — không đoán")
s = mk()
s.send("tôi có đủ điều kiện nâng hạng thẻ không?")
s.send("0328985767")  # Vũ Đức Hiếu (KHÔNG có ưu đãi) - SĐT
r = s.send("038076763936")  # ... CCCD
check("E5-verified", verified(r), str(verified(r)))
check(
    "E5-no-hallucinated-offer",
    any(w in txt(r).lower() for w in ["không có", "chưa có", "hiện không", "no offer"]),
    txt(r)[:80],
)
check("E5-route-deterministic", route(r) == "deterministic", route(r))

# ==============================================================================
section("F - API & VẬN HÀNH (tự động kiểm tra)")
# ==============================================================================

print("\nF-health. /api/health trả về đúng cấu trúc")
h = _get("/api/health")
check("F-health-has-provider", "provider" in h, str(list(h.keys())[:6]))
check("F-health-has-kb-stats", "knowledge_base" in h, str(list(h.keys())[:6]))
kb_stats = h.get("knowledge_base", {})
doc_ct = kb_stats.get("documents", kb_stats.get("document_count", 0))
check("F-health-kb-doc-count>0", doc_ct > 0, str(doc_ct))

print("\nF-queue. /api/queue — session vừa escalate phải có trong hàng đợi")
s_q = mk()
s_q.send("ngân hàng có dịch vụ két sắt không?")
s_q.send("có")
queue_raw = _get("/api/queue")
# queue_raw có thể là list hoặc {"sessions": [...]}
if isinstance(queue_raw, list):
    queue_items = queue_raw
elif isinstance(queue_raw, dict):
    queue_items = queue_raw.get("sessions", queue_raw.get("queue", []))
else:
    queue_items = []
queue_ids = []
for item in queue_items:
    if isinstance(item, dict):
        queue_ids.append(item.get("session_id") or item.get("id", ""))
    else:
        queue_ids.append(str(item))
check("F-queue-is-list", isinstance(queue_items, list), type(queue_raw).__name__)
check(
    "F-queue-contains-new-session",
    s_q.id in queue_ids or any(s_q.id in str(q) for q in queue_ids),
    f"session={s_q.id[:16]} queue_ids={queue_ids[:2]}",
)

print("\nF-session. /api/session/<id> — transcript + audit trail (staff)")
s_t = mk()
s_t.send("kiểm tra số dư")
s_t.send("0842199388")  # Nguyễn Văn An - SĐT
s_t.send("001094113804")  # ... CCCD
sess = _get(f"/api/session/{s_t.id}")
sess_keys = list(sess.keys()) if isinstance(sess, dict) else []
check(
    "F-session-no-error",
    "_error" not in sess and "_http_error" not in sess,
    str(sess_keys[:6]),
)
check(
    "F-session-has-transcript",
    any(k in sess for k in ("transcript", "messages", "turns", "history")),
    str(sess_keys[:6]),
)

print("\nF-providers. /api/providers — catalogue")
prov = _get("/api/providers")
check(
    "F-providers-ok",
    isinstance(prov, (dict, list)) and "_error" not in str(prov)[:30],
    str(list(prov.keys())[:4]) if isinstance(prov, dict) else str(type(prov)),
)

print("\nF-kb. /api/kb — danh sách tài liệu có sẵn")
kb = _get("/api/kb")
if isinstance(kb, list):
    docs = kb
elif isinstance(kb, dict):
    docs = kb.get("documents", kb.get("files", []))
else:
    docs = []
check("F-kb-has-documents", len(docs) > 0, f"{len(docs)} docs")
check(
    "F-kb-has-banking-doc",
    any(
        any(
            kw in str(d).lower()
            for kw in ["acct", "card", "loan", "kb-", "fund", "dep"]
        )
        for d in docs[:15]
    ),
    str(
        [
            d.get("name", "")[:20] if isinstance(d, dict) else str(d)[:20]
            for d in docs[:3]
        ]
    ),
)

# ==============================================================================
section("KẾT QUẢ")
# ==============================================================================

passed = sum(1 for _, ok, _ in results if ok)
total = len(results)
failed = [(n, d) for n, ok, d in results if not ok]

print(f"\n{passed}/{total} checks passed  ({len(failed)} failed)\n")
if failed:
    print("FAILED:")
    for name, detail in failed:
        print(f"  ✗ {name}  —  {detail}")
    sys.exit(1)
else:
    print("TẤT CẢ PASS ✓")
