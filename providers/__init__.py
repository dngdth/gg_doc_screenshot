"""Các pipeline chụp tài liệu, tách riêng theo từng nhà cung cấp."""

from .google_docs import capture_google_docs
from .scribd import capture_scribd
from .studocu import capture_studocu

__all__ = ["capture_google_docs", "capture_scribd", "capture_studocu"]
