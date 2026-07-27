"""Text extraction for city agenda/packet PDFs.

Bloomington's OnBoard PDFs are digitally generated (not scanned), so plain
text extraction is reliable; no OCR path. Kept as its own module so the
agenda parser stays pure-text and other adapters (county SharePoint PDFs)
can reuse it.
"""
from pathlib import Path

import pdfplumber


def extract_text(pdf_path: Path) -> str:
    """Return the PDF's text, pages joined by newlines, line structure kept."""
    if not Path(pdf_path).exists():
        raise FileNotFoundError(pdf_path)
    pages = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            pages.append(page.extract_text() or "")
    return "\n".join(pages)
