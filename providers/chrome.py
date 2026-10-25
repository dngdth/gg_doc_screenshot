"""Open installed Chrome normally; receive screenshots from its activeTab extension."""

import hmac
import os
import secrets
import shutil
import subprocess
import threading
from dataclasses import dataclass, field
from io import BytesIO
from pathlib import Path
from time import monotonic

from flask import Blueprint, abort, jsonify, render_template, request
from PIL import Image, UnidentifiedImageError

from .common import CaptureRuntime


chrome_capture_blueprint = Blueprint("chrome_capture", __name__)
BRIDGE_LOCK = threading.RLock()
BRIDGE_JOBS: dict[str, "ChromeJob"] = {}
MAX_PNG_BYTES = 24 * 1024 * 1024


@dataclass
class ChromeJob:
    job_id: str
    target_url: str
    output_dir: Path
    runtime: CaptureRuntime
    token: str = field(default_factory=lambda: secrets.token_urlsafe(32))
    total: int = 0
    files: dict[int, str] = field(default_factory=dict)
    deadline: float = 0
    connected: bool = False
    started: bool = False
    done: bool = False

    def touch(self):
        self.deadline = monotonic() + float(self.runtime.config.get("chrome_capture_timeout", 600))


def find_chrome(config) -> Path:
    configured = config.get("chrome_executable") or os.environ.get("CHROME_PATH")
    if configured:
        executable = Path(configured)
        if executable.is_file():
            return executable
        raise FileNotFoundError(f"Không tìm thấy Chrome tại: {executable}")
    candidates = [shutil.which("chrome"), shutil.which("google-chrome")]
    for env in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
        if os.environ.get(env):
            candidates.append(str(Path(os.environ[env]) / "Google/Chrome/Application/chrome.exe"))
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return Path(candidate)
    raise FileNotFoundError("Không tìm thấy Google Chrome. Đặt CONFIG['chrome_executable'] tới chrome.exe.")


def _finish(job: ChromeJob, success: bool, error: str | None = None):
    job.done = True
    files = [job.files[number] for number in sorted(job.files)]
    job.runtime.finish_job(
        job.job_id, success, f"Đã chụp {len(files)}/{job.total} trang Studocu bằng Chrome",
        error=error, files=files, pages_captured=len(files), output_dir=str(job.output_dir),
    )


def _expire(job_id: str, token: str):
    with BRIDGE_LOCK:
        job = BRIDGE_JOBS.get(job_id)
        if not job or not hmac.compare_digest(job.token, token):
            return
        remaining = job.deadline - monotonic()
        if not job.done and remaining > 0:
            timer = threading.Timer(remaining, _expire, args=(job_id, token))
            timer.daemon = True
            timer.start()
            return
        if not job.done:
            _finish(job, False, "Hết thời gian chờ tiện ích Chrome. Cài tiện ích, mở tài liệu rồi bấm biểu tượng tiện ích để chụp.")
        BRIDGE_JOBS.pop(job_id, None)


def capture_studocu_in_chrome(job_id: str, doc_id: str, target_url: str, output_dir: Path, runtime: CaptureRuntime):
    del doc_id
    try:
        chrome = find_chrome(runtime.config)
        job = ChromeJob(job_id, target_url, output_dir, runtime)
        job.touch()
        with BRIDGE_LOCK:
            BRIDGE_JOBS[job_id] = job
        # A local handoff page passes the job to the extension. The token stays in
        # the URL fragment, never in the Studocu URL, process logs, or HTTP logs.
        bridge_url = f"http://127.0.0.1:5000/chrome_capture/{job_id}#{job.token}"
        runtime.update_job(
            job_id, "Đang mở Chrome thường. Tiện ích sẽ mở tài liệu; bấm biểu tượng Document Page Screenshot để bắt đầu chụp.",
            status="waiting_chrome", browser="chrome", output_dir=str(output_dir),
        )
        subprocess.Popen([str(chrome), "--new-tab", bridge_url], shell=False)
        timer = threading.Timer(float(runtime.config.get("chrome_capture_timeout", 600)), _expire, args=(job_id, job.token))
        timer.daemon = True
        timer.start()
    except Exception as error:
        with BRIDGE_LOCK:
            BRIDGE_JOBS.pop(job_id, None)
        runtime.finish_job(job_id, False, "Không mở được Chrome", error=str(error), output_dir=str(output_dir))


def _authorized(job_id: str) -> ChromeJob:
    job = BRIDGE_JOBS.get(job_id)
    bearer = request.headers.get("Authorization", "")
    if not job or not hmac.compare_digest(bearer, f"Bearer {job.token}"):
        abort(403)
    if job.done or monotonic() >= job.deadline:
        abort(409, description="Job đã kết thúc hoặc hết hạn.")
    job.touch()
    return job


@chrome_capture_blueprint.get("/chrome_capture/<job_id>")
def handoff(job_id):
    with BRIDGE_LOCK:
        if job_id not in BRIDGE_JOBS:
            abort(404)
    response = render_template("chrome_capture.html", job_id=job_id, extension_dir=str(Path(__file__).resolve().parent.parent / "chrome_extension"))
    return response, 200, {"Cache-Control": "no-store", "X-Frame-Options": "DENY", "Referrer-Policy": "no-referrer"}


@chrome_capture_blueprint.get("/chrome_capture/<job_id>/info")
def chrome_info(job_id):
    with BRIDGE_LOCK:
        job = _authorized(job_id)
        if not job.connected:
            job.connected = True
            job.runtime.update_job(job_id, "Tiện ích đã kết nối. Mở tài liệu trong Chrome rồi bấm biểu tượng tiện ích để chụp.", status="waiting_chrome")
        return jsonify(ok=True, target_url=job.target_url)


@chrome_capture_blueprint.post("/chrome_capture/<job_id>/event")
def chrome_event(job_id):
    with BRIDGE_LOCK:
        job = _authorized(job_id)
        data = request.get_json(silent=True) or {}
        if not isinstance(data, dict):
            abort(400)
        event = data.get("event")
        if event == "start":
            total = data.get("total")
            if type(total) is not int or not 1 <= total <= 1000 or job.started:
                abort(400)
            job.total, job.started = total, True
            job.runtime.update_job(job_id, f"Chrome đã nhận diện {total} khung; đang cuộn và chụp", status="running")
        elif event in {"progress", "wait"}:
            message = data.get("message")
            if not isinstance(message, str) or len(message) > 500:
                abort(400)
            job.runtime.update_job(job_id, message, status="waiting_chrome" if event == "wait" else "running")
        elif event == "finish":
            if not job.started:
                abort(400)
            complete = len(job.files) == job.total
            _finish(job, complete, None if complete else "Một số khung trang chưa được lưu.")
        elif event == "error":
            message = str(data.get("message", "Lỗi tiện ích Chrome"))[:1000]
            _finish(job, False, message)
        else:
            abort(400)
        return jsonify(ok=True)


@chrome_capture_blueprint.post("/chrome_capture/<job_id>/page/<int:number>")
def chrome_page(job_id, number):
    with BRIDGE_LOCK:
        job = _authorized(job_id)
        if not job.started or not 1 <= number <= job.total:
            abort(400)
        if request.content_length and request.content_length > MAX_PNG_BYTES:
            abort(413)
        png = request.stream.read(MAX_PNG_BYTES + 1)
        if len(png) > MAX_PNG_BYTES:
            abort(413)
        try:
            with Image.open(BytesIO(png)) as image:
                if image.format != "PNG" or image.width * image.height > 32_000_000:
                    abort(400)
                image.verify()
        except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
            abort(400)
        filename = f"page_{number:03d}.png"
        (job.output_dir / filename).write_bytes(png)
        job.files[number] = filename
        files = [job.files[n] for n in sorted(job.files)]
        job.runtime.update_job(job_id, f"Đã chụp Chrome trang {number}/{job.total}: {filename}", status="running", files=files, pages_captured=len(files))
        return jsonify(ok=True)
