# 📄 Document Page Screenshot Tool

Ứng dụng Flask + Playwright nhận link Google Docs, Scribd hoặc Studocu và tự động chụp
từng trang thành các file PNG riêng biệt.

## Tính năng

- Nhận diện URL Google Docs, Scribd và Studocu.
- Mỗi nguồn chạy một pipeline riêng trong `providers/` để selector và cách cuộn
  của một website không ảnh hưởng website khác.
- Google Docs được quét theo vị trí trang tuyệt đối, không dựa vào số node DOM
  tại một thời điểm; cách này hỗ trợ viewer virtualized chỉ giữ 3–5 trang trong DOM.
- Với Scribd, tự tìm nút fullscreen bằng `data-e2e="full-screen-icon"`.
- Nhận diện từng khung trang Scribd bằng `.outer_page[id^="outer_page_"]`.
- Cuộn tài liệu để kích hoạt lazy-load, chờ nội dung của từng trang rồi chụp
  đúng theo viền ngoài của trang.
- Lưu ảnh dạng `page_001.png`, `page_002.png`, ... vào thư mục riêng trong
  `outputs/`.
- Hiển thị tiến trình và lỗi theo thời gian thực trên giao diện web.
- Lưu `debug_dom.html` và `debug_full_page.png` khi không thể nhận diện/chụp.

## Giới hạn

- Chỉ chụp nội dung mà phiên trình duyệt hiện tại được phép hiển thị.
- Không vượt quyền truy cập, đăng nhập, CAPTCHA hoặc paywall.
- Selector có thể cần cập nhật nếu Google, Scribd hoặc Studocu thay đổi giao diện.
- Ứng dụng không vượt đăng nhập, CAPTCHA, paywall hoặc cơ chế chặn truy cập.

## Cài đặt

Yêu cầu Python 3.10 trở lên.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
playwright install chromium
```

## Chạy ứng dụng

```powershell
python app.py
```

Mở <http://127.0.0.1:5000>, dán URL tài liệu rồi chọn **Bắt đầu chụp**.

Ví dụ URL được hỗ trợ:

```text
https://docs.google.com/document/d/<document-id>/edit
https://www.scribd.com/document/<document-id>/<slug>
https://fr.scribd.com/document/<document-id>/<slug>
https://www.studocu.com/<locale>/document/.../<document-id>
```
