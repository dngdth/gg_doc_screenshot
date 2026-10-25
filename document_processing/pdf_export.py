import math
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from PIL import Image, ImageOps
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen.canvas import Canvas


@dataclass(frozen=True)
class PdfResult:
    path: Path
    pages: int
    size_bytes: int


def _page_order(path: Path):
    return tuple((1, int(part)) if part.isdigit() else (0, part.casefold())
                 for part in re.split(r"(\d+)", path.name))


def images_to_pdf(image_paths: Sequence[Path], destination: Path, *, dpi: float = 150) -> PdfResult:
    """Embed screenshots losslessly, one image per PDF page, without loading all images at once."""
    paths = sorted((Path(path) for path in image_paths), key=_page_order)
    if not paths:
        raise ValueError("Không có ảnh để tạo PDF.")
    if not math.isfinite(dpi) or dpi <= 0:
        raise ValueError("DPI phải là số dương.")
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=".pdf-", suffix=".tmp", dir=destination.parent)
    os.close(fd)
    temporary = Path(temporary_name)
    try:
        canvas = Canvas(str(temporary), pageCompression=1)
        canvas.setTitle(destination.stem)
        canvas.setCreator("Document Page Screenshot")
        for path in paths:
            with Image.open(path) as source, ImageOps.exif_transpose(source) as image:
                if image.mode == "RGBA" or "transparency" in image.info or image.mode == "LA":
                    with image.convert("RGBA") as rgba:
                        rgb = Image.new("RGB", rgba.size, "white")
                        rgb.paste(rgba, mask=rgba.getchannel("A"))
                else:
                    rgb = image.convert("RGB")
                try:
                    width, height = rgb.width * 72 / dpi, rgb.height * 72 / dpi
                    canvas.setPageSize((width, height))
                    canvas.drawImage(ImageReader(rgb), 0, 0, width=width, height=height)
                    canvas.showPage()
                finally:
                    rgb.close()
        canvas.save()
        # Only publish a complete PDF; a missing/corrupt PNG cannot leave a partial download.
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)
    return PdfResult(destination, len(paths), destination.stat().st_size)


def finish_capture_as_pdf(job_id: str, doc_id: str, provider: str, output_dir: Path, files: Sequence[str], runtime) -> None:
    """Used by Docs and Scribd: build the PDF before reporting a successful job."""
    runtime.update_job(job_id, f"Đang ghép {len(files)} trang thành PDF…", status="building_pdf")
    try:
        filename = f"{provider}_{doc_id}.pdf"
        result = images_to_pdf([output_dir / name for name in files], output_dir / filename)
    except Exception as error:
        runtime.finish_job(
            job_id, False, "Không tạo được file PDF", error=str(error),
            files=list(files), pages_captured=len(files), output_dir=str(output_dir),
        )
        return
    runtime.finish_job(
        job_id, True, f"Hoàn tất: PDF {result.pages} trang đã sẵn sàng tải xuống",
        files=list(files), pages_captured=len(files), output_dir=str(output_dir),
        pdf_file=result.path.name, pdf_pages=result.pages, pdf_size=result.size_bytes,
    )
