from pathlib import Path

from src.pdf_text import extract_text

FIX = Path(__file__).parent / "fixtures" / "onboard"


def test_extracts_agenda_text_with_line_structure():
    text = extract_text(FIX / "agenda_2026-07-29.pdf")
    # Section headers from the stable 10-section template survive extraction.
    assert "Legislation for Second Readings and Resolutions" in text
    assert "Adjournment" in text
    # Line breaks are preserved (the section parser is line-oriented).
    assert text.count("\n") > 10


def test_missing_file_raises():
    import pytest
    with pytest.raises(FileNotFoundError):
        extract_text(FIX / "nope.pdf")
