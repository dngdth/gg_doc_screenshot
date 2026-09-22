import time
import traceback
from pathlib import Path

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from .common import (
    CaptureRuntime,
    capture_virtual_page,
    discover_virtual_pages,
    find_scrollable_container,
    maximize_and_zoom_out,
    save_debug_artifacts,
)


SCRIBD_PAGE_SELECTOR = ".outer_page[id^='outer_page_']"
SCRIBD_FULLSCREEN_SELECTORS = (
    'button[data-e2e="full-screen-icon"]',
    'button[aria-label="Fullscreen"]',
    'button[aria-label="Plein écran"]',
)
SCRIBD_COOKIE_ACCEPT_SELECTORS = (
    "button.osano-cm-accept-all",
    '[role="dialog"][aria-label*="Cookie"] button:has-text("Accept All")',
    '[role="dialog"][aria-label*="Cookie"] button:has-text("Accept all")',
    '[role="dialog"][aria-label*="Cookie"] button:has-text("Tout accepter")',
    '[role="dialog"][aria-label*="Cookie"] button:has-text("Accepter tout")',
)


def _accept_cookies(page, job_id: str, runtime: CaptureRuntime, timeout_ms: int = 0) -> bool:
    deadline = time.monotonic() + timeout_ms / 1000
    while True:
        for selector in SCRIBD_COOKIE_ACCEPT_SELECTORS:
            button = page.locator(selector).first
            try:
                if button.count() and button.is_visible():
                    button.click(timeout=2500)
                    runtime.update_job(job_id, "Đã đóng banner cookie Scribd", status="running")
                    page.wait_for_timeout(400)
                    return True
            except Exception:
                continue
        if time.monotonic() >= deadline:
            return False
        page.wait_for_timeout(250)


def _open_fullscreen(page, job_id: str, runtime: CaptureRuntime) -> None:
    runtime.update_job(job_id, "Đang mở trình đọc toàn màn hình Scribd", status="running")
    for selector in SCRIBD_FULLSCREEN_SELECTORS:
        button = page.locator(selector).first
        try:
            button.wait_for(state="visible", timeout=3000)
            button.click(timeout=5000)
            page.wait_for_timeout(1800)
            return
        except Exception:
            continue
    if page.locator(SCRIBD_PAGE_SELECTOR).count() == 0:
        raise RuntimeError("Không tìm thấy nút fullscreen hoặc khung trang Scribd")


def _wait_for_page_content(page, timeout_ms: int = 10000) -> bool:
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        ready = page.evaluate(
            """
            () => {
                const el = document.querySelector('[data-capture-page-target="1"]');
                if (!el) return false;
                return el.childElementCount > 8 || Boolean(el.querySelector(
                    'canvas, img, svg, object, .text_layer, .image_layer, [style*="background-image"]'
                ));
            }
            """
        )
        if ready:
            page.wait_for_timeout(400)
            return True
        page.wait_for_timeout(300)
    return False


def capture_scribd(
    job_id: str,
    doc_id: str,
    target_url: str,
    output_dir: Path,
    runtime: CaptureRuntime,
) -> None:
    """Pipeline riêng cho Scribd."""
    del doc_id
    files: list[str] = []
    runtime.update_job(
        job_id,
        "Đang khởi tạo pipeline Scribd",
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
                runtime.update_job(job_id, f"Đang mở Scribd: {target_url}", status="running")
                page.goto(
                    target_url,
                    wait_until="domcontentloaded",
                    timeout=runtime.config["nav_timeout"],
                )
                page.wait_for_timeout(3000)
                _accept_cookies(page, job_id, runtime, timeout_ms=4000)

                current_url = page.url.lower()
                if "/login" in current_url or "/signin" in current_url:
                    save_debug_artifacts(page, output_dir)
                    runtime.finish_job(
                        job_id,
                        False,
                        "Scribd yêu cầu đăng nhập",
                        error="Hãy đăng nhập hợp lệ hoặc dùng tài liệu Scribd công khai.",
                        output_dir=str(output_dir),
                    )
                    return

                _open_fullscreen(page, job_id, runtime)
                _accept_cookies(page, job_id, runtime, timeout_ms=750)
                maximize_and_zoom_out(page, job_id, runtime)
                page.locator(SCRIBD_PAGE_SELECTOR).first.wait_for(
                    state="attached",
                    timeout=runtime.config["timeout"],
                )
                scroll_selector = find_scrollable_container(page)
                pages = discover_virtual_pages(
                    page,
                    SCRIBD_PAGE_SELECTOR,
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
                        "Không tìm thấy trang Scribd",
                        error="Không tìm thấy .outer_page hợp lệ.",
                        output_dir=str(output_dir),
                    )
                    return

                runtime.update_job(
                    job_id,
                    f"Đã nhận diện {total} trang Scribd; bắt đầu chụp",
                    status="running",
                )
                for index, geometry in enumerate(pages):
                    page_number = index + 1
                    file_name = f"page_{page_number:03d}.png"
                    file_path = output_dir / file_name
                    try:
                        _accept_cookies(page, job_id, runtime)
                        runtime.update_job(
                            job_id,
                            f"Đang chụp Scribd trang {page_number}/{total}",
                            status="running",
                        )
                        capture_virtual_page(
                            page,
                            SCRIBD_PAGE_SELECTOR,
                            scroll_selector,
                            geometry,
                            file_path,
                        )
                        _wait_for_page_content(page)
                        # Chụp lại sau khi nội dung lazy-load đã sẵn sàng.
                        page.locator('[data-capture-page-target="1"]').screenshot(
                            path=str(file_path),
                            type="png",
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
                            f"Lỗi Scribd trang {page_number}: {shot_error}",
                            status="running",
                        )

                success = len(files) == total
                if not success:
                    save_debug_artifacts(page, output_dir)
                runtime.finish_job(
                    job_id,
                    success,
                    f"Đã chụp {len(files)}/{total} trang Scribd",
                    error=None if success else "Một số trang Scribd không tải hoặc không chụp được.",
                    files=files,
                    pages_captured=len(files),
                    output_dir=str(output_dir),
                )
            except PlaywrightTimeoutError:
                save_debug_artifacts(page, output_dir)
                runtime.finish_job(
                    job_id,
                    False,
                    "Scribd hết thời gian chờ",
                    error="Timeout khi tải hoặc thao tác với Scribd.",
                    files=files,
                    pages_captured=len(files),
                    output_dir=str(output_dir),
                )
            except Exception as error:
                save_debug_artifacts(page, output_dir)
                runtime.finish_job(
                    job_id,
                    False,
                    "Lỗi trong pipeline Scribd",
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
            "Không khởi tạo được pipeline Scribd",
            error=str(error),
            files=files,
            pages_captured=len(files),
            output_dir=str(output_dir),
        )
