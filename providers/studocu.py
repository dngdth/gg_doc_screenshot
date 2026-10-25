"""Studocu capture using the installed Chrome app and its active-tab extension."""

from pathlib import Path

from .chrome import capture_studocu_in_chrome
from .common import CaptureRuntime


def capture_studocu(
    job_id: str,
    doc_id: str,
    target_url: str,
    output_dir: Path,
    runtime: CaptureRuntime,
) -> None:
    capture_studocu_in_chrome(job_id, doc_id, target_url, output_dir, runtime)
