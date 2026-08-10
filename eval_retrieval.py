"""Retrieval quality harness. Run: python eval_retrieval.py [-v]

Exists so changes to the retrieval pipeline can be argued from numbers instead
of asserted. Every question below is labelled with the passage heading that
should answer it; the harness reports how often retrieval puts that passage
first, how often it appears at all, and - separately - whether out-of-scope
questions are correctly rejected.

That second axis matters as much as the first here. A retriever tuned only for
recall will happily surface a loosely-related passage for a question the corpus
does not cover, and in this application that turns into a grounded-looking
answer to something the bank never documented. Rejection precision is a safety
property, not a metric to trade away for recall.
"""

from __future__ import annotations

import sys

# The Windows console defaults to cp1252, which cannot encode Vietnamese. Now
# that the assistant answers in Vietnamese, printing a reply raised
# UnicodeEncodeError and took the whole test run down - a test suite that dies
# on its own output reports nothing about the code it was meant to check.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")


import sys

from app.retriever import KnowledgeBase

# (question, heading that should answer it). Matched case-insensitively on a
# prefix so the labels survive small wording edits to the corpus.
IN_SCOPE: list[tuple[str, str]] = [
    # the-ghi-no-va-tin-dung.md
    ("thẻ của tôi bị mất tối qua giờ phải làm sao", "Báo mất thẻ"),
    ("tôi có phải chịu trách nhiệm giao dịch người khác thực hiện bằng thẻ bị mất không", "Báo mất thẻ"),
    ("bao lâu thì nhận được thẻ mới", "Thẻ thay thế"),
    ("làm lại thẻ có mất phí không", "Thẻ thay thế"),
    ("một ngày rút được tối đa bao nhiêu tiền ở ATM", "Hạn mức thẻ"),
    ("thanh toán bằng euro thì mất phí gì", "Phí giao dịch ngoại tệ"),
    ("dùng thẻ ở nước ngoài có bị tính phí không", "Phí giao dịch ngoại tệ"),
    ("khi nào thanh toán không tiếp xúc bắt nhập mã PIN", "Thanh toán không tiếp xúc"),

    # vay-va-the-chap.md
    ("hồ sơ vay mua nhà bao lâu mới có kết quả", "Thời gian xử lý hồ sơ"),
    ("vay tiêu dùng bao lâu thì được duyệt", "Thời gian xử lý hồ sơ"),
    ("hồ sơ đang thẩm định nghĩa là gì", "Các trạng thái hồ sơ"),
    ("bị từ chối rồi có nộp lại được không", "Các trạng thái hồ sơ"),
    ("vay tiêu dùng cần giấy tờ gì", "Chứng từ cần nộp"),
    ("tôi tự kinh doanh thì cần nộp chứng từ nào", "Chứng từ cần nộp"),
    ("lãi suất vay mua ô tô hiện nay bao nhiêu", "Lãi suất"),
    ("trả nợ trước hạn có bị phạt không", "Trả nợ trước hạn"),

    # tai-khoan-va-bieu-phi.md
    ("mở tài khoản cần giấy tờ gì", "Mở tài khoản"),
    ("cần gửi tối thiểu bao nhiêu để mở tài khoản tiết kiệm", "Mở tài khoản"),
    ("làm sao để không bị thu phí quản lý tài khoản", "Phí quản lý tài khoản"),
    ("tài khoản sinh viên có mất phí hàng tháng không", "Phí quản lý tài khoản"),
    ("phí thấu chi là bao nhiêu", "Thấu chi"),
    ("chuyển tiền quốc tế mất phí bao nhiêu và bao lâu", "Chuyển tiền"),
    ("chuyển khoản nội bộ có mất phí không", "Chuyển tiền"),
    ("sao kê giấy tính phí thế nào", "Sao kê"),
    ("mấy giờ chi nhánh mở cửa", "Giờ làm việc chi nhánh"),
    ("nửa đêm có khoá thẻ được không", "Giờ làm việc chi nhánh"),

    # ngan-hang-so-va-an-toan.md
    ("tôi quên mật khẩu ngân hàng số", "Đặt lại mật khẩu"),
    ("nhập sai mật khẩu mấy lần thì bị khoá", "Đặt lại mật khẩu"),
    ("ngân hàng có bao giờ hỏi mã OTP không", "Đăng ký thiết bị mới"),
    ("tôi muốn tra soát một giao dịch lạ", "Tra soát giao dịch"),
    ("bao lâu thì được ghi có tạm thời khi tra soát", "Tra soát giao dịch"),
    ("làm sao nhận biết tin nhắn lừa đảo", "Nhận biết lừa đảo"),
    ("nộp séc qua ứng dụng tối đa bao nhiêu một ngày", "Ứng dụng ngân hàng số"),

    # dieu-khoan-tai-khoan-tien-gui.md
    ("số dư bình quân ngày được tính như thế nào", "Điều kiện miễn phí"),
    ("không đủ tiền thì khoản nào bị trừ trước", "Thứ tự thanh toán"),
    ("âm tài khoản một ít có bị thu phí không", "Thấu chi và ngưỡng miễn phí"),
    ("lãi tiết kiệm được tính và trả khi nào", "Lãi tiền gửi"),
    ("đóng tài khoản sớm có mất phí không", "Đóng tài khoản"),
    ("phí rút tiền ATM ngoài hệ thống", "Biểu phí tài khoản"),

    # thoi-diem-su-dung-tien.md
    ("séc số tiền lớn bị giữ bao lâu", "Các trường hợp phong toả ngoại lệ"),
    ("khoản chuyển tiền đến có bị phong toả như séc không", "Các trường hợp phong toả ngoại lệ"),
    ("tài khoản mới mở thì tiền về khi nào dùng được", "Tài khoản mới mở"),
    ("mấy giờ là hết giờ nộp tiền trong ngày", "Ngày làm việc và thời điểm chốt"),
    ("nộp tiền mặt tại quầy khi nào dùng được", "Lịch sử dụng tiền tiêu chuẩn"),
    ("ngân hàng phong toả tiền thì có báo tôi không", "Thông báo khi phong toả"),

    # tra-soat-va-boi-hoan-the.md
    ("tôi có bao nhiêu ngày để tra soát giao dịch thẻ", "Tra soát thẻ ghi nợ"),
    ("báo mất thẻ muộn thì tôi chịu trách nhiệm bao nhiêu", "Trách nhiệm của khách hàng"),
    ("thời hạn gửi yêu cầu bồi hoàn tới tổ chức thẻ", "Quy tắc bồi hoàn"),
    ("tôi có phải liên hệ người bán trước khi tra soát không", "Trước khi mở tra soát"),
    ("nếu tra soát không thành công thì tiền tạm ứng bị thu lại thế nào", "Thu hồi khoản ghi có tạm thời"),

    # thanh-toan-va-chuyen-tien.md
    ("chuyển nhanh 24/7 có huỷ được không", "Thu hồi lệnh chuyển tiền"),
    ("phí đề nghị thu hồi lệnh chuyển tiền", "Thu hồi lệnh chuyển tiền"),
    ("mấy giờ chốt lệnh chuyển tiền quốc tế", "Thời điểm chốt lệnh"),
    ("biên độ tỷ giá khi chuyển tiền quốc tế là bao nhiêu", "Chuyển tiền quốc tế"),
    ("huỷ uỷ nhiệm thu có chấm dứt hợp đồng dịch vụ không", "Lệnh thanh toán định kỳ"),
    ("hạn mức chuyển tiền một ngày của khách hàng phổ thông", "Hạn mức"),

    # gian-lan-va-lua-dao.md
    ("tôi bị lừa chuyển tiền thì có lấy lại được không", "Phân biệt quyết định mọi thứ"),
    ("tài khoản an toàn là gì", "Những điều ngân hàng không bao giờ làm"),
    ("bị lừa rồi thì tôi phải làm gì trước tiên", "Khi khách hàng nghi ngờ đã bị lừa"),
    ("khi nào tôi được bồi hoàn tiền bị lừa", "Bồi hoàn"),

    # khieu-nai-va-quyen-khach-hang.md
    ("tôi muốn khiếu nại thì bao lâu có trả lời", "Tiếp nhận khiếu nại"),
    ("không hài lòng với kết quả khiếu nại thì làm gì tiếp", "Khi khách hàng không đồng ý"),
    ("tôi có quyền yêu cầu ngân hàng xoá dữ liệu của mình không", "Quyền về dữ liệu cá nhân"),

    # thi-truong-the-*.md
    ("phí thường niên thẻ Vietcombank Visa Platinum", "Vietcombank Visa Platinum"),
    ("thời gian miễn lãi 45 ngày tính như thế nào", "Thời gian miễn lãi"),
    ("thẻ VPBank StepUp có hoàn tiền cho Shopee không", "Phần loại trừ"),
    ("thẻ VPBank Lady hoàn tiền tối đa bao nhiêu một năm", "VPBank Lady"),
    ("hạn mức thẻ Techcombank Priority Visa Signature", "Techcombank Priority Visa Signature"),
    ("thẻ MB Hi Collection không in số thẻ thì bất tiện gì", "Mặt thẻ trống"),
    ("phí thường niên thẻ MB JCB là bao nhiêu", "Mâu thuẫn về phí thường niên"),
]

OUT_OF_SCOPE: list[str] = [
    "ngân hàng có bán bảo hiểm nhân thọ không",
    "hôm nay Hà Nội thời tiết thế nào",
    "tôi giao dịch ngoại hối qua ngân hàng được không",
    "đội nào vô địch world cup",
    "mã số doanh nghiệp của ngân hàng là gì",
    "tôi mua tiền điện tử trên app được không",
    "ngân hàng có cho vay mua đất nông nghiệp trồng nho ở Bồ Đào Nha không",
]


def _matches(heading: str, expected: str) -> bool:
    return heading.lower().startswith(expected.lower()[:24])


def evaluate(kb: KnowledgeBase, top_k: int = 3, label: str = "",
             cases: list[tuple[str, str]] | None = None) -> dict:
    cases = cases if cases is not None else IN_SCOPE
    hits_at_1 = hits_at_k = 0
    reciprocal_ranks = 0.0
    misses: list[tuple[str, str, list[str]]] = []

    for question, expected in cases:
        headings = [r.passage.heading for r in kb.search(question, top_k=top_k)]
        rank = next((i for i, h in enumerate(headings, start=1)
                     if _matches(h, expected)), None)
        if rank == 1:
            hits_at_1 += 1
        if rank is not None:
            hits_at_k += 1
            reciprocal_ranks += 1.0 / rank
        else:
            misses.append((question, expected, headings))

    rejected = 0
    false_positives: list[tuple[str, str]] = []
    for question in OUT_OF_SCOPE:
        results = kb.search(question, top_k=top_k)
        if not results:
            rejected += 1
        else:
            false_positives.append((question, results[0].passage.heading))

    total = len(cases) or 1
    return {
        "label": label,
        "precision_at_1": hits_at_1 / total,
        "recall_at_k": hits_at_k / total,
        "mrr": reciprocal_ranks / total,
        "rejection_rate": rejected / (len(OUT_OF_SCOPE) or 1),
        "misses": misses,
        "false_positives": false_positives,
        "top_k": top_k,
        "n": total,
    }


def print_report(report: dict, verbose: bool = False) -> None:
    print(f"\n{'=' * 72}")
    print(f"{report['label'] or 'retrieval'}   (n={report['n']}, top_k={report['top_k']})")
    print("=" * 72)
    print(f"  P@1         {report['precision_at_1']:6.1%}   right passage ranked first")
    print(f"  Recall@{report['top_k']}    {report['recall_at_k']:6.1%}   right passage retrieved at all")
    print(f"  MRR         {report['mrr']:6.3f}")
    print(f"  Rejection   {report['rejection_rate']:6.1%}   out-of-scope correctly returning nothing")

    if verbose and report["misses"]:
        print(f"\n  Missed ({len(report['misses'])}):")
        for question, expected, got in report["misses"]:
            print(f"    - {question}")
            print(f"        want: {expected}")
            print(f"        got : {got or '(nothing)'}")

    if verbose and report["false_positives"]:
        print(f"\n  False positives ({len(report['false_positives'])}):")
        for question, heading in report["false_positives"]:
            print(f"    - {question}  ->  {heading}")


class _RerankedKB:
    """Adapter presenting the reranked pipeline behind the same `search` call.

    Lets the harness measure the reranking path without duplicating the metric
    code, and without the metrics knowing which pipeline produced the results.
    """

    def __init__(self, kb: KnowledgeBase) -> None:
        self.kb = kb
        self.stats = kb.stats

    def search(self, query: str, top_k: int = 3):
        from app.llm import rerank
        candidates = self.kb.search(query, top_k=rerank.CANDIDATE_POOL,
                                    gate=rerank.RECALL_GATE)
        results, _ = rerank.rerank(query, candidates, top_k=top_k)
        return results


def main() -> int:
    verbose = "-v" in sys.argv or "--verbose" in sys.argv
    with_rerank = "--rerank" in sys.argv

    kb = KnowledgeBase()
    print(f"Corpus   : {kb.stats['passages']} passages / {kb.stats['documents']} documents")
    print(f"Labelled : {len(IN_SCOPE)} in-scope, {len(OUT_OF_SCOPE)} out-of-scope")
    print_report(evaluate(kb, top_k=3, label="lexical pipeline"), verbose=verbose)

    if with_rerank:
        from app import llm
        from app.llm import rerank

        info = llm.describe()
        if info["mode"] != "live":
            print(f"\n  --rerank needs a provider: {info['detail']}")
            return 1
        print(f"\n  Reranking with {info['provider']} / {info['model']} "
              f"(pool={rerank.CANDIDATE_POOL}, gate={rerank.RECALL_GATE}, "
              f"min_score={rerank.MIN_SCORE})")
        print("  This makes one API call per question - it will take a minute.")
        print_report(
            evaluate(_RerankedKB(kb), top_k=3, label="+ LLM reranking"),
            verbose=verbose,
        )
    else:
        print("\n  Pass --rerank (with a provider configured) to measure the "
              "reranking stage.")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
