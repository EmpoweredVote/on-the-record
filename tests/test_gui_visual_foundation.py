# tests/test_gui_visual_foundation.py
from pathlib import Path

from fastapi.testclient import TestClient

from gui.app import create_app


def test_pages_have_a_single_stylesheet_link(tagged_meeting_dir, tmp_meetings_dir):
    tagged_meeting_dir("x", meeting_id="2026-02-04-council", completed_stage=4)
    client = TestClient(create_app())
    for path in ("/", "/meetings/2026-02-04-council", "/new", "/discovery"):
        body = client.get(path).text
        assert body.count('rel="stylesheet"') == 1, f"{path} should link the stylesheet once"
        assert body.count("<!doctype html>") == 1


def test_base_template_exists_and_is_extended():
    assert Path("gui/templates/base.html").exists()
    for page in ("library.html", "workspace.html", "new_meeting.html",
                 "dedup_confirm.html", "discovery.html"):
        text = Path(f"gui/templates/{page}").read_text()
        assert 'extends "base.html"' in text, f"{page} must extend base.html"


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


def test_style_has_no_stray_semantic_hex_after_refresh():
    import re
    from pathlib import Path
    css = Path("gui/static/style.css").read_text()
    root = css.split("}", 1)[0]          # the :root block (first block in the file)
    body = css[len(root):]               # everything after it = rule bodies
    for name in ("--c-pass-bg", "--c-review-bg", "--c-fail-bg", "--c-info-bg"):
        m = re.search(re.escape(name) + r"\s*:\s*(#[0-9a-fA-F]{3,8})", root)
        assert m, f"{name} not defined in :root"
        hexval = m.group(1)
        assert hexval not in body, f"{hexval} ({name}) should be tokenized, not repeated in a rule body"


def test_ui_macro_file_defines_badge():
    from pathlib import Path
    text = Path("gui/templates/_ui.html").read_text()
    assert "macro badge" in text


def test_library_and_workspace_import_the_badge_macro():
    from pathlib import Path
    lib = Path("gui/templates/library.html").read_text()
    ws = Path("gui/templates/workspace.html").read_text()
    assert 'import badge' in lib and 'import badge' in ws
