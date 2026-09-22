import traceback
from pathlib import Path
from typing import Optional

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from .common import (
    CaptureRuntime,
    capture_virtual_page,
    detect_page_selector,
    discover_virtual_pages,
    find_scrollable_container,
    maximize_and_zoom_out,
    save_debug_artifacts,
)


GOOGLE_DOCS_PAGE_SELECTORS = (
    ".kix-page-paginated",
    ".kix-page",
    ".docs-page",
    "div[data-page-number]",
    "[data-page-id]",
)


def _access_blocked(page) -> Optional[str]:
    current_url = page.url.lower()
    if "accounts.google.com" in current_url or "servicelogin" in current_url:
        return "Google Docs yêu cầu đăng nhập. Hãy đăng nhập hoặc dùng tài liệu công khai."

    try:
        body_text = page.locator("body").inner_text(timeout=4000).lower()
    except Exception:
        body_text = ""

    blocked_phrases = (
        "you need access",
        "request access",
        "access denied",
        "bạn cần có quyền truy cập",
        "yêu cầu quyền truy cập",
        "bạn cần quyền truy cập",
        "không có quyền truy cập",
    )
    for phrase in blocked_phrases:
        if phrase in body_text:
            return (
                f"Không truy cập được Google Docs (phát hiện: '{phrase}'). "
                "Hãy chia sẻ quyền xem hoặc đăng nhập bằng tài khoản có quyền."
            )
    return None


def capture_google_docs(
    job_id: str,
    doc_id: str,
    target_url: str,
    output_dir: Path,
    runtime: CaptureRuntime,
) -> None:
    """Pipeline riêng cho Google Docs, có hỗ trợ DOM virtualized."""
    files: list[str] = []
    runtime.update_job(
        job_id,
        "Đang khởi tạo pipeline Google Docs",
        status="running",
        output_dir=str(output_dir),
    )

    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                headless=runtime.config["headless"],
                args=["--start-maximized", "--disable-blink-features=AutomationControlled"],
            )
            context = browser.new_context(
                viewport={
                    "width": runtime.config["viewport_width"],
                    "height": runtime.config["viewport_height"],
                },
                locale="en-US",
                device_scale_factor=3,
            )
            page = context.new_page()
            page.set_default_timeout(runtime.config["timeout"])
            page.set_default_navigation_timeout(runtime.config["nav_timeout"])

            try:
                runtime.update_job(job_id, f"Đang mở Google Docs: {target_url}", status="running")
                page.goto(
                    target_url,
                    wait_until="domcontentloaded",
                    timeout=runtime.config["nav_timeout"],
                )
                page.wait_for_timeout(3000)

                blocked_message = _access_blocked(page)
                if blocked_message:
                    save_debug_artifacts(page, output_dir)
                    runtime.finish_job(
                        job_id,
                        False,
                        "Google Docs không cho phép truy cập",
                        error=blocked_message,
                        output_dir=str(output_dir),
                    )
                    return

                maximize_and_zoom_out(page, job_id, runtime)
                selector, initial_count = detect_page_selector(
                    page,
                    GOOGLE_DOCS_PAGE_SELECTORS,
                )
                if not selector:
                    save_debug_artifacts(page, output_dir)
                    runtime.finish_job(
                        job_id,
                        False,
                        "Không tìm thấy trang Google Docs",
                        error="Google Docs đã đổi cấu trúc trang hoặc tài liệu chưa tải xong.",
                        output_dir=str(output_dir),
                    )
                    return

                runtime.update_job(
                    job_id,
                    f"Google Docs đang giữ {initial_count} trang trong DOM; bắt đầu quét toàn tài liệu",
                    status="running",
                )
                scroll_selector = find_scrollable_container(
                    page,
                    preferred_selectors=(".kix-appview-editor",),
                )
                pages = discover_virtual_pages(
                    page,
                    selector,
                    scroll_selector,
                    job_id,
                    runtime,
                )
                total = len(pages)
                if total == 0:
                    save_debug_artifacts(page, output_dir)
                    runtime.finish_job(
                        job_id,
                        False,
                        "Không nhận diện được trang Google Docs",
                        error="Đã mở tài liệu nhưng không thu thập được vị trí trang.",
                        output_dir=str(output_dir),
                    )
                    return

                runtime.update_job(
                    job_id,
                    f"Đã nhận diện đủ {total} vị trí trang Google Docs; bắt đầu chụp",
                    status="running",
                )
                for index, geometry in enumerate(pages):
                    page_number = index + 1
                    file_name = f"page_{page_number:03d}.png"
                    file_path = output_dir / file_name
                    try:
                        runtime.update_job(
                            job_id,
                            f"Đang chụp Google Docs trang {page_number}/{total}",
                            status="running",
                        )
                        capture_virtual_page(
                            page,
                            selector,
                            scroll_selector,
                            geometry,
                            file_path,
                        )
                        if file_path.exists() and file_path.stat().st_size > 0:
                            files.append(file_name)
                            runtime.update_job(
                                job_id,
                                f"Đã chụp trang {page_number}/{total}: {file_name}",
                                status="running",
                                files=files.copy(),
                                pages_captured=len(files),
                            )
                    except Exception as shot_error:
                        runtime.update_job(
                            job_id,
                            f"Lỗi Google Docs trang {page_number}: {shot_error}",
                            status="running",
                        )

                if len(files) != total:
                    save_debug_artifacts(page, output_dir)
                    runtime.finish_job(
                        job_id,
                        False,
                        f"Chỉ chụp được {len(files)}/{total} trang Google Docs",
                        error="Một số trang virtualized không render lại đúng lúc. Đã lưu file debug.",
                        files=files,
                        pages_captured=len(files),
                        output_dir=str(output_dir),
                    )
                    return

                runtime.finish_job(
                    job_id,
                    True,
                    f"Hoàn tất: đã chụp {total}/{total} trang Google Docs",
                    files=files,
                    pages_captured=len(files),
                    output_dir=str(output_dir),
                )
            except PlaywrightTimeoutError:
                save_debug_artifacts(page, output_dir)
                runtime.finish_job(
                    job_id,
                    False,
                    "Google Docs hết thời gian chờ",
                    error="Timeout khi tải hoặc thao tác với Google Docs.",
                    files=files,
                    pages_captured=len(files),
                    output_dir=str(output_dir),
                )
            except Exception as error:
                save_debug_artifacts(page, output_dir)
                runtime.finish_job(
                    job_id,
                    False,
                    "Lỗi trong pipeline Google Docs",
                    error=str(error),
                    files=files,
                    pages_captured=len(files),
                    output_dir=str(output_dir),
                )
            finally:
                context.close()
                browser.close()
    except Exception as error:
        traceback.print_exc()
        runtime.finish_job(
            job_id,
            False,
            "Không khởi tạo được pipeline Google Docs",
            error=str(error),
            files=files,
            pages_captured=len(files),
            output_dir=str(output_dir),
        )
