# Tích hợp AI Callbot (outbound) với ElevenLabs Conversational AI

Tài liệu hướng dẫn biến demo này thành **outbound callbot**: admin nhập số điện
thoại khách → bot gọi ra và tư vấn (sales/support) bằng giọng nói real-time.

Bộ não nghiệp vụ (`router.py` / `flows.py` / skillset trong `agents.py`) **giữ
nguyên** và được voice agent gọi tới qua **tools (webhook)**. ElevenLabs lo phần
hội thoại real-time (nghe, ngắt lời, đọc); backend này lo **dữ liệu + hành động
ngân hàng + nhật ký cuộc gọi**.

> ⚠️ **Ngân hàng, dữ liệu ra nước ngoài:** ElevenLabs xử lý ở Mỹ. Trước khi dùng
> thật, pháp chế/bảo mật phải duyệt việc gửi giọng + dữ liệu khách ra ngoài, và
> cân nhắc SIP trunk trong nước + phương án lưu trữ tại VN.

---

## 1. Kiến trúc

```
Admin UI (tab "Gọi khách")
   │  POST /api/voice/call {phone, agent_id}
   ▼
Backend (server.py) ── tạo bản ghi call ──► [Outbound API nhà cung cấp]
   │                                              │ đặt cuộc gọi qua Twilio/SIP
   │                                              ▼
   │                                    ElevenLabs Conversational Agent
   │                                       (STT + LLM + TTS real-time)
   │      webhook tools (giữa cuộc gọi)         │
   ◄────────────────────────────────────────────┘
   │  POST /api/voice/tools/verify_customer
   │  POST /api/voice/tools/account_snapshot
   │  POST /api/voice/tools/offers
   ▼
db.py / campaigns.py  (số dư, thẻ, ưu đãi...)

Kết thúc cuộc gọi:
ElevenLabs post-call webhook ──► POST /api/voice/calls/<id> {status, transcript, summary}
```

Điểm chốt: **agent + skillset** đã xây map thành **agent trên ElevenLabs** + bộ
**tools** nó được phép gọi (Operation Support / Product Q&A / Cross-sell = tập
tool khác nhau).

---

## 2. Những gì đã có sẵn trong repo (phần "của mình")

| Thành phần | Vị trí | Vai trò |
|---|---|---|
| Tool `verify_customer` | `app/voice.py::tool_verify_customer` | Xác thực SĐT + CCCD, trả `customer_id` |
| Tool `account_snapshot` | `app/voice.py::tool_account_snapshot` | Số dư + trạng thái thẻ |
| Tool `offers` | `app/voice.py::tool_offers` | Ưu đãi cross-sell từ CRM (không bịa) |
| Webhook tool | `POST /api/voice/tools/<name>` | ElevenLabs gọi giữa cuộc trò chuyện |
| Đặt cuộc gọi | `POST /api/voice/call` (admin) | Tạo bản ghi; **chỗ cắm Outbound API** |
| Cập nhật cuộc gọi | `POST /api/voice/calls/<id>` | Nhà cung cấp post trạng thái/transcript về |
| Nhật ký | `GET /api/voice/calls` (admin) + `data/call_log.json` | Danh sách cuộc gọi |
| UI | tab **Gọi khách** (chỉ admin) | Nhập SĐT + chọn agent + xem nhật ký |

Bảo mật webhook: đặt biến môi trường **`VOICE_TOOLS_SECRET`**; ElevenLabs gửi
`Authorization: Bearer <secret>`. Nếu chưa đặt, server cho qua (chỉ để dev PoC —
**bắt buộc đặt khi production**).

---

## 3. Các bước tích hợp ElevenLabs

### 3.1. Tạo Agent
- ElevenLabs Dashboard → **Conversational AI → Agents → Create**.
- Ngôn ngữ: Tiếng Việt; chọn voice tiếng Việt; **test độ trễ** trước.
- System prompt: nêu rõ là *trợ lý ảo ngân hàng ABC*, tự giới thiệu, chỉ tư vấn
  đúng skillset, không bịa số liệu/ưu đãi (bắt buộc gọi tool để lấy dữ liệu).
- First message (outbound): câu chào + lý do gọi + xin phép tiếp tục.

### 3.2. Khai báo Tools (Server tools / webhooks)
Trỏ về backend này. Ví dụ schema cho `verify_customer`:

```jsonc
{
  "name": "verify_customer",
  "description": "Xác thực khách bằng SĐT (10 số) và CCCD (12 số). Gọi trước mọi thao tác cần danh tính.",
  "url": "https://<BACKEND>/api/voice/tools/verify_customer",
  "method": "POST",
  "headers": { "Authorization": "Bearer <VOICE_TOOLS_SECRET>" },
  "parameters": {
    "type": "object",
    "properties": {
      "phone": { "type": "string", "description": "SĐT đã đăng ký, 10 số" },
      "national_id": { "type": "string", "description": "CCCD/CMND, 12 số" }
    },
    "required": ["phone", "national_id"]
  }
}
```

Tương tự cho `account_snapshot` (param `customer_id`) và `offers` (param
`customer_id`). Skillset quyết định agent nào được gán tool nào:
- **Operation Support** → `verify_customer`, `account_snapshot`
- **Cross-sell** → `verify_customer`, `offers`
- **Product Q&A** → (chủ yếu dùng knowledge base, ít tool)

### 3.3. Gọi ra số VN (outbound)
ElevenLabs không có đầu số VN — cần một trong hai:
- **Twilio**: mua/nối số, bật outbound. Lưu ý **outbound tới số VN bị hạn chế** —
  kiểm tra khả dụng trước.
- **SIP trunk**: nối trunk của nhà mạng VN (Stringee/Viettel/VNPT) vào ElevenLabs
  qua SIP — đây là hướng khả thi hơn cho gọi ra số VN.

Kích hoạt cuộc gọi từ backend (thay TODO trong `server.py::/api/voice/call`):

```python
# Pseudocode - gọi Outbound Call API của ElevenLabs sau khi tạo record
# POST https://api.elevenlabs.io/v1/convai/... (outbound)
#   headers: {"xi-api-key": ELEVENLABS_API_KEY}
#   body: {"agent_id": <eleven_agent_id theo skillset>,
#          "to_number": record["phone"],
#          "dynamic_variables": {"call_id": record["id"]}}
# -> lưu id cuộc gọi của provider vào record để đối soát webhook.
```

Truyền `call_id` của mình vào `dynamic_variables` để post-call webhook biết cập
nhật đúng bản ghi.

### 3.4. Post-call webhook
Cấu hình ElevenLabs gửi kết quả về `POST /api/voice/calls/<call_id>` với
`{status, transcript, summary}` (kèm `Authorization: Bearer <VOICE_TOOLS_SECRET>`).
Server cập nhật `data/call_log.json` và tab Gọi khách hiển thị.

---

## 4. Biến môi trường (.env)

```bash
ELEVENLABS_API_KEY=sk_...            # tài khoản trả phí (Conversational AI)
VOICE_TOOLS_SECRET=<chuỗi-ngẫu-nhiên-dài>   # bảo vệ webhook tools/calls
# (Nếu dùng Twilio) TWILIO_ACCOUNT_SID=... TWILIO_AUTH_TOKEN=... TWILIO_NUMBER=...
```

---

## 5. Bắt buộc trước khi chạy thật (ngân hàng)

- [ ] **Đồng ý (consent)** của khách nhận cuộc gọi tự động + danh sách **Do-Not-Call**.
- [ ] **Giờ gọi** hợp lệ; giới hạn số lần gọi lại.
- [ ] Bot **tự giới thiệu là trợ lý ảo**, không giả danh người.
- [ ] **Thông báo ghi âm**; lưu recording/transcript đúng quy định.
- [ ] Không đọc dữ liệu nhạy cảm khi **chưa** `verify_customer` thành công.
- [ ] Đặt `VOICE_TOOLS_SECRET`; backend chạy HTTPS; hạn chế IP nếu được.
- [ ] Pháp chế duyệt việc **dữ liệu ra nước ngoài** (hoặc chuyển sang SIP + hạ tầng VN).

---

## 6. Kiểm thử nhanh (PoC, chưa cần ElevenLabs)

```bash
python3 server.py
# đăng nhập admin, mở tab "Gọi khách", nhập 0842199388 + chọn agent -> "Gọi"
# (PoC: tạo bản ghi trong nhật ký, chưa gọi thật)

# Thử tool như ElevenLabs sẽ gọi:
curl -X POST http://127.0.0.1:8000/api/voice/tools/verify_customer \
  -H 'Content-Type: application/json' \
  -d '{"phone":"0842199388","national_id":"001094113804"}'
```

Khi đã sẵn sàng nối ElevenLabs: điền TODO ở `server.py::/api/voice/call`, đặt env,
khai tools + agent như mục 3.
