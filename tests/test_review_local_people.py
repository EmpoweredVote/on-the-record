import pytest

from src.models import SpeakerMapping
from src.review import LOCAL_SLUG_RE, default_local_slug


def test_default_local_slug_kebab_cases_the_name():
    assert default_local_slug("Susan Brackney", "SPEAKER_04") == "susan-brackney"


def test_default_local_slug_falls_back_to_the_label():
    assert default_local_slug(None, "SPEAKER_04") == "speaker-04"
    assert default_local_slug("   ", "SPEAKER_04") == "speaker-04"


def test_default_local_slug_output_is_always_valid():
    for name, label in [("Susan Brackney", "S0"), ("!!!", "S0"), ("!!!", "!!!"),
                        ("O'Brien-Smith, Jr.", "S1"), ("x" * 300, "S2")]:
        slug = default_local_slug(name, label)
        assert LOCAL_SLUG_RE.match(slug), f"({name!r}, {label!r}) produced {slug!r}"


def test_default_local_slug_is_bounded_at_one_hundred():
    assert len(default_local_slug("x" * 300, "S2")) == 100
