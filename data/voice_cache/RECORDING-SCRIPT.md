# Bảng thu âm giọng nói cho chatbot (Voice recording script)

> Dùng khi tự thu âm thay cho ElevenLabs. File đặt vào `data/voice_cache/`.

## Cách hoạt động

- **Câu tĩnh:** thu 1 file cho mỗi câu, đặt tên đúng `<TÊN_HẰNG>.mp3` (vd `GREETING.mp3`) trong `data/voice_cache/`. Hệ thống tự khớp text→file và **phát y nguyên file thu**, không đụng tới. Đã có file cùng tên thì không bị ghi đè/không bị TTS thay.
- **Định dạng:** mp3 (giống các file mẫu sẵn có). Đọc đúng câu ở cột "NỘI DUNG ĐỌC".
- **Câu động** (có tên khách, số dư, tên thẻ, mã tham chiếu...): KHÔNG thu sẵn trọn câu được vì đổi theo từng khách — xem Mục 2.

---

## Mục 1 — CÂU TĨNH (thu sẵn được) — 34 file

| # | Tên file | Nội dung đọc |
|---|----------|--------------|
| 1 | `ACTIVATION_NONE_PENDING.mp3` | Các thẻ trong hồ sơ của quý khách đều đã được kích hoạt. Nếu có giao dịch nào bị từ chối, có thể do nguyên nhân khác - tôi sẽ kiểm tra giúp quý khách. |
| 2 | `CARD_ACTION_CANCELLED.mp3` | Vâng ạ, tôi giữ nguyên trạng thái thẻ cho quý khách. |
| 3 | `CARD_CLOSE_ALREADY_CLOSED.mp3` | Thẻ này đã được đóng từ trước đó. |
| 4 | `CARD_CLOSE_CANCELLED.mp3` | Tôi đã huỷ yêu cầu - thẻ của quý khách vẫn hoạt động bình thường. |
| 5 | `CARD_CLOSE_CONFIRM_ASK.mp3` | Quý khách có chắc muốn đóng thẻ này không? Vui lòng xác nhận có hoặc không. |
| 6 | `CARD_CLOSE_NONE.mp3` | Tôi không tìm thấy thẻ nào trên hồ sơ của quý khách. |
| 7 | `CARD_CLOSE_UNCLEAR.mp3` | Quý khách xác nhận muốn đóng thẻ này không ạ? Vui lòng trả lời có hoặc không. |
| 8 | `CARD_NONE_LEFT_FREEZE.mp3` | Quý khách hiện không có thẻ nào đang hoạt động để tạm khóa. |
| 9 | `CARD_NONE_LEFT_REPORT_LOST.mp3` | Tôi đã kiểm tra, hiện quý khách không có thẻ nào để khóa. |
| 10 | `CARD_NONE_LEFT_UNFREEZE.mp3` | Hiện không có thẻ nào đang tạm khóa. Nếu thẻ đã được báo mất thì đã bị khóa vĩnh viễn, không thể mở lại - tôi có thể kiểm tra thẻ thay thế giúp quý khách. |
| 11 | `CROSS_SELL_ASK_INTEREST_UNVERIFIED.mp3` | Quý khách thường mua sắm trên nền tảng nào, hoặc chi tiêu nhiều nhất ở lĩnh vực nào ạ? |
| 12 | `CROSS_SELL_DISCOVER.mp3` | Dạ, cảm ơn quý khách đã chia sẻ. Để gợi ý thẻ phù hợp nhất, cho tôi hỏi thêm một chút ạ: Hằng ngày quý khách chi tiêu nhiều nhất cho khoản nào (ăn uống, mua sắm online, di chuyển, giải trí...)? Quý khách ưu tiên hoàn tiền hay tích điểm ạ? |
| 13 | `CROSS_SELL_NO_OPEN_MATCH.mp3` | Với nhu cầu quý khách vừa chia sẻ, hiện chưa có ưu đãi mở thẻ riêng. Hai thẻ đang mở đăng ký: Thẻ Classic – hoàn tiền 5% mua sắm online (Shopee, Lazada, Tiki) & 3% ăn uống, cà phê. Thẻ Platinum – hoàn tiền 5% ăn uống, di chuyển, rạp chiếu phim. Quý khách quan tâm thẻ nào, hoặc cho tôi biết thêm khoản chi tiêu hằng ngày để tôi tư vấn ạ? |
| 14 | `ESCALATION_DECLINED.mp3` | Vâng ạ, tôi tiếp tục hỗ trợ quý khách. Quý khách cần hỏi thêm điều gì không? |
| 15 | `ESCALATION_MESSAGE.mp3` | Tôi xin kết nối quý khách với một chuyên viên - chuyên viên sẽ thấy đầy đủ nội dung cuộc trò chuyện này nên quý khách không cần giải thích lại. Quý khách đang trong hàng chờ nhân viên hỗ trợ. |
| 16 | `ESCALATION_OFFER_BODY.mp3` | Thành thật mà nói, tôi chưa có thông tin chính xác về vấn đề này. Quý khách có muốn tôi kết nối với một chuyên viên để hỗ trợ thêm không? Chuyên viên sẽ thấy đầy đủ nội dung cuộc trò chuyện này nên quý khách không cần giải thích lại. Hoặc quý khách có thể hỏi tôi câu khác. |
| 17 | `GOODBYE.mp3` | Cảm ơn quý khách đã sử dụng dịch vụ hôm nay. Chúc quý khách một ngày tốt lành! |
| 18 | `GREETING.mp3` | Trợ lý ảo ABC Bank hân hạnh được phục vụ quý khách. Tôi có thể hỗ trợ quý khách kiểm tra số dư và giao dịch, khóa hoặc mở khóa thẻ, tra cứu hồ sơ vay, và giải đáp về sản phẩm, biểu phí của ngân hàng. Quý khách cần hỗ trợ gì ạ? |
| 19 | `HUMAN_AGENT_HANDOFF.mp3` | Vâng ạ, tôi xin kết nối quý khách với chuyên viên ngay. |
| 20 | `LEAVE_NOOP.mp3` | Quý khách đang trò chuyện với Trợ lý ảo ABC Bank. Tôi có thể hỗ trợ gì cho quý khách? |
| 21 | `LEFT_AGENT_NO_NAME.mp3` | Quý khách đã quay lại với trợ lý ảo, tôi xin đưa quý khách ra khỏi hàng chờ. Tôi có thể hỗ trợ gì tiếp theo cho quý khách? |
| 22 | `LIMIT_ASK_AMOUNT.mp3` | Quý khách muốn nâng hạn mức mới lên bao nhiêu ạ? |
| 23 | `LIMIT_INVALID.mp3` | Tôi chưa nhận được số hạn mức hợp lệ, quý khách vui lòng gửi lại giúp tôi nhé. |
| 24 | `NO_LOAN_ON_FILE.mp3` | Tôi không thấy hồ sơ vay nào đang mở trên tài khoản của quý khách. Nếu quý khách vừa nộp hồ sơ tại chi nhánh trong 24 giờ qua, có thể hệ thống chưa kịp đồng bộ - tôi xin chuyển quý khách sang bộ phận tín dụng kiểm tra lại. |
| 25 | `NO_TRANSACTIONS.mp3` | Tôi không thấy giao dịch gần đây nào trên tài khoản của quý khách. |
| 26 | `OFFERS_NONE.mp3` | Quý khách hiện chưa có ưu đãi nào. Tôi có thể hỗ trợ thêm gì cho quý khách không? |
| 27 | `REQUEUED_MESSAGE.mp3` | Câu hỏi này cũng nằm ngoài phạm vi tôi có thể trả lời - tôi đã ghi chú lại để chuyên viên nắm khi tiếp nhận quý khách. Quý khách vẫn đang trong hàng chờ. |
| 28 | `RESTRICTED_RESPONSE.mp3` | Tôi xin phép không tư vấn về đầu tư, thuế hay pháp lý - những vấn đề này cần chuyên gia có chứng chỉ hành nghề mới đưa ra được thông tin chính xác. Tôi có thể kết nối quý khách với chuyên viên ngân hàng để trao đổi về sản phẩm, hoặc hỗ trợ quý khách các câu hỏi về tài khoản, thẻ hay hồ sơ. |
| 29 | `SMALLTALK.mp3` | Tôi vẫn đang lắng nghe quý khách đây ạ. Quý khách cần hỏi thêm điều gì không? |
| 30 | `VERIFICATION_ASK_CCCD.mp3` | Cảm ơn quý khách. Quý khách cho tôi thêm số căn cước công dân hoặc chứng minh nhân dân (đủ 12 số) để hoàn tất xác minh nhé. |
| 31 | `VERIFICATION_CCCD_FORMAT_RETRY.mp3` | Thông tin chưa đúng định dạng - tôi cần đúng 12 số căn cước công dân hoặc chứng minh nhân dân. Quý khách vui lòng không gửi số thẻ đầy đủ hay mã PIN - tôi không bao giờ cần những thông tin đó. |
| 32 | `VERIFICATION_FAILED.mp3` | Tôi xin lỗi vì chưa xác minh được thông tin của quý khách, nên không thể tiếp tục thử thêm. Tôi xin kết nối quý khách với chuyên viên hỗ trợ trực tiếp. |
| 33 | `VERIFICATION_PHONE_FORMAT_RETRY.mp3` | Thông tin chưa đúng định dạng - tôi cần đúng 10 số điện thoại, không có khoảng trắng hay ký tự khác. |
| 34 | `VERIFICATION_PROMPT.mp3` | Tôi rất sẵn lòng hỗ trợ quý khách. Trước tiên, tôi cần xác minh nhanh để đảm bảo đúng là quý khách. Quý khách vui lòng cho tôi số điện thoại đã đăng ký (đủ 10 số) nhé. |

---

## Mục 2 — CÂU ĐỘNG (có dữ liệu riêng của khách — KHÔNG thu sẵn trọn câu)

Các câu dưới đây thay đổi theo từng khách. Phần trong `{ }` là dữ liệu động.
Với demo không có ElevenLabs, các câu này sẽ **đọc bằng gTTS** (giọng máy). 
Nếu muốn giọng thu cho phần cố định, có thể thu riêng câu mở đầu rồi để số/tên đọc bằng gTTS — nhưng đơn giản nhất là để cả câu này dùng gTTS.

| # | Tình huống | Mẫu câu (động) |
|---|-----------|----------------|
| 1 | Xác minh thành công | Cảm ơn quý khách {HỌ TÊN}, quý khách đã xác minh thành công. {lời nhắc lần trước nếu có} |
| 2 | Số dư tài khoản | Đây là số dư tài khoản của quý khách tính đến hôm nay, {TÊN}: - {loại TK} {****mask} - số dư {số tiền} VND, khả dụng {số tiền} VND |
| 3 | Lịch sử giao dịch | Đây là các giao dịch gần đây của quý khách: - {ngày} · {mô tả} · {số tiền} VND (lặp lại theo từng giao dịch) |
| 4 | Tổng quan tài khoản | Quý khách là {HỌ TÊN} ({mã KH}), đã được xác minh. Đây là các sản phẩm quý khách đang có: - Thẻ {loại} {****mask} {trạng thái} |
| 5 | Xác nhận thao tác thẻ | Quý khách trả lời có để {khóa/mở khóa/báo mất} thẻ, hoặc không để giữ nguyên nhé. |
| 6 | Đóng thẻ xong | Đã đóng thẻ của quý khách (mã tham chiếu {mã}). |
| 7 | Yêu cầu nâng hạn mức | Tôi đã ghi nhận yêu cầu điều chỉnh hạn mức lên {số tiền} (mã tham chiếu {mã}) rồi nhé. Đây là yêu cầu chờ duyệt - hạn mức hiện tại chưa thay đổi cho tới khi có kết quả xét duyệt. |
| 8 | Thẻ ngủ đông (kích hoạt lại) | Thẻ {loại} {****mask} của quý khách đang ở trạng thái ngủ đông do không có giao dịch trong thời gian dài. {ưu đãi + hướng dẫn kích hoạt} |
| 9 | Thẻ chưa kích hoạt | Thẻ {loại} {****mask} của quý khách đã được cấp nhưng chưa kích hoạt. {hướng dẫn kích hoạt} |
| 10 | Gợi ý thẻ (cross-sell) | Thẻ {TÊN THẺ} phù hợp với quý khách: - {các dòng ưu đãi khớp sở thích} |
| 11 | Cross-sell chủ động | Nhân tiện: {tên chiến dịch} – {ưu đãi}. → {nút}: {đường dẫn} |
| 12 | Quyền lợi thẻ | Quyền lợi hiện có trên {TÊN THẺ}: - {các dòng quyền lợi} |
| 13 | Danh sách ưu đãi | Đây là các ưu đãi hiện có trên tài khoản của quý khách hôm nay: - {từng ưu đãi} |

---

## Ghi chú
- Thu xong copy các file `.mp3` vào `data/voice_cache/` — không cần sửa code.
- Kiểm tra nhanh: mở web, bật 🔊, gõ câu tương ứng, nghe có đúng file thu không.
- Câu tĩnh phủ ~90% lời chào/xác minh/từ chối/escalation/cross-sell mở đầu — phần khách nghe nhiều nhất.

curl -X POST http://127.0.0.1:8080/v1/voices \
  -F "name=Giang" \
  -F "file=@/voice_preview_giang - podcast.mp3" \
  -F "denoise=true"