from pathlib import Path

import pytest

from bench.score import calculate_der


def _write_rttm(path: Path, turns):
    path.write_text(
        "".join(
            f"SPEAKER meeting 1 {start:.3f} {end - start:.3f} "
            f"<NA> <NA> {speaker} <NA> <NA>\n"
            for start, end, speaker in turns
        )
    )


def _write_uem(path: Path, start=0.0, end=10.0):
    path.write_text(f"meeting 1 {start:.3f} {end:.3f}\n")


def test_calculate_der_perfect_match(tmp_path):
    reference = tmp_path / "reference.rttm"
    hypothesis = tmp_path / "hypothesis.rttm"
    uem = tmp_path / "region.uem"
    turns = [(0, 5, "A"), (5, 10, "B")]
    _write_rttm(reference, turns)
    _write_rttm(hypothesis, [(0, 5, "X"), (5, 10, "Y")])
    _write_uem(uem)

    result = calculate_der(reference, hypothesis, uem, collar=0.0)

    assert result["der"] == pytest.approx(0.0)
    assert result["confusion"] == pytest.approx(0.0)
    assert result["missed_detection"] == pytest.approx(0.0)
    assert result["false_alarm"] == pytest.approx(0.0)


def test_calculate_der_reports_confusion(tmp_path):
    reference = tmp_path / "reference.rttm"
    hypothesis = tmp_path / "hypothesis.rttm"
    uem = tmp_path / "region.uem"
    _write_rttm(reference, [(0, 5, "A"), (5, 10, "B")])
    _write_rttm(hypothesis, [(0, 10, "X")])
    _write_uem(uem)

    result = calculate_der(reference, hypothesis, uem, collar=0.0)

    assert result["der"] == pytest.approx(0.5)
    assert result["confusion"] == pytest.approx(0.5)


def test_calculate_der_reports_missed_speech(tmp_path):
    reference = tmp_path / "reference.rttm"
    hypothesis = tmp_path / "hypothesis.rttm"
    uem = tmp_path / "region.uem"
    _write_rttm(reference, [(0, 10, "A")])
    _write_rttm(hypothesis, [(0, 5, "X")])
    _write_uem(uem)

    result = calculate_der(reference, hypothesis, uem, collar=0.0)

    assert result["der"] == pytest.approx(0.5)
    assert result["missed_detection"] == pytest.approx(0.5)


def test_calculate_der_reports_false_alarm(tmp_path):
    reference = tmp_path / "reference.rttm"
    hypothesis = tmp_path / "hypothesis.rttm"
    uem = tmp_path / "region.uem"
    _write_rttm(reference, [(0, 5, "A")])
    _write_rttm(hypothesis, [(0, 10, "X")])
    _write_uem(uem)

    result = calculate_der(reference, hypothesis, uem, collar=0.0)

    assert result["der"] == pytest.approx(1.0)
    assert result["false_alarm"] == pytest.approx(1.0)
