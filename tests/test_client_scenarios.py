"""Client scenario tests – khớp với yêu cầu khách hàng email 2026-08-19.

Ba kịch bản:
  1. Customer Service     (ưu tiên 2) – tư vấn thẻ cho người chưa đăng nhập
  2. Automated Cross-Selling (ưu tiên 1) – đề xuất chủ động sau xác minh
  3. Card Operations      (ưu tiên 3) – đóng thẻ, điều chỉnh hạn mức, quyền lợi

Credentials từ data/accounts.json (đọc động – không hard-code):
  Nguyen Van An   CUS-100301  MASS       có ưu đãi XS-CLASSIC-2026Q3
  Tran Thi Bich   CUS-100308  MASS       thẻ ngủ đông + XS-CLASSIC-2026Q3
  Hoang Thi Ha    CUS-100329  MASS       thẻ tạm khoá
  Le Minh Chau    CUS-100315  MASS       thẻ inactive (CARD-ACTIVATION)
"""

import sys
import os

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app import db, cards as cards_mod
from app.router import Router


# ── helpers ──────────────────────────────────────────────────────────────────

def cred(profile: str) -> str:
    """Lấy credentials từ fixture – không hard-code để không bị stale."""
    import json
    from pathlib import Path
    data = json.loads(
        (Path(__file__).resolve().parent.parent / "data" / "accounts.json")
        .read_text(encoding="utf-8"))["customers"]
    by_profile = {c["profile"]: c for c in data}
    c = by_profile[profile]
    return f"{c['phone_last4']} {c['national_id_last4']}"


VAN_AN   = cred("travel_offer")    # CUS-100301 MASS, có XS-CLASSIC-2026Q3
BICH     = cred("dormant_card")    # CUS-100308 MASS, thẻ ngủ đông
HA       = cred("frozen_card")     # CUS-100329 MASS, thẻ tạm khoá
CHAU     = cred("inactive_card")   # CUS-100315 MASS, thẻ chưa kích hoạt


def run(router: Router, turns: list[str]) -> tuple:
    """Chạy một cuộc hội thoại, trả về (session, [TurnResult,...])."""
    session = router.sessions.create()
    results = []
    for t in turns:
        r = router.handle_turn(session, t)
        results.append(r)
    return session, results


# ── test registry ─────────────────────────────────────────────────────────────

_TESTS: list[tuple[str, callable]] = []


def test(name: str):
    def decorator(fn):
        _TESTS.append((name, fn))
        return fn
    return decorator


# ═══════════════════════════════════════════════════════════════════════════════
# SCENARIO 1 – CUSTOMER SERVICE
# ═══════════════════════════════════════════════════════════════════════════════

@test("1A-step1: Bot KHÔNG hỏi xác minh khi chưa xác định – hỏi sở thích thay")
def t1a_step1(router: Router) -> list[str]:
    session, results = run(router, ["tôi muốn tìm thẻ phù hợp với mua sắm online"])
    r = results[-1]
    errs = []
    if session.verified:
        errs.append("bot đã đặt session.verified=True – không nên xác minh cho luồng tư vấn")
    if "xác minh" in r.text.lower() or "cmnd" in r.text.lower() or "cccd" in r.text.lower():
        errs.append(f"bot yêu cầu xác minh danh tính trong câu trả lời: {r.text[:100]}")
    if not session.pending_flow == "cross_sell_interest":
        errs.append(f"pending_flow nên là 'cross_sell_interest', thực tế: {session.pending_flow!r}")
    return errs


@test("1A-step2: Sau khi trả lời sở thích, bot giới thiệu Thẻ Classic cho mua sắm online")
def t1a_step2(router: Router) -> list[str]:
    session, results = run(router, [
        "tôi muốn tìm thẻ phù hợp với mua sắm online",
        "tôi hay mua hàng Shopee, Lazada",
    ])
    r = results[-1]
    errs = []
    if "Classic" not in r.text:
        errs.append(f"Kỳ vọng 'Classic' trong câu trả lời, nhận được: {r.text[:200]}")
    if r.route not in ("deterministic",):
        errs.append(f"route phải là 'deterministic', nhận được: {r.route!r}")
    if session.pending_flow:
        errs.append(f"flow chưa được reset sau khi trả lời: pending_flow={session.pending_flow!r}")
    return errs


@test("1A-step2: Câu trả lời có disclaimer (không bịa)")
def t1a_disclaimer(router: Router) -> list[str]:
    session, results = run(router, [
        "tôi muốn tìm thẻ phù hợp",
        "mua sắm online Shopee",
    ])
    r = results[-1]
    # disclaimer thường là chữ nghiêng phía cuối (có từ "lưu ý" hoặc dấu *)
    has_disclaimer = "_" in r.text or "*" in r.text or "lưu ý" in r.text.lower()
    if not has_disclaimer:
        return [f"Kỳ vọng có disclaimer, không tìm thấy: {r.text[-100:]}"]
    return []


@test("1B: Golf → Thẻ Infinite")
def t1b(router: Router) -> list[str]:
    session, results = run(router, [
        "tôi thích chơi golf, thẻ nào phù hợp với tôi?",
        "golf và du lịch nước ngoài",
    ])
    r = results[-1]
    errs = []
    if "Infinite" not in r.text:
        errs.append(f"Kỳ vọng 'Infinite', nhận được: {r.text[:200]}")
    if "Classic" in r.text and "Infinite" not in r.text:
        errs.append("Giới thiệu Classic thay vì Infinite cho sở thích golf")
    return errs


@test("1C: So sánh Classic và Platinum → route deterministic, có đủ 2 thẻ")
def t1c(router: Router) -> list[str]:
    session, results = run(router, ["Classic và Platinum khác nhau chỗ nào?"])
    r = results[-1]
    errs = []
    if "Classic" not in r.text:
        errs.append("Không thấy thông tin Classic trong so sánh")
    if "Platinum" not in r.text:
        errs.append("Không thấy thông tin Platinum trong so sánh")
    if r.route not in ("deterministic",):
        errs.append(f"route phải là 'deterministic', nhận được: {r.route!r}")
    return errs


@test("1D: Ưu đãi chào mừng Platinum từ KB")
def t1d(router: Router) -> list[str]:
    session, results = run(router, ["thẻ Platinum có ưu đãi gì khi mở thẻ lần đầu?"])
    r = results[-1]
    errs = []
    # Ưu đãi chào mừng Platinum: hoàn 10%, 5 giao dịch đầu, 500.000 VND
    if "500" not in r.text and "Platinum" not in r.text:
        errs.append(f"Câu trả lời không đề cập đúng ưu đãi Platinum: {r.text[:200]}")
    if r.route not in ("rag", "deterministic"):
        errs.append(f"Kỳ vọng route rag hoặc deterministic, nhận được: {r.route!r}")
    return errs


# ═══════════════════════════════════════════════════════════════════════════════
# SCENARIO 2 – AUTOMATED CROSS-SELLING
# ═══════════════════════════════════════════════════════════════════════════════

@test("2A-verify: Xác minh Nguyen Van An thành công")
def t2a_verify(router: Router) -> list[str]:
    session, results = run(router, [
        "tôi muốn kiểm tra số dư",
        VAN_AN,
    ])
    errs = []
    if not session.verified:
        errs.append("Xác minh thất bại – session.verified=False")
    if session.customer_id != "CUS-100301":
        errs.append(f"customer_id sai: {session.customer_id!r}")
    r = results[-1]
    if "An" not in r.text and "xác minh" not in r.text.lower():
        errs.append(f"Câu trả lời sau xác minh không có tên khách: {r.text[:100]}")
    return errs


@test("2A-proactive: Sau xác minh, bot CHỦ ĐỘNG đề xuất ưu đãi Classic (proactive offer)")
def t2a_proactive(router: Router) -> list[str]:
    session, results = run(router, [
        "xin chào",
        "tôi muốn kiểm tra số dư",
        VAN_AN,
    ])
    # Tìm turn chứa xác minh (turn cuối cùng)
    r = results[-1]
    errs = []
    combined_text = " ".join(res.text for res in results)
    # Proactive offer nên xuất hiện sau khi verified
    if "Classic" not in combined_text and "Thẻ Classic" not in combined_text:
        errs.append(
            "FAIL: Bot không chủ động đề xuất Thẻ Classic sau khi xác minh. "
            f"Toàn bộ text: {combined_text[:300]}"
        )
    # Kiểm tra không có sản phẩm giả
    for fake in ("Premier Travel", "Cashback Hàng ngày", "Cashback", "Premier"):
        if fake in combined_text:
            errs.append(f"Bot đề xuất sản phẩm không tồn tại: '{fake}'")
    return errs


@test("2A-interest: Bot hỏi sở thích sau khi khách muốn biết thêm về thẻ")
def t2a_interest(router: Router) -> list[str]:
    session, results = run(router, [
        "tôi muốn kiểm tra số dư",
        VAN_AN,
        "tôi muốn biết thêm về thẻ này",
    ])
    r = results[-1]
    errs = []
    keywords = ["mua sắm", "ăn uống", "du lịch", "golf", "quan tâm"]
    if not any(k in r.text.lower() for k in keywords):
        errs.append(f"Bot không hỏi sở thích chi tiêu: {r.text[:200]}")
    return errs


@test("2A-fit: Sau khi trả lời sở thích, bot giải thích tại sao Classic phù hợp")
def t2a_fit(router: Router) -> list[str]:
    session, results = run(router, [
        "tôi muốn kiểm tra số dư",
        VAN_AN,
        "tôi muốn biết thêm về thẻ này",
        "tôi hay mua sắm online",
    ])
    r = results[-1]
    errs = []
    if "Classic" not in r.text:
        errs.append(f"Bot không nhắc Thẻ Classic khi giải thích: {r.text[:200]}")
    if "hoàn tiền" not in r.text.lower() and "5%" not in r.text and "mua sắm" not in r.text.lower():
        errs.append(f"Bot không giải thích quyền lợi cụ thể: {r.text[:200]}")
    return errs


@test("2B: Khách hỏi 'tôi có ưu đãi gì' sau xác minh → hiển thị XS-CLASSIC-2026Q3")
def t2b(router: Router) -> list[str]:
    # Xác minh qua balance_inquiry (trong PERSONAL_INTENTS) để session được verified
    # trước khi hỏi offers. card_offers không nằm trong PERSONAL_INTENTS nên sẽ
    # xử lý ngay với session đã verified.
    session, results = run(router, [
        "kiểm tra số dư tài khoản",
        VAN_AN,
        "tôi có ưu đãi gì không?",
    ])
    r = results[-1]
    errs = []
    if "Classic" not in r.text:
        errs.append(f"Không tìm thấy ưu đãi Thẻ Classic: {r.text[:200]}")
    if "không có ưu đãi" in r.text.lower():
        errs.append("Bot báo không có ưu đãi trong khi CUS-100301 có XS-CLASSIC-2026Q3")
    for fake in ("Premier Travel", "Cashback Hàng ngày"):
        if fake in r.text:
            errs.append(f"Bot nhắc sản phẩm không tồn tại: '{fake}'")
    return errs


@test("2C: Khách hỏi số dư → bot trả lời số dư (sau xác minh)")
def t2c(router: Router) -> list[str]:
    session, results = run(router, [
        "số dư tài khoản",
        VAN_AN,
    ])
    r = results[-1]
    errs = []
    if "số dư" not in r.text.lower() and "balance" not in r.text.lower() and "VND" not in r.text:
        errs.append(f"Kỳ vọng số dư trong câu trả lời: {r.text[:200]}")
    return errs


@test("2D: Hoang Thi Ha không có cross-sell campaign → 'không có ưu đãi'")
def t2d(router: Router) -> list[str]:
    session, results = run(router, [
        "tôi có ưu đãi gì không?",
        HA,
        "tôi có ưu đãi gì không?",
    ])
    r = results[-1]
    errs = []
    # Sau xác minh HA không có cross_sell campaign → offers_for returns []
    if "Classic" in r.text or "Platinum" in r.text:
        errs.append(f"Bot bịa ưu đãi cho CUS-100329: {r.text[:200]}")
    return errs


# ═══════════════════════════════════════════════════════════════════════════════
# SCENARIO 3 – CARD OPERATIONS
# ═══════════════════════════════════════════════════════════════════════════════

@test("3A: Xem quyền lợi thẻ – hiển thị reward_scheme của Classic cho CUS-100301")
def t3a(router: Router) -> list[str]:
    session, results = run(router, [
        "thẻ của tôi có quyền lợi gì?",
        VAN_AN,
    ])
    r = results[-1]
    errs = []
    # Classic reward_scheme: hoàn tiền 3%, 5%, tối đa 300.000
    if "hoàn tiền" not in r.text.lower():
        errs.append(f"Không có thông tin hoàn tiền: {r.text[:200]}")
    if "300" not in r.text and "Classic" not in r.text:
        errs.append(f"Không thấy quyền lợi Classic: {r.text[:200]}")
    if r.route not in ("deterministic",):
        errs.append(f"route phải là 'deterministic', nhận được: {r.route!r}")
    return errs


@test("3B-step1: Yêu cầu tăng hạn mức – bot hỏi số tiền mới")
def t3b_step1(router: Router) -> list[str]:
    session, results = run(router, [
        "tôi muốn tăng hạn mức thẻ",
        VAN_AN,
    ])
    r = results[-1]
    errs = []
    # Sau xác minh, bot nên hỏi số hạn mức mới
    if "hạn mức" not in r.text.lower():
        errs.append(f"Bot không hỏi về hạn mức: {r.text[:200]}")
    if session.pending_flow != "card_limit_adjust":
        errs.append(f"pending_flow phải là 'card_limit_adjust', nhận được: {session.pending_flow!r}")
    return errs


@test("3B-step2: Trả lời '50 triệu' → ghi nhận yêu cầu, mã LMT-, báo CHỜ DUYỆT")
def t3b_step2(router: Router) -> list[str]:
    session, results = run(router, [
        "tôi muốn tăng hạn mức thẻ",
        VAN_AN,
        "50 triệu",
    ])
    r = results[-1]
    errs = []
    if "LMT-" not in r.text:
        errs.append(f"Không có mã tham chiếu LMT-: {r.text[:200]}")
    if "chờ duyệt" not in r.text.lower() and "xét duyệt" not in r.text.lower():
        errs.append(f"Bot không ghi rõ là yêu cầu chờ duyệt: {r.text[:200]}")
    if "50.000.000" not in r.text and "50,000,000" not in r.text:
        errs.append(f"Bot không xác nhận số tiền 50.000.000 VND: {r.text[:200]}")
    if session.pending_flow:
        errs.append(f"flow chưa reset sau khi hoàn thành: {session.pending_flow!r}")
    return errs


@test("3C-step1: Đóng thẻ – bot hỏi xác nhận")
def t3c_step1(router: Router) -> list[str]:
    # Reset DB trước để đảm bảo thẻ active
    db.reset()
    cards_mod.reset()
    session, results = run(router, [
        "tôi muốn đóng thẻ",
        VAN_AN,
    ])
    r = results[-1]
    errs = []
    if "đóng thẻ" not in r.text.lower() and "chắc" not in r.text.lower() and "xác nhận" not in r.text.lower():
        errs.append(f"Bot không hỏi xác nhận đóng thẻ: {r.text[:200]}")
    if session.pending_flow != "card_close":
        errs.append(f"pending_flow phải là 'card_close', nhận được: {session.pending_flow!r}")
    return errs


@test("3C-step2: Trả lời 'có' → đóng thẻ thành công, mã CLS-")
def t3c_step2(router: Router) -> list[str]:
    db.reset()
    cards_mod.reset()
    session, results = run(router, [
        "tôi muốn đóng thẻ",
        VAN_AN,
        "có",
    ])
    r = results[-1]
    errs = []
    if "CLS-" not in r.text:
        errs.append(f"Không có mã tham chiếu CLS-: {r.text[:200]}")
    if "đóng" not in r.text.lower():
        errs.append(f"Bot không xác nhận đã đóng thẻ: {r.text[:200]}")
    if session.pending_flow:
        errs.append(f"flow chưa reset sau khi đóng thẻ: {session.pending_flow!r}")
    return errs


@test("3C-step3: Đóng thẻ lần 2 trên cùng khách → báo thẻ không còn active")
def t3c_step3(router: Router) -> list[str]:
    db.reset()
    cards_mod.reset()
    # Lần 1: đóng thẻ thành công
    run(router, ["tôi muốn đóng thẻ", VAN_AN, "có"])
    # Lần 2: thử đóng lại (session mới, cùng khách)
    session2, results2 = run(router, ["tôi muốn đóng thẻ", VAN_AN, "có"])
    combined = " ".join(r.text for r in results2)
    errs = []
    # Một trong hai kết quả hợp lệ: (a) thông báo "đã đóng trước đó"
    # hoặc (b) "không tìm thấy thẻ đang hoạt động" – cả hai đều đúng về mặt
    # nghiệp vụ (thẻ đã ở trạng thái closed, không còn active)
    has_closed_msg = (
        "đã đóng" in combined.lower()
        or "trước đó" in combined.lower()
        or "không tìm thấy thẻ" in combined.lower()
        or "không hoạt động" in combined.lower()
        or "không có thẻ" in combined.lower()
    )
    if not has_closed_msg:
        errs.append(f"Bot không thông báo thẻ đã đóng/không active: {combined[:300]}")
    return errs


@test("3D: Thoát khỏi luồng đóng thẻ, hỏi số dư → bot bỏ flow và trả lời số dư")
def t3d(router: Router) -> list[str]:
    db.reset()
    cards_mod.reset()
    session, results = run(router, [
        "tôi muốn đóng thẻ",
        VAN_AN,
        "thôi không cần, hỏi về số dư thôi",
    ])
    r = results[-1]
    errs = []
    if session.pending_flow == "card_close":
        errs.append("Bot không thoát khỏi luồng card_close sau khi khách từ chối")
    if "số dư" not in r.text.lower() and "VND" not in r.text:
        errs.append(f"Bot không trả lời về số dư sau khi thoát luồng: {r.text[:200]}")
    return errs


@test("3E-step1: Hoang Thi Ha có thẻ tạm khoá → bot xác nhận và hỏi mở khoá")
def t3e_step1(router: Router) -> list[str]:
    session, results = run(router, [
        "tôi muốn mở khoá thẻ",
        HA,
    ])
    r = results[-1]
    errs = []
    if "mở" not in r.text.lower() and "khoá" not in r.text.lower() and "khóa" not in r.text.lower():
        errs.append(f"Bot không đề cập đến mở khoá thẻ: {r.text[:200]}")
    if session.pending_flow != "unfreeze":
        errs.append(f"pending_flow phải là 'unfreeze', nhận được: {session.pending_flow!r}")
    return errs


@test("3E-step2: Xác nhận 'có' → thẻ được mở khoá, mã tham chiếu có trong câu trả lời")
def t3e_step2(router: Router) -> list[str]:
    session, results = run(router, [
        "tôi muốn mở khoá thẻ",
        HA,
        "có",
    ])
    r = results[-1]
    errs = []
    # Confirmation có thể dùng "mở khoá", "kích hoạt trở lại", hoặc "hoạt động trở lại"
    unfrozen_keywords = ["mở", "khoá", "khóa", "kích hoạt", "hoạt động trở lại", "active"]
    if not any(k in r.text.lower() for k in unfrozen_keywords):
        errs.append(f"Bot không xác nhận thẻ đã được mở khoá/kích hoạt: {r.text[:200]}")
    # Mã tham chiếu: UNF- hoặc bất kỳ chuỗi reference nào
    if not any(marker in r.text for marker in ("UNF-", "mã tham chiếu", "tham chiếu")):
        errs.append(f"Không thấy mã tham chiếu trong câu trả lời: {r.text[:200]}")
    if session.pending_flow:
        errs.append(f"flow chưa reset sau khi mở khoá: {session.pending_flow!r}")
    return errs


# ═══════════════════════════════════════════════════════════════════════════════
# KIỂM TRA CHÉO – SẢN PHẨM VÀ KB
# ═══════════════════════════════════════════════════════════════════════════════

@test("KB-1: Hỏi về BYOR Signature → có câu trả lời có nội dung phù hợp")
def tkb1(router: Router) -> list[str]:
    session, results = run(router, ["thẻ Signature có chương trình BYOR là gì?"])
    r = results[-1]
    errs = []
    if "BYOR" not in r.text and "Build Your Own" not in r.text and "linh hoạt" not in r.text.lower():
        errs.append(f"Câu trả lời không giải thích BYOR: {r.text[:200]}")
    if r.route == "escalation":
        errs.append("Bot escalate thay vì trả lời từ KB")
    return errs


@test("KB-2: Phí thường niên Infinite → 5.999.000 VND")
def tkb2(router: Router) -> list[str]:
    session, results = run(router, ["phí thường niên thẻ Infinite bao nhiêu?"])
    r = results[-1]
    errs = []
    if "5.999" not in r.text and "5,999" not in r.text:
        errs.append(f"Không thấy '5.999.000' trong câu trả lời: {r.text[:200]}")
    return errs


@test("KB-3: Miễn lãi bao nhiêu ngày → 45 ngày")
def tkb3(router: Router) -> list[str]:
    session, results = run(router, ["thẻ tín dụng miễn lãi bao nhiêu ngày?"])
    r = results[-1]
    errs = []
    if "45" not in r.text:
        errs.append(f"Không thấy '45 ngày' trong câu trả lời: {r.text[:200]}")
    return errs


@test("KB-4: Hoàn tiền ghi có sau bao lâu → 3-5 ngày làm việc")
def tkb4(router: Router) -> list[str]:
    session, results = run(router, ["hoàn tiền được ghi có sau bao lâu?"])
    r = results[-1]
    errs = []
    if "3" not in r.text or "5" not in r.text:
        errs.append(f"Không thấy '3-5 ngày' trong câu trả lời: {r.text[:200]}")
    if r.route == "escalation":
        errs.append("Bot escalate thay vì trả lời từ KB")
    return errs


# ═══════════════════════════════════════════════════════════════════════════════
# RUNNER
# ═══════════════════════════════════════════════════════════════════════════════

def main() -> int:
    print("Khởi tạo Router và reset dữ liệu demo...")
    db.reset()
    cards_mod.reset()
    router = Router()

    pass_count = 0
    fail_count = 0

    print(f"\n{'═' * 78}")
    print(f"{'CLIENT SCENARIO TESTS':^78}")
    print(f"{'═' * 78}")

    for name, fn in _TESTS:
        # Mỗi test nhận router nhưng tạo session riêng bên trong
        try:
            errs = fn(router)
        except Exception as exc:
            errs = [f"EXCEPTION: {type(exc).__name__}: {exc}"]

        if errs:
            print(f"\n  FAIL  {name}")
            for e in errs:
                print(f"        ↳ {e}")
            fail_count += 1
        else:
            print(f"  PASS  {name}")
            pass_count += 1

    total = pass_count + fail_count
    print(f"\n{'═' * 78}")
    print(f"Kết quả: {pass_count}/{total} passed  ({fail_count} failed)")
    print(f"{'═' * 78}")
    return 1 if fail_count else 0


if __name__ == "__main__":
    raise SystemExit(main())
