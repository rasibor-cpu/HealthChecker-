"""Synthetic, non-PHI fixtures for the HC-360 OCR/import qualification suites."""
from __future__ import annotations

import pymupdf


def png_lines(lines: list[str], size: int = 34) -> bytes:
    doc = pymupdf.open()
    page = doc.new_page(width=900, height=120 + 70 * len(lines))
    for i, text in enumerate(lines):
        page.insert_text((40, 90 + 70 * i), text, fontsize=size)
    return page.get_pixmap(dpi=110).tobytes("png")


def blank_png() -> bytes:
    doc = pymupdf.open()
    return doc.new_page(width=600, height=300).get_pixmap(dpi=96).tobytes("png")


def text_pdf(lines: list[str]) -> bytes:
    doc = pymupdf.open()
    page = doc.new_page()
    for i, text in enumerate(lines):
        page.insert_text((72, 100 + 30 * i), text, fontsize=14)
    return doc.tobytes()


def scanned_pdf(pages: list[list[str]]) -> bytes:
    """Image-only PDF (no embedded text) built from rendered line images."""
    doc = pymupdf.open()
    for lines in pages:
        page = doc.new_page()
        page.insert_image(page.rect, stream=png_lines(lines))
    return doc.tobytes()


def blank_pdf(page_count: int) -> bytes:
    doc = pymupdf.open()
    for i in range(page_count):
        doc.new_page().insert_text((72, 100), f"p{i}", fontsize=14)
    return doc.tobytes()
