# Kịch bản quay video demo — Giao diện quản trị (Admin)

Mục tiêu: quay một video ngắn (khoảng **3–4 phút**) giới thiệu cho khách hàng
thấy họ có thể **theo dõi và điều khiển hoạt động của trợ lý ảo** qua một giao
diện quản trị duy nhất: trạng thái bot, cấu hình model, giọng nói và kho tri thức.

> Đây là script quay hình cho **giao diện admin**. Kịch bản hội thoại phía khách
> hàng nằm ở `scripts.md`.

---

## 0. Chuẩn bị trước khi quay

- [ ] Chạy server: `python3 server.py`, đợi log báo `LIVE` và TTS/STT sẵn sàng.
- [ ] Mở trình duyệt ở chế độ ẩn danh, phóng to cửa sổ (khuyến nghị 1440×900).
- [ ] Nạp sẵn dữ liệu: đăng nhập admin → tab **Tổng quan & vận hành** →
      bấm **Nạp dữ liệu demo** để bảng số liệu có nội dung.
- [ ] Chuẩn bị sẵn một file `.md` hoặc `.docx` nhỏ để minh hoạ tải tài liệu
      (phần Kho tri thức) — không bắt buộc.
- [ ] Tắt thông báo hệ thống để không lọt vào khung hình.

Tài khoản đăng nhập demo: **`agent` / `demo1234`**.

---

## 1. Mở đầu — Vào bảng điều khiển (khoảng 0:00–0:25)

**Thao tác:**

1. Từ màn hình khách, bấm **Đăng nhập** ở góc trái dưới.
2. Nhập `agent` / `demo1234`, bấm **Đăng nhập**.
3. Dừng ở màn hình **Bảng điều khiển quản trị**.

**Lời thoại gợi ý:**

> "Đây là bảng điều khiển quản trị của trợ lý ảo. Toàn bộ hoạt động của bot —
> từ trạng thái, model đang chạy, giọng nói cho tới kho tri thức — đều được
> theo dõi và điều khiển tại đây, không cần đụng tới code hay dòng lệnh."

---

## 2. Tổng quan & Điều khiển bot (khoảng 0:25–1:15)

**Thao tác:**

1. Trỏ vào khối **Điều khiển bot** ở đầu trang — 4 ô trạng thái:
   **Trạng thái bot**, **Giọng nói (TTS)**, **Nhận giọng nói (STT)**, **Kho tri thức**.
2. Nhấn mạnh các chấm xanh "đang hoạt động / sẵn sàng".
3. Chỉ vào hai nút lối tắt **Cấu hình bot** và **Cài đặt giọng nói**.
4. Lướt xuống dải số liệu vận hành (tỉ lệ tự xử lý, thời gian phản hồi,
   độ chính xác dẫn nguồn, số ca chuyển nhân viên…).

**Lời thoại gợi ý:**

> "Ngay khi vào, khách có thể thấy tình trạng sức khoẻ của bot theo thời gian
> thực: model nào đang chạy, giọng đọc và nhận giọng nói có sẵn sàng không,
> kho tri thức đang có bao nhiêu tài liệu. Bên dưới là các chỉ số vận hành —
> bao nhiêu phần trăm hội thoại bot tự xử lý, tốc độ phản hồi, và mức độ trả lời
> có dẫn nguồn."

**Ghi chú quay:** các số liệu này đọc trực tiếp từ tiến trình đang chạy, nên nếu
bấm **Làm mới** trước khi quay sẽ thấy số cập nhật — có thể quay cận cảnh nút này.

---

## 3. Cấu hình bot (khoảng 1:15–2:05)

**Thao tác:**

1. Bấm tab **Cấu hình bot**.
2. Bên trái: chỉ vào danh sách **Nhà cung cấp LLM** (anthropic, openai, gemini, groq),
   nhấn mạnh cái đang được đánh dấu **· đang dùng**.
3. Bên phải: khối **Cấu hình đang dùng** — chế độ, nhà cung cấp, model, mức suy luận.
4. Minh hoạ đổi **Giọng điệu** (ví dụ từ "chuyên nghiệp" sang "thân thiện"),
   rồi bấm **Áp dụng**.

**Lời thoại gợi ý:**

> "Ở tab Cấu hình bot, khách có thể chọn nhà cung cấp AI và model, điều chỉnh
> mức độ suy luận và giọng điệu trả lời — ví dụ chuyên nghiệp, thân thiện hay
> ngắn gọn. Mọi thay đổi áp dụng ngay cho phiên đang chạy."

**Ghi chú:** phần quản lý khoá API đã được ẩn khỏi giao diện này để bảng điều
khiển gọn và an toàn hơn — khoá được cấu hình sẵn ở phía máy chủ.

---

## 4. Giọng nói (khoảng 2:05–2:50)

**Thao tác:**

1. Bấm tab **Giọng nói**.
2. Chỉ vào ô chọn **Nhà cung cấp** (Voice 1 / Voice 2) và **Giọng đọc**.
3. Chọn một giọng, bấm **Nghe thử** — để micro thu được câu chào mẫu phát ra.
4. Bấm **Áp dụng** để lưu.

**Lời thoại gợi ý:**

> "Bot có thể trả lời bằng giọng nói. Tại đây khách chọn nhà cung cấp và giọng
> đọc, nghe thử ngay trước khi áp dụng. Giọng mới có hiệu lực cho cả khung chat
> lẫn chế độ hội thoại giọng nói mà không cần tải lại trang."

**Ghi chú quay:** bật loa/thu âm hệ thống để câu "Nghe thử" vào được video.

---

## 5. Kho tri thức (khoảng 2:50–3:30)

**Thao tác:**

1. Bấm tab **Kho tri thức**.
2. Chỉ vào danh sách tài liệu và dòng thống kê (số tài liệu / số đoạn trích).
3. Bấm một tài liệu để mở phần **Đoạn trích đã tách** — cho thấy bot "nhìn" nội dung thế nào.
4. (Tuỳ chọn) kéo thả một file `.md`/`.docx` vào ô tải lên để minh hoạ đánh index tức thì.

**Lời thoại gợi ý:**

> "Đây là bộ não kiến thức của bot. Khách tự thêm hoặc chỉnh tài liệu; hệ thống
> tách thành các đoạn trích và đánh index ngay — không cần khởi động lại. Bot chỉ
> trả lời dựa trên đúng những tài liệu này, nên nội dung do khách kiểm soát hoàn toàn."

---

## 6. Kết (khoảng 3:30–3:50)

**Thao tác:** quay lại tab **Tổng quan & vận hành**, dừng ở khối **Điều khiển bot**.

**Lời thoại gợi ý:**

> "Tóm lại: một giao diện duy nhất để khách theo dõi tình trạng bot, đổi model
> và giọng điệu, quản lý giọng nói và kho tri thức — toàn quyền kiểm soát hoạt
> động của trợ lý ảo mà không cần tới đội kỹ thuật."

---

## Phụ lục — Thứ tự tab để quay liền mạch

`Tổng quan & vận hành → Cấu hình bot → Giọng nói → Kho tri thức → Tổng quan & vận hành`

## Phụ lục — Mẹo quay

- Di chuột chậm, dừng lại 1–2 giây ở mỗi khối trước khi bấm.
- Nếu quay lại nhiều lần, bấm **Xoá toàn bộ phiên** rồi **Nạp dữ liệu demo**
  để về trạng thái sạch, số liệu nhất quán giữa các lần quay.
- Tránh để lộ URL ngrok/nội bộ trong khung hình nếu không muốn công khai.
