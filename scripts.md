# Kịch bản Demo Chatbot AI for Banking - Credit Card Advisory

## Quy tắc chung

- Khi khách hàng mở giao diện chat, bot chủ động gửi lời chào đầu tiên.
- Khi cần tra cứu tài khoản hoặc thực hiện thao tác trên thẻ, bot xác minh theo đúng thứ tự:
  1. Hỏi số điện thoại đã đăng ký, đủ 10 số.
  2. Chỉ khi số điện thoại khớp mới hỏi CCCD, đủ 12 số.
  3. Chỉ khi CCCD khớp mới tiếp tục tra cứu hoặc thực hiện yêu cầu.
- SĐT và CCCD được gửi ở **hai tin nhắn riêng biệt**. Không yêu cầu khách hàng nhập OTP trong chat; OTP được thực hiện trên ứng dụng hoặc kênh bảo mật.
- Các quyền lợi, mức phí và trạng thái tài khoản trong tài liệu là dữ liệu mô phỏng đã được Product/Compliance phê duyệt.

### Dữ liệu xác minh dùng trong demo

Nguyen Van An 0842199388 | 001094113804  
Tran Thi Bich 0882935134 | 079190133919
Le Minh Chau 0944984320 | 031183007998  
Pham Quoc Dung 0984911069 | 048075754149  
Hoang Thi Ha 0394818868 | 036193127325  
Vu Duc Hieu 0328985767 | 038076763936

---

## Scenario 1: Customer Service (Ưu tiên 2)

_Khách hàng chủ động hỏi, bot tư vấn thẻ phù hợp dựa trên nhu cầu chi tiêu._

### Demo 1.1 - Nhu cầu Golf và Travel

**Bot:** **Trợ lý ảo ABC Bank** hân hạnh được phục vụ quý khách. Tôi có thể hỗ trợ quý khách kiểm tra số dư và giao dịch, khóa hoặc mở khóa thẻ, tra cứu hồ sơ vay, và giải đáp về sản phẩm, biểu phí của ngân hàng.

**Khách hàng:** Tôi thường chơi golf và đi công tác nước ngoài. Có thẻ nào phù hợp không?

**Bot:** Quý khách cho tôi biết mức chi tiêu dự kiến mỗi tháng và phần lớn giao dịch phát sinh ở Việt Nam hay nước ngoài. Đây là thông tin tư vấn chung nên chưa cần xác minh danh tính.

**Khách hàng:** Tôi chi khoảng 300-400 triệu đồng mỗi tháng, phần lớn là giao dịch quốc tế.

**Bot:** Với nhu cầu du lịch, golf và mức chi tiêu này, **Infinite** là sản phẩm phù hợp để tham khảo. Thẻ có quyền lợi về dặm bay, Golf và thưởng theo cột mốc chi tiêu. Mức hưởng thực tế áp dụng theo giao dịch hợp lệ và thể lệ hiện hành.

**Khách hàng:** Phí thường niên và cách đăng ký thế nào?

**Bot:** Phí thường niên là 5.999.000 đồng/năm. Theo điều kiện hiện tại, phí năm tiếp theo có thể được miễn khi tổng chi tiêu hợp lệ đạt từ 400 triệu đồng. Tôi sẽ gửi quý khách biểu phí, điều kiện và đường dẫn đăng ký. Khi quý khách đồng ý xử lý thông tin cá nhân, tôi sẽ chuyển sang biểu mẫu bảo mật.

### Demo 1.2 - Nhu cầu mua sắm online và ăn uống

**Bot:** **Trợ lý ảo ABC Bank** hân hạnh được phục vụ quý khách. Tôi có thể hỗ trợ quý khách kiểm tra số dư và giao dịch, khóa hoặc mở khóa thẻ, tra cứu hồ sơ vay, và giải đáp về sản phẩm, biểu phí của ngân hàng. Quý khách cần hỗ trợ gì ạ?

**Khách hàng:** Mình muốn tìm thẻ có ưu đãi khi mua sắm online và ăn uống, chi tiêu khoảng 5-6 triệu đồng/tháng.

**Bot:** Với nhu cầu này, **Platinum** là sản phẩm phù hợp để tham khảo. Thẻ có ưu đãi cho một số nhóm chi tiêu và mức hoàn tiền theo thể lệ chương trình. Mức thực nhận phụ thuộc giao dịch đủ điều kiện và hạn mức áp dụng.

**Khách hàng:** Phí thường niên có cao không?

**Bot:** Phí thường niên của Platinum là 399.000 đồng/năm. Theo điều kiện hiện tại, phí năm tiếp theo có thể được miễn nếu tổng chi tiêu hợp lệ đạt từ 60 triệu đồng. Tôi sẽ gửi quý khách thông tin chi tiết để xem trước, không cần xác minh nếu quý khách chỉ muốn tham khảo sản phẩm.

**Khách hàng:** Mình muốn đăng ký.

**Bot:** Tôi sẽ chuyển quý khách sang biểu mẫu bảo mật. Hồ sơ chỉ được gửi sau khi quý khách xem lại thông tin, đồng ý xử lý dữ liệu cá nhân và xác nhận.

---

## Scenario 2: Automated Cross-Selling (Ưu tiên 1)

_Bot chủ động tiếp cận khách hàng trong nhóm mục tiêu, sau khi được phép sử dụng thông tin quan hệ sản phẩm._

### Demo 2.1 - Khách hàng chưa có thẻ

**Bot:** **Trợ lý ảo ABC Bank** hân hạnh được phục vụ quý khách. Tôi có thể hỗ trợ quý khách kiểm tra số dư và giao dịch, khóa hoặc mở khóa thẻ, tra cứu hồ sơ vay, và giải đáp về sản phẩm, biểu phí của ngân hàng. Quý khách cần hỗ trợ gì ạ?

**Khách hàng:** Tôi muốn kiểm tra thông tin quan hệ sản phẩm

**Bot:** Để kiểm tra thông tin quan hệ sản phẩm. Quý khách vui lòng nhập SĐT đã đăng ký (10 số) để xác minh danh tính

**Khách hàng:** 0842199388

**Bot:** Số điện thoại đã khớp. Quý khách vui lòng nhập CCCD gồm đủ 12 số để tiếp tục xác minh.

**Khách hàng:** 001094113804

**Bot:** Chào An. Theo thông tin được phép tra cứu, quý khách hiện chưa có thẻ tín dụng. Quý khách thường chi tiêu nhiều hơn cho mua sắm online, ăn uống hay đi lại?

**Khách hàng:** Mình chủ yếu mua sắm online và đi xe công nghệ, chi khoảng 3-4 triệu đồng/tháng.

**Bot:** Với nhu cầu và mức chi tiêu này, **Classic** là sản phẩm phù hợp để quý khách tham khảo. Phí thường niên là 199.000 đồng/năm; giao dịch đủ điều kiện có thể được hưởng ưu đãi hoàn tiền theo thể lệ. Tôi sẽ gửi thông tin sản phẩm và biểu mẫu bảo mật để quý khách đăng ký khi sẵn sàng.

### Demo 2.2 - Đề xuất sản phẩm cao hơn

**Bot:** **Trợ lý ảo ABC Bank** hân hạnh được phục vụ quý khách. Tôi có thể hỗ trợ quý khách kiểm tra số dư và giao dịch, khóa hoặc mở khóa thẻ, tra cứu hồ sơ vay, và giải đáp về sản phẩm, biểu phí của ngân hàng. Quý khách cần hỗ trợ gì ạ?

**Khách hàng:** Tôi muốn mở thẻ

**Bot:** Tôi sẵn sàng hỗ trợ quý khách mở thẻ. Quý khách thường chi tiêu nhiều hơn cho mua sắm online, ăn uống hay đi lại?

**Khách hàng:** Tôi thường ăn nhà hàng và mua sắm online, chi khoảng 8-10 triệu đồng/tháng.

**Bot:** Với nhu cầu ăn uống và mua sắm online, **Classic** và **Platinum** đều là những lựa chọn phù hợp. Classic có mức phí thường niên thấp hơn, phù hợp nếu quý khách muốn tối ưu chi phí. Platinum có mức hoàn tiền tối đa cao hơn ở một số nhóm chi tiêu, phù hợp nếu quý khách sử dụng thẻ thường xuyên hơn. Quý khách muốn tôi so sánh chi tiết hai thẻ để lựa chọn không?

**Khách hàng:** Phí của Platinum cao hơn bao nhiêu?

**Bot:** Phí thường niên Platinum là 399.000 đồng/năm, cao hơn Classic khoảng 200.000 đồng/năm.

**Khách hàng:** Mình muốn đăng ký Platinum.

**Bot:** Tôi đã ghi nhận lựa chọn Platinum. Tôi sẽ chuyển quý khách sang biểu mẫu bảo mật. Hồ sơ chỉ được gửi sau khi quý khách đồng ý xử lý thông tin cá nhân và xác nhận các điều khoản.

---

## Scenario 3: Card Operations Management (Ưu tiên 3)

_Khách hàng đã có thẻ, dùng chatbot để quản lý thẻ và gửi yêu cầu hỗ trợ._

### Demo 3.1 - Đóng thẻ

**Bot:** **Trợ lý ảo ABC Bank** hân hạnh được phục vụ quý khách. Tôi có thể hỗ trợ quý khách kiểm tra số dư và giao dịch, khóa hoặc mở khóa thẻ, tra cứu hồ sơ vay, và giải đáp về sản phẩm, biểu phí của ngân hàng. Quý khách cần hỗ trợ gì ạ?

**Khách hàng:** Tôi muốn đóng thẻ

**Bot:** Tôi đã nhận được yêu cầu của quý khách. Để đóng thẻ, quý khách vui lòng nhập SĐT đã đăng ký (10 số) để xác minh danh tính
Tôi rất sẵn lòng
**Khách hàng:** 0944984320

**Bot:** Số điện thoại đã khớp. Quý khách vui lòng nhập CCCD gồm đủ 12 số để tiếp tục xác minh.

**Khách hàng:** 031183007998

**Bot:** Chào Châu. Trước khi tiếp nhận yêu cầu, tôi cần kiểm tra dư nợ, giao dịch đang chờ xử lý, trả góp, thẻ phụ và thanh toán định kỳ. Quý khách muốn đóng thẻ vì lý do nào?

**Khách hàng:** Tôi thấy phí thường niên cao.

**Bot:** Tôi đã ghi nhận yêu cầu đóng thẻ Signature. Hệ thống sẽ kiểm tra điều kiện, cung cấp mã yêu cầu và thời gian dự kiến. Thẻ chỉ được xác nhận đóng sau khi hệ thống hoàn tất xử lý và gửi thông báo chính thức.

### Demo 3.2 - Reward và tăng hạn mức

**Bot:** **Trợ lý ảo ABC Bank** hân hạnh được phục vụ quý khách. Tôi có thể hỗ trợ quý khách kiểm tra số dư và giao dịch, khóa hoặc mở khóa thẻ, tra cứu hồ sơ vay, và giải đáp về sản phẩm, biểu phí của ngân hàng. Quý khách cần hỗ trợ gì ạ?

**Khách hàng:** Tôi muốn tra cứu reward và tăng hạn mức thẻ.

**Bot:** Yêu cầu tra cứu reward và tăng hạn mức của quý khách cần xác minh danh tính. Quý khách vui lòng nhập số điện thoại đã đăng ký (10 số)

**Khách hàng:** 0328985767

**Bot:** Số điện thoại đã khớp. Quý khách vui lòng nhập CCCD gồm đủ 12 số để tiếp tục xác minh

**Khách hàng:** 038076763936

**Bot:** Chào Hiếu. Theo thông tin được phép tra cứu, quý khách chưa thiết lập nhóm ưu tiên cho chương trình Build Your Own Rewards. Dữ liệu giao dịch cho thấy nhóm Du lịch & Giải trí là lựa chọn quý khách có thể cân nhắc. Quý khách có muốn tạo yêu cầu thiết lập nhóm này không?

**Khách hàng:** Có. Đồng thời mình muốn tăng hạn mức lên 100 triệu đồng.

**Bot:** Tôi đã tiếp nhận yêu cầu thiết lập nhóm Du lịch & Giải trí và yêu cầu tăng hạn mức lên 100 triệu đồng. Mã yêu cầu giả lập là **LIM-20260825-001**. Hồ sơ sẽ được chuyển thẩm định và kết quả sẽ được thông báo qua kênh đã đăng ký. Đây là mã yêu cầu, chưa phải thông báo phê duyệt.

---
