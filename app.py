import os
import re
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

from flask import Flask, jsonify, render_template, request

from providers import capture_google_docs, capture_scribd, capture_studocu
from providers.common import CaptureRuntime


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
    "scroll_pause": 0.6,
    "max_scrolls": 120,
}

PROVIDER_PIPELINES = {
    "google_docs": capture_google_docs,
    "scribd": capture_scribd,
    "studocu": capture_studocu,
}

JOBS: dict[str, dict] = {}
JOBS_LOCK = threading.Lock()


def new_job(
    doc_id: Optional[str] = None,
    docs_url: str = "",
    provider: Optional[str] = None,
) -> str:
    """Tạo job mới để frontend theo dõi tiến trình."""
    job_id = uuid.uuid4().hex[:12]
    with JOBS_LOCK:
        JOBS[job_id] = {
            "job_id": job_id,
            "status": "queued",
            "step": "Đang chờ xử lý",
            "progress": [],
            "docs_url": docs_url,
            "doc_id": doc_id,
            "provider": provider,
            "output_dir": None,
            "files": [],
            "pages_captured": 0,
            "success": False,
            "error": None,
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
    return job_id


def update_job(
    job_id: str,
    message: str,
    *,
    status: Optional[str] = None,
    error: Optional[str] = None,
    **extra,
) -> None:
    """Cập nhật trạng thái và log tiến trình của job."""
    with JOBS_LOCK:
        job = JOBS[job_id]
        if status:
            job["status"] = status
        if error is not None:
            job["error"] = error
        job["step"] = message
        job["progress"].append(f"[{datetime.now().strftime('%H:%M:%S')}] {message}")
        job.update(extra)


def finish_job(job_id: str, success: bool, message: str, **extra) -> None:
    """Đánh dấu job đã hoàn tất hoặc thất bại."""
    with JOBS_LOCK:
        job = JOBS[job_id]
        job["success"] = success
        job["status"] = "done" if success else "failed"
        job["step"] = message
        job["progress"].append(f"[{datetime.now().strftime('%H:%M:%S')}] {message}")
        job.update(extra)


def extract_document_info(url: str) -> dict:
    """Nhận diện Google Docs, Scribd hoặc Studocu và lấy document ID."""
    if not url or not url.strip():
        return {
            "ok": False,
            "provider": None,
            "doc_id": None,
            "url": None,
            "error": "URL không được để trống.",
        }

    url = url.strip()
    try:
        parsed = urlparse(url)
    except ValueError:
        parsed = None

    if not parsed or parsed.scheme not in {"http", "https"} or not parsed.hostname:
        return {
            "ok": False,
            "provider": None,
            "doc_id": None,
            "url": None,
            "error": "URL không hợp lệ. URL phải bắt đầu bằng http:// hoặc https://.",
        }

    hostname = parsed.hostname.lower().rstrip(".")

    if hostname == "docs.google.com":
        match = re.match(r"^/document/d/([a-zA-Z0-9_-]+)(?:/|$)", parsed.path)
        if match:
            doc_id = match.group(1)
            return {
                "ok": True,
                "provider": "google_docs",
                "doc_id": doc_id,
                "url": f"https://docs.google.com/document/d/{doc_id}/edit",
                "error": None,
            }

    if hostname == "scribd.com" or hostname.endswith(".scribd.com"):
        match = re.match(r"^/(?:document|doc)/(\d+)(?:/|$)", parsed.path)
        if match:
            return {
                "ok": True,
                "provider": "scribd",
                "doc_id": match.group(1),
                "url": url,
                "error": None,
            }

    if hostname == "studocu.com" or hostname.endswith(".studocu.com"):
        path_parts = [part for part in parsed.path.split("/") if part]
        numeric_parts = [part for part in path_parts if part.isdigit()]
        if "document" in path_parts and numeric_parts:
            return {
                "ok": True,
                "provider": "studocu",
                "doc_id": numeric_parts[-1],
                "url": url,
                "error": None,
            }

    return {
        "ok": False,
        "provider": None,
        "doc_id": None,
        "url": None,
        "error": (
            "Chỉ hỗ trợ URL Google Docs, Scribd hoặc Studocu. Ví dụ: "
            "https://docs.google.com/document/d/<id>/edit, "
            "https://www.scribd.com/document/<id>/<slug>, hoặc "
            "https://www.studocu.com/<locale>/document/.../<id>"
        ),
    }


def extract_doc_id(url: str) -> dict:
    """Giữ tương thích với mã cũ dùng tên hàm extract_doc_id."""
    return extract_document_info(url)


def make_output_dir(doc_id: str, provider: Optional[str] = None) -> Path:
    """Tạo thư mục output riêng theo provider và lần chạy."""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    prefix = f"{provider}_" if provider else ""
    output_dir = OUTPUTS_DIR / f"{prefix}{doc_id}_{timestamp}"
    output_dir.mkdir(parents=True, exist_ok=False)
    return output_dir


@app.route("/", methods=["GET"])
def index():
    return render_template("index.html")


@app.route("/start_capture", methods=["POST"])
def start_capture():
    """Nhận URL, chọn đúng pipeline và chạy trong background thread."""
    docs_url = request.form.get("docs_url", "").strip()
    validation = extract_document_info(docs_url)
    if not validation["ok"]:
        return jsonify({"ok": False, "error": validation["error"]}), 400

    doc_id = validation["doc_id"]
    provider = validation["provider"]
    target_url = validation["url"]
    output_dir = make_output_dir(doc_id, provider)
    job_id = new_job(doc_id=doc_id, docs_url=docs_url, provider=provider)
    update_job(job_id, "Đã tạo job mới", status="queued", output_dir=str(output_dir))

    runtime = CaptureRuntime(
        config=CONFIG,
        update_job=update_job,
        finish_job=finish_job,
    )
    thread = threading.Thread(
        target=PROVIDER_PIPELINES[provider],
        args=(job_id, doc_id, target_url, output_dir, runtime),
        daemon=True,
    )
    thread.start()
    return jsonify({"ok": True, "job_id": job_id})


@app.route("/status/<job_id>", methods=["GET"])
def status(job_id):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if not job:
            return jsonify({"ok": False, "error": "Không tìm thấy job."}), 404
        return jsonify({"ok": True, "job": job})


if __name__ == "__main__":
    OUTPUTS_DIR.mkdir(exist_ok=True)
    print("=" * 50)
    print("  Document Page Screenshot App")
    print("  http://127.0.0.1:5000")
    print("=" * 50)
    app.run(
        debug=False,
        use_reloader=False,
        host="127.0.0.1",
        port=5000,
        threaded=True,
    )
