# 📄 Document Page Screenshot Tool

Ứng dụng nhận link Google Docs, Scribd hoặc Studocu và lưu từng trang thành PNG.
Google Docs/Scribd dùng Playwright; Studocu mở bằng Chrome đã cài trên máy và chụp
qua tiện ích trong phiên Chrome của bạn.

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
- Studocu mở Chrome bằng hệ điều hành, dùng phiên đăng nhập của Chrome đang chạy.
  Pipeline Studocu không khởi tạo Playwright, không tạo hồ sơ riêng hay mở cổng debug.
- Tiện ích nhận diện khung `.pf`, thu nhỏ vừa màn hình, cuộn và chụp sau một nhịp
  render 700ms. Ảnh được cắt theo khung giấy và gửi về ứng dụng trên máy.
- Tiến trình Studocu vẫn hiển thị tại ứng dụng và lưu ảnh trong `outputs/`.
- Google Docs/Scribd lưu `debug_dom.html` và `debug_full_page.png` khi gặp lỗi.

## Giới hạn

- Chỉ chụp nội dung mà phiên trình duyệt hiện tại được phép hiển thị.
- Với Studocu, hoàn tất xác minh/đăng nhập trong Chrome trước khi bấm tiện ích.
- Không vượt quyền truy cập, đăng nhập hoặc paywall.
- Selector có thể cần cập nhật nếu Google, Scribd hoặc Studocu thay đổi giao diện.

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

## Chụp Studocu bằng Chrome đang dùng

Cài tiện ích một lần trong Chrome có tài khoản Studocu của bạn:

1. Mở `chrome://extensions`, bật **Developer mode**.
2. Chọn **Load unpacked**, chọn thư mục `chrome_extension` trong dự án.
3. Ghim tiện ích **Document Page Screenshot** vào thanh công cụ.

Sau đó:

1. Chạy `python app.py`, nhập link Studocu và bấm **Bắt đầu chụp**.
2. Chrome mở trang kết nối; tiện ích tự mở tab tài liệu trong cùng cửa sổ.
3. Khi tài liệu đã hiển thị, bấm biểu tượng tiện ích **một lần** để cấp quyền
   `activeTab` và bắt đầu chụp. Nếu chưa cài tiện ích, trang kết nối hiện hướng dẫn.
4. Giữ tab tài liệu hoạt động trong lúc chụp. Ảnh tự lưu vào `outputs/`; quay về
   ứng dụng để xem tiến trình sau khi tiện ích hiện **OK**.

Tiện ích có quyền đọc khung trang trên `studocu.vn`/`studocu.com` và kết nối
`http://127.0.0.1:5000`; quyền chụp tab `activeTab` được cấp khi bạn bấm biểu tượng.
Quyền Studocu áp dụng cả tên miền gốc và tên miền con để giữ truy cập khi website
chuyển hướng. Không cần lấy cookie, sao chép hồ sơ Chrome hay đóng Chrome.
Nếu chuyển sang tab khác trong lúc chụp, job dừng để tránh lưu nhầm tab.
Khung xem trước, nội dung làm mờ và lớp giới hạn truy cập được giữ nguyên.

Ứng dụng tự tìm Chrome tại vị trí cài đặt thông thường. Nếu cần, đặt
`CONFIG["chrome_executable"]` trong `app.py` hoặc biến môi trường `CHROME_PATH`.
`CONFIG["chrome_capture_timeout"]` là số giây chờ tiện ích trước khi job hết hạn,
mặc định 600 giây. Khi khởi động lại ứng dụng, tạo job mới để chụp.

Nếu tiện ích hiện **ERR**, xem thông báo trên tab hoặc dòng cuối trong tiến trình
ứng dụng. Sau khi cập nhật mã tiện ích, vào `chrome://extensions` và bấm **Reload**
trên Document Page Screenshot. Nếu Chrome hỏi quyền Studocu, cần cấp quyền này.

Ví dụ URL được hỗ trợ:

```text
https://docs.google.com/document/d/<document-id>/edit
https://www.scribd.com/document/<document-id>/<slug>
https://fr.scribd.com/document/<document-id>/<slug>
https://www.studocu.com/<locale>/document/.../<document-id>
https://www.studocu.vn/vn/document/.../<document-id>
```
