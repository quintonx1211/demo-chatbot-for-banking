"""
Test E2E cho tính năng Workforce: vai trò admin + agent + skillset.

Gọi thật qua HTTP vào server đang chạy, giống test_e2e.py.
Chạy:  python3 test_workforce.py
Yêu cầu: server.py đang chạy ở http://localhost:8000

Kịch bản kiểm thử:
  W1  Phân quyền: chưa đăng nhập → 401; đăng nhập agent → 403; admin → 200.
  W2  Workforce mặc định: đúng 3 agent (ops/qa/xsell) với số skill kỳ vọng.
  W3  Định tuyến chuyển agent: phiên đang ở Product Q&A hỏi số dư → chuyển
      sang Operation Support (trace có bước 'workforce').
  W4  Tắt skill toàn cục: bỏ balance_inquiry ở ops (agent duy nhất có) →
      khách hỏi số dư → escalation "chưa được hỗ trợ".
  W5  Bật lại: hỏi số dư → về lại luồng xác thực (deterministic), hết escalation.
  W6  Bền vững + an toàn: cấu hình lưu lại sau khi đọc lại; skill lạ bị bỏ qua;
      skill nền tảng không thể tắt.
  W7  Tắt cả agent: ops.enabled=false → hỏi số dư → escalation; bật lại.

Test tự dọn: luôn khôi phục ops về đủ skill + enabled ở cuối.
"""

import json
import sys
import urllib.error
import urllib.request

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BASE = "http://localhost:8000"
results = []

# ─── HTTP helpers ──────────────────────────────────────────────────────────────
# Mỗi "nhân vật" (admin / agent / khách vãng lai) có cookie jar riêng, nên phân
# quyền được kiểm tra độc lập thay vì dùng chung một phiên đăng nhập.


def _client():
    import http.cookiejar

    cj = http.cookiejar.CookieJar()
    return urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))


def _req(opener, method, path, payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        f"{BASE}{path}",
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with opener.open(req, timeout=30) as r:
            return r.status, json.loads(r.read() or "{}")
    except urllib.error.HTTPError as e:
        return e.code, {"_body": e.read().decode()}


def check(name, ok, detail=""):
    results.append(ok)
    mark = "\033[92m[✓]\033[0m" if ok else "\033[91m[✗]\033[0m"
    print(f"  {mark} {name}" + (f"  —  {str(detail)[:90]}" if detail else ""))


# ─── Nhân vật ──────────────────────────────────────────────────────────────────
anon = _client()
agent = _client()
admin = _client()
_req(agent, "POST", "/api/auth/login", {"username": "agent", "password": "demo1234"})
_req(admin, "POST", "/api/auth/login", {"username": "admin", "password": "admin1234"})


def chat(opener, message, session_id=None):
    body = {"message": message}
    if session_id:
        body["session_id"] = session_id
    _, res = _req(opener, "POST", "/api/chat", body)
    return res


def set_agent(agent_id, **fields):
    return _req(admin, "POST", f"/api/agents/{agent_id}", fields)


ALL_OPS = [
    "balance_inquiry",
    "account_summary",
    "transaction_history",
    "loan_status",
    "reward_inquiry",
    "activate_card",
    "block_card",
    "freeze_card",
    "unfreeze_card",
    "card_close",
    "card_limit_adjust",
]

# ─── W1. Phân quyền ─────────────────────────────────────────────────────────────
print("\nW1. Phân quyền admin-only trên /api/agents")
code_anon, _ = _req(anon, "GET", "/api/agents")
check("W1-anon-401", code_anon == 401, f"HTTP {code_anon}")
code_agent, _ = _req(agent, "GET", "/api/agents")
check("W1-agent-403", code_agent == 403, f"HTTP {code_agent}")
code_admin, admin_agents = _req(admin, "GET", "/api/agents")
check("W1-admin-200", code_admin == 200, f"HTTP {code_admin}")
code_prov, _ = _req(agent, "GET", "/api/providers")
check(
    "W1-config-admin-only", code_prov == 403, f"providers cho agent: HTTP {code_prov}"
)

# ─── W2. Workforce mặc định ─────────────────────────────────────────────────────
print("\nW2. Workforce mặc định = 3 agent")
by_id = {a["id"]: a for a in admin_agents.get("agents", [])}
check("W2-three-agents", set(by_id) == {"ops", "qa", "xsell"}, list(by_id))
expected_counts = {"ops": 11, "qa": 3, "xsell": 2}
for aid, n in expected_counts.items():
    a = by_id.get(aid, {})
    check(
        f"W2-{aid}-skills",
        len(a.get("skills", [])) == n and all(s["on"] for s in a.get("skills", [])),
        f"{a.get('skillset_label')}: {len(a.get('skills', []))} skill",
    )

# ─── W3. Chuyển agent giữa phiên ────────────────────────────────────────────────
print("\nW3. Khách đang ở Product Q&A hỏi số dư → chuyển sang Operation Support")
r1 = chat(anon, "so sánh thẻ Visa và Mastercard")
sid = r1.get("session_id")
r2 = chat(anon, "cho tôi xem số dư", session_id=sid)
wf = [t for t in r2.get("trace", []) if t.get("stage") == "workforce"]
check(
    "W3-transfer-traced",
    bool(wf),
    wf[0].get("outcome") if wf else "không có bước workforce",
)
check(
    "W3-transfer-to-ops",
    bool(wf) and "Operation Support" in wf[0].get("outcome", ""),
    wf[0].get("outcome") if wf else "",
)

# ─── W4. Tắt skill toàn cục ─────────────────────────────────────────────────────
print("\nW4. Tắt balance_inquiry ở ops (agent duy nhất) → nghiệp vụ bị chặn")
skills_no_balance = [s for s in ALL_OPS if s != "balance_inquiry"]
_, saved = set_agent("ops", enabled_skills=skills_no_balance)
on_now = [s["id"] for s in saved["agent"]["skills"] if s["on"]]
check("W4-balance-removed", "balance_inquiry" not in on_now, on_now)
r = chat(anon, "kiểm tra số dư tài khoản")
check(
    "W4-escalated",
    r.get("route") == "escalation" and r.get("escalated"),
    f"route={r.get('route')}",
)
check(
    "W4-unsupported-msg",
    "chưa được hỗ trợ" in r.get("reply", ""),
    r.get("reply", "")[:60],
)

# ─── W5. Bật lại ────────────────────────────────────────────────────────────────
print("\nW5. Bật lại balance_inquiry → nghiệp vụ trở lại (hỏi xác thực)")
set_agent("ops", enabled_skills=ALL_OPS)
r = chat(anon, "kiểm tra số dư tài khoản")
check(
    "W5-back-deterministic",
    r.get("route") == "deterministic",
    f"route={r.get('route')}",
)
check(
    "W5-asks-verification",
    any(w in r.get("reply", "").lower() for w in ["xác minh", "điện thoại"]),
    r.get("reply", "")[:60],
)

# ─── W6. Bền vững + an toàn payload ──────────────────────────────────────────────
print("\nW6. Cấu hình bền vững + bỏ qua đầu vào không hợp lệ")
set_agent("ops", enabled_skills=["balance_inquiry", "block_card"])
_, refetched = _req(admin, "GET", "/api/agents")
ops = next(a for a in refetched["agents"] if a["id"] == "ops")
on_after = {s["id"] for s in ops["skills"] if s["on"]}
check("W6-persisted", on_after == {"balance_inquiry", "block_card"}, sorted(on_after))
# Skill lạ + skill của agent khác (product_faq) phải bị loại bỏ.
_, saved = set_agent(
    "ops", enabled_skills=["balance_inquiry", "product_faq", "khong_ton_tai"]
)
on_clean = {s["id"] for s in saved["agent"]["skills"] if s["on"]}
check("W6-rejects-foreign-skills", on_clean == {"balance_inquiry"}, sorted(on_clean))
# Skill nền tảng không nằm trong danh sách config của agent.
all_skill_ids = {s["id"] for a in refetched["agents"] for s in a["skills"]}
check(
    "W6-baseline-not-configurable",
    not ({"greeting", "smalltalk", "goodbye", "human_agent"} & all_skill_ids),
    "baseline skills vắng mặt trong config (đúng)",
)
set_agent("ops", enabled_skills=ALL_OPS)  # khôi phục

# ─── W7. Tắt cả agent ───────────────────────────────────────────────────────────
print("\nW7. Tắt cả agent ops → nghiệp vụ của nó bị chặn; bật lại thì phục hồi")
set_agent("ops", enabled=False)
r = chat(anon, "kiểm tra số dư tài khoản")
check(
    "W7-disabled-agent-blocks",
    r.get("route") == "escalation",
    f"route={r.get('route')}",
)
_, saved = set_agent("ops", enabled=True, enabled_skills=ALL_OPS)
check("W7-reenabled", saved["agent"]["enabled"], saved["agent"]["enabled"])

# ─── Tổng kết ───────────────────────────────────────────────────────────────────
passed = sum(results)
total = len(results)
print(f"\n{'='*50}\nKẾT QUẢ: {passed}/{total} pass")
sys.exit(0 if passed == total else 1)
