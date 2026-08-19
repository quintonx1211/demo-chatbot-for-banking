# Customer segments

Khách hàng xác nhận đúng 4 segment này (không phải tự nghiên cứu như lần
trước). **Ngưỡng số liệu bên dưới vẫn là ước lượng theo thông lệ ngành —
khách chưa cho con số cụ thể phân biệt 4 hạng — cần khách xác nhận trước khi
dùng ngoài mục đích demo.** Mỗi segment gắn 1-1 với đúng 1 thẻ trong
`data/products.json` (`app/rules_engine.py::card_for_segment`).

## MASS → Thẻ Classic

Không yêu cầu số dư/thu nhập tối thiểu đặc biệt. Phân khúc phổ thông, đối
tượng chính của Scenario 1 (Automated Cross-Selling) — CRM chọn danh sách
khách Mass để bot chủ động liên hệ.

## MASS_AFFLUENT → Thẻ Platinum

Thu nhập/số dư cao hơn Mass nhưng chưa tới ngưỡng Affluent — ước lượng thu
nhập 15-30 triệu VND/tháng.

## AFFLUENT → Thẻ Signature

Ước lượng thu nhập 30-100 triệu VND/tháng hoặc tài sản quản lý tương ứng.

## PRIVATE → Thẻ Infinite

Ước lượng thu nhập trên 100 triệu VND/tháng hoặc tài sản quản lý lớn. Khách
Private thường có chuyên viên quan hệ khách hàng riêng.
