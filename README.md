# 📄 Google Docs Page Screenshot Tool

Ứng dụng web cho phép bạn nhập link Google Docs và tự động chụp ảnh từng trang
của tài liệu thành các file PNG riêng biệt.

## 🎯 Tính năng

- Nhập URL Google Docs qua giao diện web
- Tự động mở tài liệu bằng trình duyệt Playwright
- Nhận diện từng trang trong tài liệu
- Chụp ảnh mỗi trang thành file PNG riêng (page_001.png, page_002.png, ...)
- Lưu vào thư mục output có tên chứa doc_id và timestamp
- Giao diện hiển thị kết quả chi tiết

## ⚠️ Giới hạn

- **Chỉ hoạt động với tài liệu công khai** (Anyone with the link can view) hoặc
  tài liệu mà phiên trình duyệt hiện tại có quyền truy cập.
- **Không bypass** bất kỳ quyền truy cập, xác thực, hay CAPTCHA nào.
- Tài liệu private mà không có quyền sẽ báo lỗi rõ ràng.
- Google có thể thay đổi cấu trúc HTML của Docs, khi đó cần cập nhật selector.

---

## 🛠️ Cài đặt

### Yêu cầu

- Python 3.10 trở lên
- Kết nối internet

### Bước 1: Tạo Virtual Environment

**CMD (Windows):**
```cmd
python -m venv venv
venv\Scripts\activate