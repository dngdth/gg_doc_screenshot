import traceback
from pathlib import Path

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


STUDOCU_PAGE_SELECTORS = (
    '[data-testid="document-page"]',
    '[data-test="document-page"]',
    '[data-test-selector="document-page"]',
    '[data-page-number]',
    '.document-page',
    '[class*="document-page"]',
    '[class*="page-container"]',
    '[class*="pageWrapper"]',
)
STUDOCU_COOKIE_SELECTORS = (
    '#onetrust-accept-btn-handler',
    'button:has-text("Accept all")',
    'button:has-text("Allow all")',
    'button:has-text("Đồng ý tất cả")',
)


def _dismiss_cookies(page) -> None:
    for selector in STUDOCU_COOKIE_SELECTORS:
        try:
            button = page.locator(selector).first
            if button.count() and button.is_visible():
                button.click(timeout=2500)
                page.wait_for_timeout(500)
                return
        except Exception:
            continue


def _blocked_message(page) -> str | None:
    current_url = page.url.lower()
    try:
        title = page.title().lower()
        body = page.locator("body").inner_text(timeout=4000).lower()
    except Exception:
        title = ""
        body = ""

    if "/login" in current_url or "/signin" in current_url:
        return "Studocu yêu cầu đăng nhập để xem tài liệu này."
    if "access blocked" in title or "suspicious activity" in body:
        return "Studocu đã chặn kết nối tự động hiện tại. Hãy thử lại từ kết nối/trình duyệt được Studocu cho phép."
    if "captcha" in body or "verify you are human" in body:
        return "Studocu yêu cầu xác minh CAPTCHA; ứng dụng không vượt bước xác minh này."
    return None


def capture_studocu(
    job_id: str,
    doc_id: str,
    target_url: str,
    output_dir: Path,
    runtime: CaptureRuntime,
) -> None:
    """Pipeline riêng cho Studocu; không dùng selector của Scribd/Google Docs."""
    del doc_id
    files: list[str] = []
    runtime.update_job(
        job_id,
        "Đang khởi tạo pipeline Studocu",
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
                runtime.update_job(job_id, f"Đang mở Studocu: {target_url}", status="running")
                page.goto(
                    target_url,
                    wait_until="domcontentloaded",
                    timeout=runtime.config["nav_timeout"],
                )
                page.wait_for_timeout(4000)
                _dismiss_cookies(page)

                blocked = _blocked_message(page)
                if blocked:
                    save_debug_artifacts(page, output_dir)
                    runtime.finish_job(
                        job_id,
                        False,
                        "Studocu không cho phép truy cập",
                        error=blocked,
                        output_dir=str(output_dir),
                    )
                    return

                maximize_and_zoom_out(page, job_id, runtime, steps=2)
                selector, initial_count = detect_page_selector(
                    page,
                    STUDOCU_PAGE_SELECTORS,
                    min_width=420,
                    min_height=450,
                )
                if not selector:
                    save_debug_artifacts(page, output_dir)
                    runtime.finish_job(
                        job_id,
                        False,
                        "Không tìm thấy trang Studocu",
                        error="Studocu đã đổi cấu trúc viewer, tài liệu chưa tải, hoặc tài liệu bị giới hạn.",
                        output_dir=str(output_dir),
                    )
                    return

                runtime.update_job(
                    job_id,
                    f"Studocu đang hiển thị {initial_count} khung; bắt đầu quét toàn tài liệu",
                    status="running",
                )
                scroll_selector = find_scrollable_container(page)
                pages = discover_virtual_pages(
                    page,
                    selector,
                    scroll_selector,
                    job_id,
                    runtime,
                    min_width=420,
                    min_height=450,
                )
                total = len(pages)
                if total == 0:
                    save_debug_artifacts(page, output_dir)
                    runtime.finish_job(
                        job_id,
                        False,
                        "Không nhận diện được trang Studocu",
                        error="Không thu thập được vị trí trang từ viewer Studocu.",
                        output_dir=str(output_dir),
                    )
                    return

                for index, geometry in enumerate(pages):
                    page_number = index + 1
                    file_name = f"page_{page_number:03d}.png"
                    file_path = output_dir / file_name
                    try:
                        runtime.update_job(
                            job_id,
                            f"Đang chụp Studocu trang {page_number}/{total}",
                            status="running",
                        )
                        capture_virtual_page(
                            page,
                            selector,
                            scroll_selector,
                            geometry,
                            file_path,
                            min_width=420,
                            min_height=450,
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
                            f"Lỗi Studocu trang {page_number}: {shot_error}",
                            status="running",
                        )

                success = len(files) == total
                if not success:
                    save_debug_artifacts(page, output_dir)
                runtime.finish_job(
                    job_id,
                    success,
                    f"Đã chụp {len(files)}/{total} trang Studocu",
                    error=None if success else "Một số trang Studocu không tải hoặc không chụp được.",
                    files=files,
                    pages_captured=len(files),
                    output_dir=str(output_dir),
                )
            except PlaywrightTimeoutError:
                save_debug_artifacts(page, output_dir)
                runtime.finish_job(
                    job_id,
                    False,
                    "Studocu hết thời gian chờ",
                    error="Timeout khi tải hoặc thao tác với Studocu.",
                    files=files,
                    pages_captured=len(files),
                    output_dir=str(output_dir),
                )
            except Exception as error:
                save_debug_artifacts(page, output_dir)
                runtime.finish_job(
                    job_id,
                    False,
                    "Lỗi trong pipeline Studocu",
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
            "Không khởi tạo được pipeline Studocu",
            error=str(error),
            files=files,
            pages_captured=len(files),
            output_dir=str(output_dir),
        )
