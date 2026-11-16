# -*- coding: utf-8 -*-
"""Rasterize proposal plan PDFs into PNG pages for vision models."""
from __future__ import annotations

import base64
import logging
import os
import shutil
import subprocess
import tempfile

from .binary_utils import binary_to_b64

_logger = logging.getLogger(__name__)

DEFAULT_MAX_PLAN_PAGES = 8
DEFAULT_MAX_PHOTOS = 2
# Keep under typical multimodal payload limits while fitting more plan pages.
DEFAULT_MAX_CHARS = 900_000
DEFAULT_PDF_MATRIX = 1.15
DEFAULT_JPEG_QUALITY = 65


def _chrome_bin():
    return (
        os.environ.get("CHROME_BIN")
        or "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    )


def _pdftoppm_bin():
    return (
        os.environ.get("PDFTOPPM_BIN")
        or shutil.which("pdftoppm")
        or "/opt/homebrew/bin/pdftoppm"
        or "/usr/local/bin/pdftoppm"
    )


def _render_with_pdftoppm(pdf_path, tmp, max_pages):
    bin_path = _pdftoppm_bin()
    if not bin_path or not os.path.exists(bin_path):
        return []
    prefix = os.path.join(tmp, "page")
    try:
        subprocess.run(
            [bin_path, "-png", "-r", "120", "-f", "1", "-l", str(max_pages), pdf_path, prefix],
            capture_output=True,
            timeout=90,
            check=False,
        )
    except Exception as exc:
        _logger.warning("pdftoppm plan render failed: %s", exc)
        return []
    pages = []
    for i in range(1, max_pages + 1):
        path = f"{prefix}-{i}.png"
        if not os.path.isfile(path):
            # Some builds use page-01.png
            path = f"{prefix}-{i:02d}.png"
        if not os.path.isfile(path):
            break
        with open(path, "rb") as fh:
            pages.append(fh.read())
    if pages:
        _logger.info("plan_render pdftoppm ok (%s pages)", len(pages))
    return pages


def _select_plan_page_indices(n_pages, max_pages):
    """Pick the most useful plan pages — not just the cover/TOC prefix.

    Construction sets put schedules, framing, and details near the end. Always
    keep the last few pages and spread the rest across the middle.
    """
    if n_pages <= 0:
        return []
    max_pages = max(1, int(max_pages))
    if n_pages <= max_pages:
        return list(range(n_pages))

    # Prefer last 3 (S-sheets / schedules / details) + cover/title + early plans.
    last_count = min(3, n_pages, max_pages)
    chosen = set(range(n_pages - last_count, n_pages))
    # Always keep title/cover + first floor plan when possible.
    for i in (0, 1, 2):
        if i < n_pages - last_count and len(chosen) < max_pages:
            chosen.add(i)
    remain = max_pages - len(chosen)
    front_pool = [i for i in range(0, n_pages - last_count) if i not in chosen]
    if remain > 0 and front_pool:
        if remain >= len(front_pool):
            chosen.update(front_pool)
        else:
            step = len(front_pool) / float(remain)
            for i in range(remain):
                chosen.add(front_pool[min(int(i * step), len(front_pool) - 1)])
    # Fill from the start if still short.
    for i in range(n_pages):
        if len(chosen) >= max_pages:
            break
        chosen.add(i)
    return sorted(chosen)[:max_pages]


def _pixmap_to_jpeg_bytes(pix, quality=DEFAULT_JPEG_QUALITY):
    """Prefer JPEG so dense plan pages stay under multimodal char limits."""
    try:
        return pix.tobytes("jpeg", jpg_quality=int(quality))
    except Exception:
        return pix.tobytes("png")


def _png_bytes_to_jpeg(png_bytes, quality=DEFAULT_JPEG_QUALITY):
    try:
        from PIL import Image
        import io

        im = Image.open(io.BytesIO(png_bytes)).convert("RGB")
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=int(quality), optimize=True)
        return buf.getvalue()
    except Exception:
        return png_bytes


def _render_with_pymupdf(pdf_bytes, max_pages):
    try:
        import fitz  # PyMuPDF
    except ImportError:
        return []
    pages = []
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception as exc:
        _logger.warning("pymupdf open failed: %s", exc)
        return []
    try:
        indices = _select_plan_page_indices(len(doc), max_pages)
        matrix = fitz.Matrix(DEFAULT_PDF_MATRIX, DEFAULT_PDF_MATRIX)
        for i in indices:
            page = doc.load_page(i)
            pix = page.get_pixmap(matrix=matrix, alpha=False)
            pages.append(_pixmap_to_jpeg_bytes(pix))
        if pages:
            _logger.info(
                "plan_render pymupdf ok (%s pages, indices=%s, jpeg)",
                len(pages),
                indices,
            )
    except Exception as exc:
        _logger.warning("pymupdf render failed: %s", exc)
        return []
    finally:
        doc.close()
    return pages


def _render_with_qlmanage(pdf_path, tmp):
    """macOS Quick Look — first page thumbnail only."""
    try:
        subprocess.run(
            ["qlmanage", "-t", "-s", "1400", "-o", tmp, pdf_path],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        thumb = os.path.join(tmp, "plan.pdf.png")
        if os.path.isfile(thumb):
            with open(thumb, "rb") as fh:
                data = fh.read()
            _logger.info("plan_render qlmanage ok (1 page, %s bytes)", len(data))
            return [data]
    except Exception as exc:
        _logger.warning("qlmanage plan render failed: %s", exc)
    return []


def _render_with_chrome(pdf_path, tmp):
    chrome = _chrome_bin()
    if not os.path.exists(chrome):
        return []
    try:
        png_path = os.path.join(tmp, "page.png")
        subprocess.run(
            [
                chrome,
                "--headless",
                "--disable-gpu",
                "--hide-scrollbars",
                f"--screenshot={png_path}",
                f"file://{pdf_path}",
            ],
            capture_output=True,
            timeout=45,
            check=False,
        )
        if os.path.isfile(png_path):
            with open(png_path, "rb") as fh:
                return [fh.read()]
    except Exception as exc:
        _logger.warning("Chrome plan screenshot failed: %s", exc)
    return []


def render_pdf_bytes_to_pngs(pdf_bytes, max_pages=DEFAULT_MAX_PLAN_PAGES):
    """Return list of PNG bytes for up to max_pages of a PDF (prefer multi-page tools)."""
    if not pdf_bytes:
        return []
    max_pages = max(1, int(max_pages or DEFAULT_MAX_PLAN_PAGES))

    pages = _render_with_pymupdf(pdf_bytes, max_pages)
    if len(pages) >= min(2, max_pages) or (pages and max_pages == 1):
        return pages[:max_pages]

    with tempfile.TemporaryDirectory() as tmp:
        pdf_path = os.path.join(tmp, "plan.pdf")
        with open(pdf_path, "wb") as fh:
            fh.write(pdf_bytes)

        ppm = _render_with_pdftoppm(pdf_path, tmp, max_pages)
        if len(ppm) > len(pages):
            pages = ppm
        if len(pages) >= min(2, max_pages):
            return pages[:max_pages]

        if not pages:
            pages = _render_with_qlmanage(pdf_path, tmp)
        if not pages:
            pages = _render_with_chrome(pdf_path, tmp)
        if pages and len(pages) < 2:
            _logger.info(
                "plan_render only %s page(s); install poppler (pdftoppm) or pymupdf for multi-page",
                len(pages),
            )
    return pages[:max_pages]


def asset_datas_to_bytes(datas):
    """Odoo Binary may be bytes or base64 str."""
    if not datas:
        return b""
    if isinstance(datas, bytes):
        # Often already base64-encoded bytes from the ORM.
        try:
            return base64.b64decode(datas)
        except Exception:
            return datas
    raw = binary_to_b64(datas)
    try:
        return base64.b64decode(raw)
    except Exception:
        return b""


def vision_data_urls_for_proposal(
    proposal,
    max_photos=DEFAULT_MAX_PHOTOS,
    max_plan_pages=DEFAULT_MAX_PLAN_PAGES,
    max_chars=DEFAULT_MAX_CHARS,
    max_images=8,
):
    """Build multimodal image data URLs from plan PDF pages (priority) + before photos."""
    plan_urls = []
    photo_urls = []

    plans = proposal.asset_ids.filtered(
        lambda a: (a.slot or "") == "plan"
        or (a.mime or "").startswith("application/pdf")
        or (a.filename or "").lower().endswith(".pdf")
    )
    for asset in plans[:1]:
        pdf_bytes = asset_datas_to_bytes(asset.datas)
        for raw_img in render_pdf_bytes_to_pngs(pdf_bytes, max_pages=max_plan_pages):
            # Normalize to JPEG when possible (pdftoppm/qlmanage may still return PNG).
            if raw_img[:3] == b"\xff\xd8\xff":
                img_bytes, mime = raw_img, "image/jpeg"
            else:
                jpg = _png_bytes_to_jpeg(raw_img)
                if jpg[:3] == b"\xff\xd8\xff":
                    img_bytes, mime = jpg, "image/jpeg"
                else:
                    img_bytes, mime = raw_img, "image/png"
            b64 = base64.b64encode(img_bytes).decode("ascii")
            if len(b64) > max_chars:
                _logger.warning(
                    "plan page skipped: b64 len %s > max_chars %s", len(b64), max_chars
                )
                continue
            plan_urls.append(f"data:{mime};base64,{b64}")

    befores = proposal.asset_ids.filtered(
        lambda a: (a.slot or "") == "before" and a.datas and not (a.mime or "").startswith("application/pdf")
    )
    for asset in befores[:max_photos]:
        mime = asset.mime or "image/jpeg"
        if mime.startswith("application/"):
            continue
        raw = binary_to_b64(asset.datas)
        if not raw or len(raw) > max_chars:
            continue
        photo_urls.append(f"data:{mime};base64,{raw}")

    if not photo_urls and proposal.before_image:
        raw = binary_to_b64(proposal.before_image)
        if raw and len(raw) <= max_chars:
            photo_urls.append(f"data:image/jpeg;base64,{raw}")

    # Prefer plan pages; fill remaining slots with site photos.
    max_images = max(1, int(max_images or 8))
    urls = list(plan_urls[:max_images])
    remain = max_images - len(urls)
    if remain > 0:
        urls.extend(photo_urls[:remain])
    return urls
