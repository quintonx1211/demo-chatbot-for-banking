# Hướng dẫn cơ chế "kịch bản demo"

Đây là lưới an toàn cho một buổi demo trực tiếp: khi bật, các câu trả lời
tiếp theo trong một cuộc hội thoại được **ép cứng theo đúng nội dung đã soạn
sẵn**, bất kể model, truy xuất, hay dữ liệu khách hàng lúc đó đang thế nào.
Khác hẳn với `TEST-SCENARIOS.txt` (dùng để kiểm tra hệ thống chạy đúng) - file
này và cơ chế đứng sau nó tồn tại để một buổi trình diễn trước khách hàng
không bao giờ bị phá bởi một provider chập chờn, một lần truy xuất trượt,
hay dữ liệu fixture đã đổi khác so với lúc kịch bản được viết.

---

## 1. Cách hoạt động

Ba phần:

- **`data/demo_scripts.json`** - file dữ liệu, chứa các kịch bản có tên, mỗi
  kịch bản là một danh sách các bước theo đúng thứ tự.
- **`app/demo_script.py`** - đọc file, gắn/gỡ một kịch bản vào một session,
  và trả về "bước tiếp theo" mỗi khi được gọi.
- **`app/router.py`** (`Router.handle_turn`) - kiểm tra kịch bản **trước cả
  raw mode**, tức là trước mọi thứ khác trong hệ thống. Nếu session đang có
  kịch bản đang chạy, lượt hỏi hiện tại **không đi qua guardrail, NLU, truy
  xuất hay model** - nó chỉ lấy đúng câu trả lời kế tiếp trong danh sách.

Điểm quan trọng nhất cần hiểu: **cơ chế này không đọc nội dung khách gõ**.
Bất kể khách hàng (hay người demo) gõ gì, miễn có kịch bản đang chạy thì lượt
đó luôn nhận đúng câu tiếp theo trong danh sách. Đây là lựa chọn có chủ đích
- một buổi demo trực tiếp không nên đổ vỡ chỉ vì gõ nhầm một chữ số hay lệch
một từ so với kịch bản đã tập.

Khi kịch bản đã dùng hết các bước, **lượt tiếp theo tự động rơi về xử lý bình
thường** (router/RAG/model như mọi khi) - không cần thao tác gì thêm, và
không có gì báo lỗi. Có thể theo dõi tiến độ qua khung "Kịch bản demo" ngay
trên giao diện (ví dụ "2/3").

Vì được kiểm tra ngay từ đầu `handle_turn`, kịch bản áp dụng cho **mọi kênh**
gọi vào cùng một router - khung chat văn bản lẫn chế độ gọi thoại (voice
call), vì cả hai đều đi qua `/api/chat/stream` → `router.handle_turn`. Câu
trả lời trong kịch bản cũng được đọc thành tiếng bình thường qua TTS, giống
như mọi câu trả lời khác.

---

## 2. Cách bật/dùng khi demo trực tiếp

1. Đăng nhập bằng tài khoản nhân viên (`agent` / `demo1234`).
2. Bấm **Màn hình khách** (cạnh nút Đăng xuất) để quay về khung chat mà
   không mất phiên đăng nhập.
3. Ngay trên khung chat, dưới header, có khu vực **"Kịch bản demo"** (cạnh
   công tắc "Kiến trúc đầy đủ / LLM thuần"). Chọn kịch bản trong danh sách
   thả xuống, bấm **Bắt đầu**.
4. Từ lượt tiếp theo, mọi câu khách gõ (hoặc nói) đều nhận đúng câu trả lời
   đã soạn sẵn, theo đúng thứ tự. Một banner màu vàng nhạt hiện ra nhắc rõ
   đang ở chế độ này, kèm số bước đã dùng (ví dụ "2/3").
5. Bấm **Dừng** bất cứ lúc nào để tắt sớm, hoặc cứ để kịch bản tự hết - lượt
   kế tiếp sau bước cuối sẽ tự động quay về xử lý thật.

Kịch bản chỉ gắn vào **một session** (một cuộc hội thoại) - không ảnh hưởng
tới bất kỳ khách hàng nào khác đang chat cùng lúc. Bấm **Cuộc trò chuyện
mới** hoặc đăng xuất sẽ tự dọn sạch trạng thái này.

### Điều khiển qua API (không cần UI)

```bash
# Danh sách kịch bản đang có (cần cookie nhân viên đã đăng nhập)
curl -b cookies.txt http://127.0.0.1:8000/api/demo-script

# Gắn kịch bản "balance_an" vào một session
curl -b cookies.txt -X POST http://127.0.0.1:8000/api/demo-script/start \
  -H "Content-Type: application/json" \
  -d '{"session_id": "CHT-...", "name": "balance_an"}'

# Gỡ kịch bản
curl -b cookies.txt -X POST http://127.0.0.1:8000/api/demo-script/stop \
  -H "Content-Type: application/json" -d '{"session_id": "CHT-..."}'
```

Bỏ trống `session_id` (hoặc gửi một id chưa tồn tại) sẽ tự tạo một session
mới - phản hồi trả về `session_id` thật đã được gắn kịch bản, dùng giá trị
đó cho các lượt chat tiếp theo.

---

## 3. Cách viết một kịch bản mới

Mở `data/demo_scripts.json`. Mỗi kịch bản là một khoá (tên định danh, dùng
trong API và trong danh sách thả xuống) trỏ tới một object:

```json
{
  "ten_kich_ban": {
    "label": "Tên hiển thị cho người chọn kịch bản, ví dụ: Kiểm tra số dư - An",
    "steps": [
      { "reply": "Câu trả lời cho lượt đầu tiên." },
      { "reply": "Câu trả lời cho lượt thứ hai.", "route": "deterministic", "intent": "flow:verify" }
    ]
  }
}
```

Mỗi bước trong `steps`:

| Trường | Bắt buộc | Ý nghĩa |
|---|---|---|
| `reply` | Có | Đúng nguyên văn câu trả lời sẽ hiển thị và được đọc (TTS). Hỗ trợ Markdown giống các câu trả lời khác (`**đậm**`, gạch đầu dòng...). |
| `route` | Không | Chỉ để **hiển thị** trong routing inspector (ví dụ "deterministic", "rag", "guardrail") - không ảnh hưởng đến việc câu trả lời có chạy qua các lớp đó thật hay không, vì kịch bản luôn bỏ qua toàn bộ router. Mặc định `"scripted"` nếu bỏ trống. |
| `intent` | Không | Tương tự `route`, chỉ để hiển thị. Mặc định `"scripted"`. |
| `sources` | Không | Danh sách nguồn để hiển thị trong khung "Nguồn câu trả lời", nếu muốn mô phỏng một câu trả lời RAG có trích dẫn. Định dạng giống `sources` trong `/api/chat` (`citation`, `title`, `heading`, `source`, `score`...) - xem `app/router.py::_answer_from_kb` để đối chiếu các khoá đang dùng. |

Vài nguyên tắc khi soạn:

- **Viết đúng những gì hệ thống thật đã từng trả lời** khi có thể - cách an
  toàn nhất để soạn một bước là chạy đúng luồng đó thật (qua khung chat hoặc
  `run_scenarios.py`), copy nguyên văn câu trả lời thật ra, rồi mới chỉnh
  sửa nếu cần. Kịch bản giả mà nghe "giả" hơn hệ thống thật là phản tác
  dụng.
- **Không có dữ liệu động** (tên khách, số dư, mã tham chiếu...) - cơ chế
  này chỉ phát lại đúng chuỗi văn bản đã ghi cứng. Nếu kịch bản cần nhắc tên
  khách hàng, viết cứng luôn tên đó vào `reply` (xem ví dụ `balance_an`
  trong file - viết cứng "An" ở bước cuối).
- **Số bước không cần khớp số lượt khách sẽ gõ thật** - vì kịch bản không
  đọc nội dung khách gõ, khách gõ bao nhiêu lượt thì dùng hết bấy nhiêu bước
  cho tới khi hết, không cần khớp chính xác một câu hỏi với một câu trả lời.
  Soạn đúng số bước bằng đúng số lượt bạn dự định demo.
- **Một kịch bản = một mạch hội thoại trọn vẹn.** Đừng dồn nhiều kịch bản
  không liên quan vào cùng một mục `steps` - tách thành các kịch bản riêng,
  đặt tên rõ ràng (xem ba ví dụ có sẵn: `balance_an`, `freeze_unfreeze_hieu`,
  `restricted_topic`) để chọn đúng cái cần giữa nhiều kịch bản.

### Kiểm tra trước khi demo

File JSON viết tay dễ sai cú pháp (thiếu dấu phẩy, thiếu ngoặc). Chạy:

```bash
python -m app.demo_script
```

Lệnh này đọc và kiểm tra `data/demo_scripts.json`, in ra danh sách kịch bản
hợp lệ kèm số bước, hoặc liệt kê chính xác lỗi (dòng/cột JSON sai, kịch bản
thiếu `steps`, bước thiếu `reply`...) nếu có vấn đề. **Luôn chạy lệnh này
sau khi sửa file, trước khi vào phòng demo** - một file sai cú pháp không
làm sập server (xem mục 4), nhưng có nghĩa là *không kịch bản nào* dùng
được cho tới khi sửa xong, kể cả những kịch bản không liên quan tới chỗ sai.

---

## 4. Giới hạn cần biết

- **Không kiểm tra nội dung khách gõ**, như đã nói ở mục 1 - đây là lựa chọn
  có chủ đích cho độ bền của demo, nhưng cũng có nghĩa nếu bấm nhầm kịch bản
  (hoặc quên bấm Dừng từ buổi demo trước), khách hàng thật tiếp theo trên
  session đó sẽ nhận câu trả lời của một kịch bản không liên quan. Luôn kiểm
  tra banner "Kịch bản demo" trước khi bắt đầu một cuộc hội thoại thật.
- **Không có dữ liệu động** - mọi `reply` là văn bản tĩnh, viết cứng tại
  thời điểm soạn. Nếu dữ liệu khách hàng (`make_customers.py`) được sinh lại
  và số thẻ/số dư trong kịch bản không còn khớp `data/accounts.json`, kịch
  bản vẫn chạy bình thường (nó không đọc dữ liệu thật) - chỉ là nội dung có
  thể lệch với những gì nhân viên thấy trên các tab khác (Tổng quan, Hàng
  chờ...). Nếu buổi demo có phần đối chiếu số liệu thật, kiểm tra lại trước.
- **File hỏng không làm sập chat** (xem `app/demo_script.py::_load`) - một
  lỗi cú pháp JSON khiến toàn bộ tính năng kịch bản tạm thời "biến mất" (danh
  sách rỗng, `start` báo "unknown script") thay vì làm hỏng những cuộc hội
  thoại khác đang chạy bình thường trên cùng server.
- Kịch bản không được ghi vào `data/db/*.csv` hay bất kỳ đâu - nó chỉ là câu
  trả lời hiển thị/đọc, không thực sự khoá thẻ, không thực sự đổi số dư. Nếu
  buổi demo sau đó chuyển sang thao tác thật trên cùng khách hàng (ví dụ
  Tổng quan → cards.csv), đừng mong trạng thái phản ánh những gì kịch bản
  vừa "nói".
