# Kịch bản Test — Trợ lý ảo ABC Bank

Playbook kiểm thử thủ công + tự động. Bám sát các tính năng hiện có: phân quyền
admin/agent, quản lý workforce (agent + skillset), hội thoại khách (xác thực 2
yếu tố + nghiệp vụ), và TTS.

---

## 0. Chuẩn bị

```bash
python3 server.py          # chạy tại http://127.0.0.1:8000
```

- Mở trình duyệt: http://127.0.0.1:8000
- TTS mặc định: **gTTS** (miễn phí, tiếng Việt, không cần key/ngrok).
- LLM: nếu không có API key hợp lệ, bot chạy chế độ **extractive** (trích dẫn KB)
  thay vì generative — vẫn trả lời được, chỉ kém tự nhiên hơn.

### Tài khoản đăng nhập

| Vai trò       | Username | Mật khẩu    | Quyền                                               |
| ------------- | -------- | ----------- | --------------------------------------------------- |
| Quản trị viên | `admin`  | `admin1234` | Toàn quyền + cấu hình (Quản lý Agent, provider, KB) |
| Nhân viên     | `agent`  | `demo1234`  | Vận hành (hàng chờ, trả lời khách, dashboard)       |

### Dữ liệu khách hàng test (SĐT / CCCD)

| Khách          | SĐT        | CCCD         | Đặc điểm thẻ / ưu đãi                     |
| -------------- | ---------- | ------------ | ----------------------------------------- |
| Nguyễn Văn An  | 0842199388 | 001094113804 | ghi nợ active; **có ưu đãi** (XS-CLASSIC) |
| Trần Thị Bích  | 0882935134 | 079190133919 | ghi nợ **dormant** (ngủ đông)             |
| Lê Minh Châu   | 0944984320 | 031183007998 | ghi nợ **inactive** (chưa kích hoạt)      |
| Phạm Quốc Dũng | 0984911069 | 048075754149 | ghi nợ + tín dụng, đều active             |
| Hoàng Thị Hà   | 0394818868 | 036193127325 | ghi nợ **frozen** + tín dụng active       |
| Vũ Đức Hiếu    | 0328985767 | 038076763936 | ghi nợ active; **không có ưu đãi**        |
| Đỗ Thị Mai     | 0395821542 | 056175473446 | ghi nợ + tín dụng, đều active             |
| Bùi Văn Long   | 0380591171 | 046094715684 | ghi nợ **blocked** + ghi nợ inactive      |

> Xác thực gồm **2 bước**: nhập **SĐT (10 số)** trước, rồi **CCCD (12 số)**.

---

## A. Phân quyền

| #   | Bước                                     | Kỳ vọng                                                        |
| --- | ---------------------------------------- | -------------------------------------------------------------- |
| A1  | Chưa đăng nhập, mở tab "Bảng điều khiển" | Không thấy tab **Quản lý Agent**; gọi thẳng API cấu hình → 401 |
| A2  | Đăng nhập `agent` / `demo1234`           | Thấy hàng chờ, dashboard; **KHÔNG** thấy tab Quản lý Agent     |
| A3  | (agent) gọi `POST /api/agents/ops`       | Trả về **403** (không đủ quyền)                                |
| A4  | Đăng nhập `admin` / `admin1234`          | Thấy tab **Quản lý Agent**; pill hiện "Quản trị viên · admin"  |
| A5  | (admin) mở tab Quản lý Agent             | Hiện danh sách 3 agent                                         |

---

## B. Quản lý Agent (workforce + skillset) — đăng nhập `admin`

### B1. Xem danh sách

- **Bước:** mở tab **Quản lý Agent**.
- **Kỳ vọng:** danh sách 3 agent bên trái:
  - **Operation Support** — 11/11 kỹ năng bật
  - **Product Q&A** — 3/3 kỹ năng bật
  - **Cross-sell** — 2/2 kỹ năng bật

### B2. Chọn agent → xem chi tiết

- **Bước:** bấm vào **Operation Support**.
- **Kỳ vọng:** bên phải hiện công tắc "Agent hoạt động" + danh sách nghiệp vụ có
  ô tick (Tra cứu số dư, Tổng quan tài khoản, ...). Mục đang chọn được tô sáng.

### B3. Tắt một kỹ năng → khách bị chặn nghiệp vụ đó

- **Bước:** trong Operation Support, **bỏ tick "Tra cứu số dư"** → **Lưu**.
- **Kỳ vọng:** báo "Đã lưu ✓"; danh sách cập nhật "10/11 kỹ năng bật".
- **Kiểm chứng:** sang khung chat khách, gõ _"kiểm tra số dư"_.
  → Bot trả lời **"Rất tiếc, nghiệp vụ này hiện chưa được hỗ trợ"** + đề nghị
  chuyển nhân viên (route = escalation).

### B4. Bật lại kỹ năng

- **Bước:** tick lại "Tra cứu số dư" → **Lưu**.
- **Kiểm chứng:** khách gõ _"kiểm tra số dư"_ → bot hỏi xác thực bình thường.

### B5. Chuyển agent tự động giữa phiên

- **Bước (cùng 1 phiên chat khách):**
  1. Gõ _"so sánh thẻ Visa và Mastercard"_ (nghiệp vụ của Product Q&A).
  2. Gõ tiếp _"cho tôi xem số dư"_ (nghiệp vụ của Operation Support).
- **Kỳ vọng:** bot xử lý được cả hai; nội bộ có bước **chuyển sang Operation
  Support** (xem ở rail "Đường ra quyết định"/trace nếu bật).

### B6. Tắt cả một agent

- **Bước:** chọn Operation Support, **bỏ tick "Agent hoạt động"** → **Lưu**.
- **Kỳ vọng:** danh sách hiện "đã tắt"; mọi nghiệp vụ của agent này bị chặn
  (khách hỏi số dư → escalation). Nhớ **bật lại** sau khi test.

---

## C. Hội thoại khách hàng (khung chat công khai)

### C1. Xác thực 2 yếu tố thành công

- **Bước:** _"kiểm tra số dư"_ → `0842199388` → `001094113804`.
- **Kỳ vọng:** xác minh thành công, chào "Nguyễn Văn An", hiển thị số dư.

### C2. Từ chối khi dán số thẻ

- **Bước:** _"kiểm tra số dư"_ → nhập `9411 3147 1234 5678` (16 số).
- **Kỳ vọng:** **không** xác minh; bot yêu cầu nhập đúng SĐT 10 số.

### C3. Sai 3 lần → khoá bảo mật

- **Bước:** _"kiểm tra số dư"_ → `0000000000` → `1111111111` → `2222222222`.
- **Kỳ vọng:** lần 1 "1/3", lần 2 "2/3", lần 3 → **escalation** (chuyển nhân viên).

### C4. Tạm khoá → mở khoá thẻ (Vũ Đức Hiếu)

- **Bước:** _"tạm khoá thẻ giúp tôi"_ → `0328985767` → `038076763936` → _"có"_.
- **Kỳ vọng:** trả mã **FRZ-...**, đúng thẻ ghi nợ. Sau đó _"mở khoá thẻ"_ → _"có"_
  → mã **UNF-...**, thẻ hoạt động lại.

### C5. Báo mất thẻ → phát hành thẻ thay thế (Đỗ Thị Mai — 2 thẻ)

- **Bước:** _"tôi làm mất thẻ"_ → `0395821542` → `056175473446` → _"ghi nợ"_ → _"có"_.
- **Kỳ vọng:** trả mã **BLK-...**, thông báo cấp thẻ thay thế.

### C6. Thẻ ngủ đông (Trần Thị Bích)

- **Bước:** _"thẻ lâu rồi tôi không dùng, còn dùng được không?"_ → `0882935134` → `079190133919`.
- **Kỳ vọng:** nhận diện thẻ **ngủ đông (dormant)** và hướng dẫn.

### C7. Kích hoạt thẻ (Lê Minh Châu — thẻ inactive)

- **Bước:** _"làm sao để kích hoạt thẻ mới?"_ → `0944984320` → `031183007998`.
- **Kỳ vọng:** hướng dẫn qua app/IVR; **không** hỏi OTP/CVV.

### C8. Bán chéo — chỉ ưu đãi có trong file chiến dịch

- **Có ưu đãi (Nguyễn Văn An):** _"có ưu đãi nào cho tôi không?"_ → `0842199388`
  → `001094113804` → hiện ưu đãi XS-CLASSIC.
- **Không ưu đãi (Vũ Đức Hiếu):** _"tôi có đủ điều kiện nâng hạng thẻ không?"_ →
  `0328985767` → `038076763936` → **không bịa** ưu đãi.

### C9. Ngoài phạm vi → không bịa

- **Bước:** _"ngân hàng có bảo hiểm cây trồng cho vườn nho ở Bồ Đào Nha không?"_
- **Kỳ vọng:** không bịa; đề nghị chuyển nhân viên hoặc nói không có thông tin.

### C10. Hỏi kiến thức (RAG) — không cần xác thực

- **Bước:** _"phí chuyển tiền quốc tế là bao nhiêu?"_
- **Kỳ vọng:** trả lời từ kho tri thức, **không** hỏi xác minh danh tính.

---

## D. Giọng nói (TTS) — đăng nhập `admin`, tab "Giọng nói"

| #   | Bước                                 | Kỳ vọng                                                                     |
| --- | ------------------------------------ | --------------------------------------------------------------------------- |
| D1  | Bấm nút loa 🔊 trên một câu trả lời  | Nghe được audio                                                             |
| D2  | Chọn **Voice 1 (VieNeu)**            | Service VieNeu local chạy → giọng Việt bản địa; service tắt → tự về Voice 2 |
| D3  | Chọn **Voice 2 (gTTS)**              | Luôn chạy (không cần key), giọng Việt cơ bản                                |

> Voice 1 = VieNeu (local, offline, giọng tự nhiên) là mặc định; nếu service chưa
> lên thì tự rớt về Voice 2 = gTTS để luôn có tiếng.

---

## E. Test tự động

```bash
# (server đang chạy ở cổng 8000)
python3 test_workforce.py   # tính năng admin + agent + skillset  → kỳ vọng 20/20
python3 test_e2e.py         # toàn bộ luồng nghiệp vụ             → kỳ vọng 110/110
```

- `test_workforce.py`: phân quyền, workforce mặc định, chuyển agent, tắt skill/agent, bền vững cấu hình.
- `test_e2e.py`: xác thực, số dư, thẻ, RAG, chiến dịch, endpoint staff — dùng đúng
  cặp SĐT/CCCD ở mục dữ liệu trên.

> Nếu chạy lại nhiều lần, đảm bảo tắt sạch server cũ: `pkill -f "server.py"`.
