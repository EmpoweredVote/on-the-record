# tests/test_gui_visual_foundation.py
from pathlib import Path


def test_style_defines_design_tokens():
    css = Path("gui/static/style.css").read_text()
    assert ":root" in css
    for token in (
        "--c-pass-bg", "--c-review-bg", "--c-fail-bg", "--c-info-bg",
        "--c-ink", "--c-surface", "--c-line", "--c-link",
        "--sp-3", "--r-md", "--fs-sm", "--font-mono",
    ):
        assert token in css, f"missing token {token}"


def test_semantic_color_rules_reference_tokens():
    css = Path("gui/static/style.css").read_text()
    # The repeated gate/live palette must now come from tokens, not raw hex.
    assert ".gate-pass" in css
    gate_pass = css.split(".gate-pass")[1].split("}")[0]
    assert "var(--c-pass-bg)" in gate_pass and "var(--c-pass-fg)" in gate_pass
