import os
import re
import threading
import time
import traceback
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional
import json

from flask import Flask, jsonify, render_template, request
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
app = Flask(__name__)

BASE_DIR = Path(__file__).resolve().parent
OUTPUTS_DIR = BASE_DIR / "outputs"
OUTPUTS_DIR.mkdir(parents=True, exist_ok=True)

CONFIG = {
    "headless": False,
    "timeout": 25000,
    "nav_timeout": 35000,
    "viewport_width": 1920,
    "viewport_height": 1080,
    "scroll_pause": 1.0,
    "page_shot_pause": 0.5,
    "final_wait": 2.0,
    "max_scrolls": 120,
    "poll_seconds": 1.2,
}

# Lưu trạng thái các job đang chạy để frontend poll tiến trình
JOBS = {}
JOBS_LOCK = threading.Lock()


def new_job(doc_id: Optional[str] = None, docs_url: str = "") -> str:
    """Tạo job mới."""
    job_id = uuid.uuid4().hex[:12]
    with JOBS_LOCK:
        JOBS[job_id] = {
            "job_id": job_id,
            "status": "queued",
            "step": "Đang chờ xử lý",
            "progress": [],
            "docs_url": docs_url,
            "doc_id": doc_id,
            "output_dir": None,
            "files": [],
            "pages_captured": 0,
            "success": False,
            "error": None,
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
    return job_id


def update_job(job_id: str, message: str, *, status: Optional[str] = None, error: Optional[str] = None, **extra):
    """Cập nhật log tiến trình cho job."""
    with JOBS_LOCK:
        job = JOBS[job_id]
        if status:
            job["status"] = status
        if error is not None:
            job["error"] = error
        job["step"] = message
        job["progress"].append(f"[{datetime.now().strftime('%H:%M:%S')}] {message}")
        for k, v in extra.items():
            job[k] = v


def finish_job(job_id: str, success: bool, message: str, **extra):
    """Đánh dấu job hoàn tất."""
    with JOBS_LOCK:
        job = JOBS[job_id]
        job["success"] = success
        job["status"] = "done" if success else "failed"
        job["step"] = message
        job["progress"].append(f"[{datetime.now().strftime('%H:%M:%S')}] {message}")
        for k, v in extra.items():
            job[k] = v


def extract_doc_id(url: str) -> dict:
    """Tách doc_id từ URL Google Docs."""
    if not url or not url.strip():
        return {"ok": False, "doc_id": None, "error": "URL không được để trống."}

    url = url.strip()
    pattern = r"https?://docs\.google\.com/document/d/([a-zA-Z0-9_-]+)"
    match = re.search(pattern, url)
    if not match:
        return {
            "ok": False,
            "doc_id": None,
            "error": "URL không đúng định dạng Google Docs. Ví dụ: https://docs.google.com/document/d/<id>/edit",
        }
    return {"ok": True, "doc_id": match.group(1), "error": None}


def make_output_dir(doc_id: str) -> Path:
    """Tạo thư mục output riêng cho mỗi lần chạy."""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_dir = OUTPUTS_DIR / f"{doc_id}_{ts}"
    output_dir.mkdir(parents=True, exist_ok=True)
    return output_dir


def is_access_blocked(page) -> Optional[str]:
    """
    Chỉ coi là bị chặn khi:
    - bị chuyển sang trang đăng nhập Google
    - hoặc xuất hiện thông báo yêu cầu quyền xem / request access thật sự
    - còn trường hợp 'Yêu cầu quyền chỉnh sửa' thì vẫn cho chụp bình thường
    """
    current_url = page.url.lower()

    # 1) Nếu bị redirect sang trang login thật sự
    if "accounts.google.com" in current_url or "servicelogin" in current_url:
        return "Google Docs yêu cầu đăng nhập. Hãy đăng nhập bằng tài khoản có quyền hoặc dùng tài liệu public."

    # 2) Lấy text body để dò thông báo chặn truy cập thật
    body_text = ""
    try:
        body_text = page.locator("body").inner_text(timeout=4000).lower()
    except Exception:
        pass

    # 3) Các dấu hiệu bị chặn truy cập thật
    blocked_phrases = [
        "you need access",
        "request access",
        "access denied",
        "bạn cần có quyền truy cập",
        "yêu cầu quyền truy cập",
        "bạn cần quyền truy cập",
        "không có quyền truy cập",
    ]

    for phrase in blocked_phrases:
        if phrase in body_text:
            return (
                f"Không truy cập được tài liệu (phát hiện nội dung: '{phrase}'). "
                "Hãy đặt chia sẻ là 'Anyone with the link can view' hoặc đăng nhập bằng tài khoản có quyền."
            )

    # 4) KHÔNG coi 'đăng nhập' hay 'yêu cầu quyền chỉnh sửa' là bị chặn
    return None

def _save_debug_artifacts(page, output_dir: Path):
    """Lưu file debug để kiểm tra khi thất bại."""
    try:
        (output_dir / "debug_dom.html").write_text(page.content(), encoding="utf-8")
    except Exception:
        pass
    try:
        page.screenshot(path=str(output_dir / "debug_full_page.png"), full_page=True)
    except Exception:
        pass

def _maximize_and_zoom_out(page, job_id: str):
    """
    Phóng to vùng hiển thị và zoom out để thấy nhiều trang hơn.
    Ưu tiên dùng phím tắt browser zoom vì gần với thao tác thật của người dùng.
    """
    update_job(job_id, "Đang đưa cửa sổ về trạng thái hiển thị lớn", status="running")
    page.wait_for_timeout(1200)

    # reset zoom về 100%
    try:
        page.keyboard.press("Control+0")
        page.wait_for_timeout(500)
    except Exception:
        pass

    # zoom out dần xuống khoảng 70%
    update_job(job_id, "Đang thu nhỏ giao diện xuống khoảng 70%", status="running")
    for _ in range(3):  # 100 -> 90 -> 80 -> 70
        try:
            page.keyboard.press("Control+-")
            page.wait_for_timeout(450)
        except Exception:
            break

    # click vào giữa để đảm bảo focus vào vùng tài liệu
    try:
        page.mouse.click(900, 500)
    except Exception:
        pass

    page.wait_for_timeout(1800)

def _wait_for_pages_stable(page, selector: str, job_id: str, rounds: int = 4):
    """
    Chờ số trang ổn định sau khi zoom/scroll.
    """
    stable = 0
    prev = -1

    for i in range(20):
        try:
            count = page.evaluate(
                """
                (selector) => {
                    return [...document.querySelectorAll(selector)].filter(el => {
                        const r = el.getBoundingClientRect();
                        return r.width > 300 && r.height > 400;
                    }).length;
                }
                """,
                selector
            )
        except Exception:
            count = 0

        update_job(job_id, f"Đang chờ layout ổn định... hiện thấy {count} trang", status="running")

        if count == prev and count > 0:
            stable += 1
        else:
            stable = 0
            prev = count

        if stable >= rounds:
            break

        page.wait_for_timeout(600)

def _detect_page_selector(page):
    selectors = [
        ".kix-page-paginated",
        ".kix-page",
        "[class*='kix-page-paginated']",
        "[class*='kix-page']",
        ".docs-page",
        "div[data-page-number]",
        "[data-page-id]",
        "div.kix-appview-editor div > div",
    ]

    for sel in selectors:
        try:
            count = page.evaluate(
                """
                (selector) => {
                    const els = [...document.querySelectorAll(selector)];
                    return els.filter(el => {
                        const r = el.getBoundingClientRect();
                        return r.width > 300 && r.height > 400;
                    }).length;
                }
                """,
                sel
            )
            print(f"[DETECT] Selector '{sel}' found {count} pages")
            if count > 0:
                return sel, count
        except Exception as e:
            print(f"[DETECT] Selector '{sel}' error: {e}")
            continue

    return None, 0
def _scroll_document(page, selector: str, job_id: str):
    """
    Cuộn đúng container để Google Docs render hết các trang.
    """
    scroll_root = _find_scrollable_container(page)
    update_job(job_id, f"Đã xác định vùng cuộn: {scroll_root}", status="running")

    previous = 0
    stable_rounds = 0
    max_same = 5
    reached_bottom = False

    for i in range(CONFIG["max_scrolls"]):
        try:
            page.evaluate(
                """
                (scrollSelector) => {
                    const el = document.querySelector(scrollSelector);
                    if (!el || scrollSelector === "body") {
                        window.scrollBy(0, 1400);
                    } else {
                        el.scrollBy(0, 1400);
                    }
                }
                """,
                scroll_root
            )
        except Exception:
            page.mouse.wheel(0, 1400)

        time.sleep(CONFIG["scroll_pause"])

        try:
            count = page.evaluate(
                """
                (selector) => {
                    return [...document.querySelectorAll(selector)].filter(el => {
                        const r = el.getBoundingClientRect();
                        return r.width > 300 && r.height > 400;
                    }).length;
                }
                """,
                selector
            )
        except Exception:
            count = previous

        update_job(
            job_id,
            f"Đang cuộn để render tài liệu... lần {i + 1}, đang thấy {count} trang",
            status="running"
        )

        if count == previous:
            stable_rounds += 1
        else:
            stable_rounds = 0
            previous = count

        reached_bottom = page.evaluate(
            """
            (scrollSelector) => {
                const el = document.querySelector(scrollSelector);
                if (!el || scrollSelector === "body") {
                    const root = document.scrollingElement || document.documentElement;
                    return Math.abs((root.scrollHeight - root.clientHeight) - root.scrollTop) < 30;
                }
                return Math.abs((el.scrollHeight - el.clientHeight) - el.scrollTop) < 30;
            }
            """,
            scroll_root
        )

        if reached_bottom:
            update_job(job_id, f"Đã cuộn đến cuối tài liệu.", status="running")
            if stable_rounds >= max_same:
                break
        else:
            # If not at bottom, continue scrolling even if stable
            if stable_rounds >= max_same:
                # Force continue scrolling to reach bottom
                stable_rounds = 0

    # Đợi thêm một chút để các trang render xong
    time.sleep(1.5)
    
    # quay lại đầu tài liệu
    page.evaluate(
        """
        (scrollSelector) => {
            const el = document.querySelector(scrollSelector);
            if (!el || scrollSelector === "body") {
                window.scrollTo(0, 0);
            } else {
                el.scrollTo(0, 0);
            }
        }
        """,
        scroll_root
    )
    time.sleep(1.0)

def _find_scrollable_container(page):
    """
    Tìm phần tử đang thực sự scroll trong Google Docs.
    Trả về CSS selector tạm thời để dùng ở các bước sau.
    """
    result = page.evaluate(
        """
        () => {
            function cssPath(el) {
                if (!el || el === document.body) return "body";
                if (el.id) return "#" + el.id;
                const parts = [];
                while (el && el.nodeType === 1 && el !== document.body) {
                    let part = el.tagName.toLowerCase();
                    if (el.className && typeof el.className === "string") {
                        const cls = el.className.trim().split(/\\s+/).slice(0, 2).join(".");
                        if (cls) part += "." + cls;
                    }
                    parts.unshift(part);
                    el = el.parentElement;
                }
                return parts.join(" > ") || "body";
            }

            const all = [document.scrollingElement, ...document.querySelectorAll("div, main, section")].filter(Boolean);

            let best = null;
            let bestScore = -1;

            for (const el of all) {
                const style = getComputedStyle(el);
                const canScroll = (
                    ["auto", "scroll"].includes(style.overflowY) ||
                    ["auto", "scroll"].includes(style.overflow)
                );

                const diff = el.scrollHeight - el.clientHeight;
                const rect = el.getBoundingClientRect();

                if (diff > 200 && rect.height > 300) {
                    let score = diff;
                    if (canScroll) score += 100000;
                    if (rect.width > 600) score += 5000;
                    if (rect.height > 500) score += 5000;

                    if (score > bestScore) {
                        bestScore = score;
                        best = el;
                    }
                }
            }

            if (!best) return { selector: "body" };

            if (!best.hasAttribute("data-capture-scroll-root")) {
                best.setAttribute("data-capture-scroll-root", "1");
            }

            return { selector: '[data-capture-scroll-root="1"]' };
        }
        """
    )
    return result.get("selector", "body")

def _get_page_boxes(page, selector: str):
    return page.evaluate(
        """
        (selector) => {
            const els = [...document.querySelectorAll(selector)].filter(el => {
                const r = el.getBoundingClientRect();
                return r.width > 300 && r.height > 400;
            });

            return els.map((el, idx) => {
                const r = el.getBoundingClientRect();
                return {
                    index: idx,
                    left: r.left,
                    top: r.top,
                    width: r.width,
                    height: r.height
                };
            });
        }
        """,
        selector
    )

def _capture_single_page(page, selector: str, idx: int, file_path: Path):
    """Chụp 1 trang duy nhất."""
    # Đầu tiên, scroll vào trang đó
    page.evaluate(
        """
        ({selector, index}) => {
            const els = [...document.querySelectorAll(selector)].filter(el => {
                const r = el.getBoundingClientRect();
                return r.width > 300 && r.height > 400;
            });

            const el = els[index];
            if (!el) return;
            el.scrollIntoView({block: "center", inline: "center", behavior: "auto"});
        }
        """,
        {"selector": selector, "index": idx}
    )

    # Chờ thêm để trang render xong
    page.wait_for_timeout(1000)

    # Lấy bounding box của trang
    box = page.evaluate(
        """
        ({selector, index}) => {
            const els = [...document.querySelectorAll(selector)].filter(el => {
                const r = el.getBoundingClientRect();
                return r.width > 300 && r.height > 400;
            });

            const el = els[index];
            if (!el) return null;

            const r = el.getBoundingClientRect();
            return {
                left: r.left,
                top: r.top,
                width: r.width,
                height: r.height
            };
        }
        """,
        {"selector": selector, "index": idx}
    )

    if not box:
        raise RuntimeError(f"Không lấy được bounding box của trang {idx + 1}")

    margin = 8
    clip = {
        "x": max(0, box["left"] - margin),
        "y": max(0, box["top"] - margin),
        "width": box["width"] + margin * 2,
        "height": box["height"] + margin * 2,
    }

    page.screenshot(path=str(file_path), type="png", clip=clip)

def capture_docs(job_id: str, doc_id: str, output_dir: Path):
    """
    Hàm chạy nền:
    - mở Google Docs
    - phóng to cửa sổ
    - zoom out xuống khoảng 50%
    - đợi render ổn định
    - tìm bounding box từng trang
    - chụp từng trang đúng 1 ảnh
    """
    target_url = f"https://docs.google.com/document/d/{doc_id}/edit"
    files = []

    update_job(job_id, "Đang khởi tạo Playwright", status="running", output_dir=str(output_dir))

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=CONFIG["headless"],
                args=[
                    "--start-maximized",
                    "--disable-blink-features=AutomationControlled",
                ],
            )

            context = browser.new_context(
                viewport={"width": CONFIG["viewport_width"], "height": CONFIG["viewport_height"]},
                locale="en-US",
                device_scale_factor=3,
            )

            page = context.new_page()
            page.set_default_timeout(CONFIG["timeout"])
            page.set_default_navigation_timeout(CONFIG["nav_timeout"])

            try:
                update_job(job_id, f"Đang truy cập vào web: {target_url}", status="running")
                page.goto(target_url, wait_until="load", timeout=CONFIG["nav_timeout"])
                page.wait_for_timeout(2500)

                update_job(job_id, f"Đã truy cập vào web, URL hiện tại: {page.url}", status="running")

                blocked_message = is_access_blocked(page)
                if blocked_message:
                    _save_debug_artifacts(page, output_dir)
                    finish_job(job_id, False, "Dừng vì không có quyền truy cập", error=blocked_message, output_dir=str(output_dir))
                    return

                # fullscreen + zoom out
                _maximize_and_zoom_out(page, job_id)

                update_job(job_id, "Đang tìm selector trang trong Google Docs", status="running")
                selector, initial_count = _detect_page_selector(page)

                if not selector:
                    _save_debug_artifacts(page, output_dir)
                    finish_job(
                        job_id,
                        False,
                        "Không tìm được vùng trang để chụp",
                        error="Không tìm được page selector sau khi zoom out. Đã lưu file debug.",
                        output_dir=str(output_dir),
                    )
                    return

                update_job(
                    job_id,
                    f"Đã tìm thấy selector trang: {selector}, tạm thấy {initial_count} trang",
                    status="running",
                )

                # cuộn để ép render thêm
                _scroll_document(page, selector, job_id)
                _wait_for_pages_stable(page, selector, job_id, rounds=4)

                boxes = _get_page_boxes(page, selector)
                total = len(boxes)

                update_job(job_id, f"Đã render xong, tổng số trang phát hiện được: {total}", status="running")

                if total == 0:
                    _save_debug_artifacts(page, output_dir)
                    finish_job(
                        job_id,
                        False,
                        "Không có trang nào để chụp",
                        error="Không tìm được trang giấy hợp lệ sau khi zoom out và render.",
                        output_dir=str(output_dir),
                    )
                    return

                for i, b in enumerate(boxes[:15], start=1):
                    update_job(
                        job_id,
                        f"Debug box trang {i}: x={int(b['left'])}, y={int(b['top'])}, w={int(b['width'])}, h={int(b['height'])}",
                        status="running",
                    )

                for idx in range(total):
                    page_num = idx + 1
                    file_name = f"page_{page_num:03d}.png"
                    file_path = output_dir / file_name

                    try:
                        update_job(job_id, f"Đang chụp trang {page_num}/{total}", status="running")
                        _capture_single_page(page, selector, idx, file_path)

                        if file_path.exists() and file_path.stat().st_size > 0:
                            files.append(file_name)
                            update_job(
                                job_id,
                                f"Đã chụp trang {page_num}/{total}: {file_name}",
                                status="running",
                                files=files.copy(),
                                pages_captured=len(files),
                            )
                        else:
                            update_job(job_id, f"Trang {page_num} không tạo được file ảnh hợp lệ", status="running")

                    except Exception as shot_error:
                        update_job(job_id, f"Lỗi khi chụp trang {page_num}: {shot_error}", status="running")

                if not files:
                    _save_debug_artifacts(page, output_dir)
                    finish_job(
                        job_id,
                        False,
                        "Không chụp được trang nào",
                        error="Đã vào được tài liệu nhưng không tạo được ảnh nào. Hãy xem file debug.",
                        files=[],
                        pages_captured=0,
                        output_dir=str(output_dir),
                    )
                    return

                finish_job(
                    job_id,
                    True,
                    f"Hoàn tất: đã chụp {len(files)}/{total} trang",
                    files=files,
                    pages_captured=len(files),
                    output_dir=str(output_dir),
                )

            except PlaywrightTimeoutError:
                _save_debug_artifacts(page, output_dir)
                finish_job(
                    job_id,
                    False,
                    "Hết thời gian chờ",
                    error="Timeout khi tải hoặc thao tác với Google Docs.",
                    output_dir=str(output_dir),
                )
            except Exception as inner_error:
                _save_debug_artifacts(page, output_dir)
                finish_job(
                    job_id,
                    False,
                    "Lỗi khi xử lý tài liệu",
                    error=str(inner_error),
                    output_dir=str(output_dir),
                )
            finally:
                try:
                    context.close()
                except Exception:
                    pass
                try:
                    browser.close()
                except Exception:
                    pass

    except Exception as e:
        traceback.print_exc()
        finish_job(
            job_id,
            False,
            "Lỗi hệ thống khi chạy Playwright",
            error=str(e),
            output_dir=str(output_dir),
        )

@app.route("/", methods=["GET"])
def index():
    return render_template("index.html")


@app.route("/start_capture", methods=["POST"])
def start_capture():
    """Tạo job và chạy ở thread nền."""
    docs_url = request.form.get("docs_url", "").strip()
    validation = extract_doc_id(docs_url)

    if not validation["ok"]:
        return jsonify({"ok": False, "error": validation["error"]}), 400

    doc_id = validation["doc_id"]
    output_dir = make_output_dir(doc_id)

    job_id = new_job(doc_id=doc_id, docs_url=docs_url)
    print(f"[START_CAPTURE] PID={os.getpid()} created job_id={job_id}")
    update_job(job_id, "Đã tạo job mới", status="queued", output_dir=str(output_dir))

    thread = threading.Thread(target=capture_docs, args=(job_id, doc_id, output_dir), daemon=True)
    thread.start()

    return jsonify({"ok": True, "job_id": job_id})


@app.route("/status/<job_id>", methods=["GET"])
def status(job_id):
    with JOBS_LOCK:
        print(f"[STATUS] PID={os.getpid()} job_id={job_id} all_jobs={list(JOBS.keys())}")
        job = JOBS.get(job_id)
        if not job:
            return jsonify({"ok": False, "error": "Không tìm thấy job."}), 404
        return jsonify({"ok": True, "job": job})

if __name__ == "__main__":
    OUTPUTS_DIR.mkdir(exist_ok=True)
    print("=" * 50)
    print("  Google Docs Screenshot App")
    print("  http://127.0.0.1:5000")
    print("=" * 50)
    app.run(
        debug=False,
        use_reloader=False,
        host="127.0.0.1",
        port=5000,
        threaded=True
    )