from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence


UpdateJob = Callable[..., None]
FinishJob = Callable[..., None]


@dataclass(frozen=True)
class CaptureRuntime:
    config: Mapping[str, Any]
    update_job: UpdateJob
    finish_job: FinishJob


def save_debug_artifacts(page, output_dir: Path) -> None:
    """Lưu DOM và ảnh toàn màn hình khi pipeline gặp lỗi."""
    try:
        (output_dir / "debug_dom.html").write_text(page.content(), encoding="utf-8")
    except Exception:
        pass

    try:
        page.screenshot(path=str(output_dir / "debug_full_page.png"), full_page=True)
    except Exception:
        pass


def maximize_and_zoom_out(page, job_id: str, runtime: CaptureRuntime, steps: int = 3) -> None:
    runtime.update_job(job_id, "Đang tối ưu vùng hiển thị", status="running")
    page.wait_for_timeout(800)

    try:
        page.keyboard.press("Control+0")
        page.wait_for_timeout(350)
    except Exception:
        pass

    for _ in range(steps):
        try:
            page.keyboard.press("Control+-")
            page.wait_for_timeout(350)
        except Exception:
            break

    try:
        page.mouse.click(900, 500)
    except Exception:
        pass

    page.wait_for_timeout(1200)


def detect_page_selector(
    page,
    selectors: Sequence[str],
    *,
    min_width: int = 300,
    min_height: int = 400,
) -> tuple[str | None, int]:
    """Chọn selector cụ thể nhất có ít nhất một khung trang hợp lệ."""
    for selector in selectors:
        try:
            count = page.evaluate(
                """
                ({selector, minWidth, minHeight}) =>
                    [...document.querySelectorAll(selector)].filter(el => {
                        const r = el.getBoundingClientRect();
                        return r.width >= minWidth && r.height >= minHeight;
                    }).length
                """,
                {
                    "selector": selector,
                    "minWidth": min_width,
                    "minHeight": min_height,
                },
            )
        except Exception:
            count = 0

        if count:
            return selector, count

    return None, 0


def find_scrollable_container(page, preferred_selectors: Sequence[str] = ()) -> str:
    """Đánh dấu và trả về vùng cuộn phù hợp nhất của riêng pipeline hiện tại."""
    result = page.evaluate(
        """
        (preferred) => {
            const candidates = [];
            for (const selector of preferred) {
                const el = document.querySelector(selector);
                if (el) candidates.push(el);
            }
            candidates.push(
                document.scrollingElement,
                ...document.querySelectorAll('div, main, section, article')
            );

            let best = null;
            let bestScore = -1;
            for (const el of candidates.filter(Boolean)) {
                const rect = el.getBoundingClientRect();
                const distance = el.scrollHeight - el.clientHeight;
                if (distance <= 200 || el.clientHeight <= 250) continue;

                const style = getComputedStyle(el);
                const explicitlyScrollable = ['auto', 'scroll'].includes(style.overflowY);
                let score = distance;
                if (explicitlyScrollable) score += 1000000;
                if (preferred.some(selector => el.matches(selector))) score += 10000000;
                if (rect.width > 500) score += 10000;
                if (score > bestScore) {
                    best = el;
                    bestScore = score;
                }
            }

            if (!best || best === document.body || best === document.documentElement ||
                best === document.scrollingElement) {
                return {selector: 'body'};
            }

            document.querySelectorAll('[data-capture-scroll-root]').forEach(el =>
                el.removeAttribute('data-capture-scroll-root')
            );
            best.setAttribute('data-capture-scroll-root', '1');
            return {selector: '[data-capture-scroll-root="1"]'};
        }
        """,
        list(preferred_selectors),
    )
    return result.get("selector", "body")


def _set_scroll_top(page, scroll_selector: str, top: float) -> dict:
    return page.evaluate(
        """
        ({scrollSelector, top}) => {
            const isBody = scrollSelector === 'body';
            const root = isBody
                ? (document.scrollingElement || document.documentElement)
                : document.querySelector(scrollSelector);
            if (!root) throw new Error(`Không tìm thấy vùng cuộn: ${scrollSelector}`);
            if (isBody) window.scrollTo(0, top);
            else root.scrollTop = top;
            return {
                scrollTop: isBody ? window.scrollY : root.scrollTop,
                scrollHeight: root.scrollHeight,
                clientHeight: root.clientHeight,
            };
        }
        """,
        {"scrollSelector": scroll_selector, "top": top},
    )


def _visible_page_geometry(
    page,
    selector: str,
    scroll_selector: str,
    min_width: int,
    min_height: int,
) -> dict:
    return page.evaluate(
        """
        ({selector, scrollSelector, minWidth, minHeight}) => {
            const isBody = scrollSelector === 'body';
            const root = isBody
                ? (document.scrollingElement || document.documentElement)
                : document.querySelector(scrollSelector);
            if (!root) throw new Error(`Không tìm thấy vùng cuộn: ${scrollSelector}`);

            const rootRect = isBody ? {top: 0, left: 0} : root.getBoundingClientRect();
            const scrollTop = isBody ? window.scrollY : root.scrollTop;
            const scrollLeft = isBody ? window.scrollX : root.scrollLeft;
            const pages = [...document.querySelectorAll(selector)]
                .map(el => {
                    const r = el.getBoundingClientRect();
                    return {
                        top: r.top - rootRect.top + scrollTop,
                        left: r.left - rootRect.left + scrollLeft,
                        width: r.width,
                        height: r.height,
                    };
                })
                .filter(item => item.width >= minWidth && item.height >= minHeight);

            return {
                scrollTop,
                scrollHeight: root.scrollHeight,
                clientHeight: root.clientHeight,
                pages,
            };
        }
        """,
        {
            "selector": selector,
            "scrollSelector": scroll_selector,
            "minWidth": min_width,
            "minHeight": min_height,
        },
    )


def _merge_page_geometry(pages: list[dict], candidate: dict) -> None:
    tolerance = max(4.0, candidate["height"] * 0.015)
    for known in pages:
        if abs(known["top"] - candidate["top"]) <= tolerance:
            known.update(candidate)
            return
    pages.append(candidate.copy())


def discover_virtual_pages(
    page,
    selector: str,
    scroll_selector: str,
    job_id: str,
    runtime: CaptureRuntime,
    *,
    min_width: int = 300,
    min_height: int = 400,
) -> list[dict]:
    """
    Quét toàn vùng cuộn và nhớ vị trí tuyệt đối của trang.

    Cách này không phụ thuộc số node đang có trong DOM, nên xử lý được Google
    Docs và các viewer tái sử dụng một nhóm node nhỏ khi cuộn.
    """
    pages: list[dict] = []
    position = 0.0
    iterations = 0
    max_iterations = int(runtime.config.get("max_scrolls", 120))

    while iterations < max_iterations:
        metrics = _set_scroll_top(page, scroll_selector, position)
        page.wait_for_timeout(int(float(runtime.config.get("scroll_pause", 0.6)) * 1000))
        state = _visible_page_geometry(
            page,
            selector,
            scroll_selector,
            min_width,
            min_height,
        )
        for candidate in state["pages"]:
            _merge_page_geometry(pages, candidate)

        pages.sort(key=lambda item: item["top"])
        runtime.update_job(
            job_id,
            f"Đang quét toàn tài liệu... đã nhận diện {len(pages)} trang",
            status="running",
        )

        max_scroll = max(0.0, state["scrollHeight"] - state["clientHeight"])
        current = float(state["scrollTop"])
        if current >= max_scroll - 2:
            # Quét lại đáy một nhịp vì viewer thường gắn trang cuối hơi trễ.
            page.wait_for_timeout(500)
            final_state = _visible_page_geometry(
                page,
                selector,
                scroll_selector,
                min_width,
                min_height,
            )
            for candidate in final_state["pages"]:
                _merge_page_geometry(pages, candidate)
            break

        step = max(300.0, state["clientHeight"] * 0.72)
        next_position = min(max_scroll, current + step)
        if next_position <= current + 1:
            break
        position = next_position
        iterations += 1

    _set_scroll_top(page, scroll_selector, 0)
    page.wait_for_timeout(500)
    pages.sort(key=lambda item: item["top"])
    return pages


def capture_virtual_page(
    page,
    selector: str,
    scroll_selector: str,
    geometry: dict,
    file_path: Path,
    *,
    min_width: int = 300,
    min_height: int = 400,
) -> None:
    """Nạp lại đúng trang ảo theo vị trí tuyệt đối rồi chụp element của nó."""
    metrics = _set_scroll_top(page, scroll_selector, 0)
    desired_top = geometry["top"] - max(0.0, (metrics["clientHeight"] - geometry["height"]) / 2)
    max_scroll = max(0.0, metrics["scrollHeight"] - metrics["clientHeight"])
    _set_scroll_top(page, scroll_selector, min(max(0.0, desired_top), max_scroll))
    page.wait_for_timeout(700)

    found = page.evaluate(
        """
        ({selector, scrollSelector, targetTop, minWidth, minHeight}) => {
            document.querySelectorAll('[data-capture-page-target]').forEach(el =>
                el.removeAttribute('data-capture-page-target')
            );
            const isBody = scrollSelector === 'body';
            const root = isBody
                ? (document.scrollingElement || document.documentElement)
                : document.querySelector(scrollSelector);
            if (!root) return false;
            const rootRect = isBody ? {top: 0} : root.getBoundingClientRect();
            const scrollTop = isBody ? window.scrollY : root.scrollTop;

            const candidates = [...document.querySelectorAll(selector)]
                .map(el => {
                    const r = el.getBoundingClientRect();
                    return {
                        el,
                        width: r.width,
                        height: r.height,
                        absoluteTop: r.top - rootRect.top + scrollTop,
                    };
                })
                .filter(item => item.width >= minWidth && item.height >= minHeight)
                .sort((a, b) => Math.abs(a.absoluteTop - targetTop) - Math.abs(b.absoluteTop - targetTop));

            if (!candidates.length) return false;
            const best = candidates[0];
            const tolerance = Math.max(8, best.height * 0.08);
            if (Math.abs(best.absoluteTop - targetTop) > tolerance) return false;
            best.el.setAttribute('data-capture-page-target', '1');
            return true;
        }
        """,
        {
            "selector": selector,
            "scrollSelector": scroll_selector,
            "targetTop": geometry["top"],
            "minWidth": min_width,
            "minHeight": min_height,
        },
    )
    if not found:
        raise RuntimeError("Trang không còn được render tại vị trí đã nhận diện")

    page.locator('[data-capture-page-target="1"]').screenshot(
        path=str(file_path),
        type="png",
    )
