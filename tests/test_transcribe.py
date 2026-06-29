from src.models import Segment
from src.transcribe import remove_segment_overlaps
from src.transcribe import transcribe_full_audio


def test_remove_segment_overlaps_trims_the_later_speaker():
    segments = [
        Segment(0, 10.0, 15.0, "SPEAKER_00"),
        Segment(1, 14.0, 18.0, "SPEAKER_01"),
        Segment(2, 17.5, 20.0, "SPEAKER_02"),
    ]

    result = remove_segment_overlaps(segments)

    assert [(seg.start_time, seg.end_time) for seg in result] == [
        (10.0, 15.0),
        (15.0, 18.0),
        (18.0, 20.0),
    ]


def test_remove_segment_overlaps_collapses_fully_covered_segment():
    segments = [
        Segment(0, 10.0, 20.0, "SPEAKER_00"),
        Segment(1, 12.0, 14.0, "SPEAKER_01"),
    ]

    result = remove_segment_overlaps(segments)

    assert result[1].start_time == result[1].end_time == 14.0


class _FakeWord:
    def __init__(self, word, start, end):
        self.word, self.start, self.end = word, start, end


class _FakeSeg:
    def __init__(self, words):
        self.words = words
        self.text = " ".join(w.word for w in words)


class _FakeModel:
    def __init__(self, segs):
        self._segs = segs
        self.calls = []

    def transcribe(self, audio, **kwargs):
        self.calls.append(kwargs)
        return iter(self._segs), {}


from src.transcribe import transcribe_and_assign


def test_transcribe_and_assign_attributes_words_to_diarized_turns(monkeypatch, tmp_path):
    import src.transcribe as t
    monkeypatch.setattr(t, "load_wav", lambda p: (b"", 16000))
    model = _FakeModel([
        _FakeSeg([_FakeWord(" abortion", 1028.18, 1028.64),
                  _FakeWord(" tourism", 1028.64, 1029.20),
                  _FakeWord(" where", 1029.20, 1030.20)]),
    ])
    segments = [
        Segment(0, 1026.655, 1029.254, "SPEAKER_00"),
        Segment(1, 1029.322, 1029.777, "SPEAKER_01"),  # short Hailey turn
        Segment(2, 1029.777, 1030.570, "SPEAKER_00"),
    ]

    result = transcribe_and_assign(model, tmp_path / "audio.wav", segments)

    steve_words = [w.word for s in result if s.speaker_label == "SPEAKER_00" for w in s.words]
    hailey_words = [w.word for s in result if s.speaker_label == "SPEAKER_01" for w in s.words]
    assert "abortion" in steve_words and "tourism" in steve_words
    assert hailey_words == []  # short turn captured no genuine word


from src.transcribe import recover_orphan_turns
from src.models import Word
import numpy as np


def test_recover_orphan_turns_fills_isolated_empty_turn(monkeypatch, tmp_path):
    import src.transcribe as t
    monkeypatch.setattr(t, "load_wav", lambda p: (np.zeros(16000 * 40), 16000))
    monkeypatch.setattr(t, "slice_audio", lambda samples, sr, a, b: np.zeros(int((b - a) * sr)))
    model = _FakeModel([_FakeSeg([_FakeWord(" Here.", 0.1, 0.4)])])
    seg = Segment(1, 5.0, 5.5, "MEMBER")  # empty, in a gap
    seg.words = []
    # continuous pass produced NO word overlapping [5.0, 5.5]
    continuous = [Word("Councilmember", 2.0, 2.6)]

    recover_orphan_turns(model, tmp_path / "audio.wav", [seg], continuous)

    assert [w.word for w in seg.words] == ["Here."]
    assert seg.text == "Here."
    # recovered word is rebased onto the turn's global timeline
    assert abs(seg.words[0].start - 5.1) < 1e-6


def test_recover_orphan_turns_skips_turn_overlapping_continuous_speech(monkeypatch, tmp_path):
    import src.transcribe as t
    monkeypatch.setattr(t, "load_wav", lambda p: (np.zeros(16000 * 40), 16000))
    monkeypatch.setattr(t, "slice_audio", lambda samples, sr, a, b: np.zeros(int((b - a) * sr)))
    # Model would (wrongly) return the dominant speaker's bled word if called.
    model = _FakeModel([_FakeSeg([_FakeWord(" abortion", 0.1, 0.4)])])
    seg = Segment(1, 1029.322, 1029.777, "SPEAKER_01")  # listener turn, empty
    seg.words = []
    # continuous pass HAS a word ("where") spanning this turn -> it's overlap/bleed
    continuous = [Word("where", 1029.20, 1030.20)]

    recover_orphan_turns(model, tmp_path / "audio.wav", [seg], continuous)

    assert seg.words == []          # left empty, not given the bled word
    assert model.calls == []        # slice was NOT transcribed


def test_transcribe_full_audio_returns_flat_chronological_words(monkeypatch, tmp_path):
    import src.transcribe as t
    # Stub audio loading so no real WAV is needed.
    monkeypatch.setattr(t, "load_wav", lambda p: (b"", 16000))
    model = _FakeModel([
        _FakeSeg([_FakeWord(" abortion", 1028.18, 1028.64),
                  _FakeWord(" tourism", 1028.64, 1029.20)]),
        _FakeSeg([_FakeWord(" where", 1029.20, 1030.20)]),
    ])

    words = transcribe_full_audio(model, tmp_path / "audio.wav")

    assert [w.word for w in words] == ["abortion", "tourism", "where"]
    assert words[0].start == 1028.18 and words[0].end == 1028.64
    # whole-audio call uses word_timestamps and is NOT sliced per segment
    assert model.calls[0]["word_timestamps"] is True


def test_recover_orphan_turns_skips_sub_min_seconds_turn(monkeypatch, tmp_path):
    import src.transcribe as t
    monkeypatch.setattr(t, "load_wav", lambda p: (np.zeros(16000 * 10), 16000))
    monkeypatch.setattr(t, "slice_audio", lambda samples, sr, a, b: np.zeros(int((b - a) * sr)))
    model = _FakeModel([_FakeSeg([_FakeWord(" x", 0.0, 0.05)])])
    seg = Segment(1, 5.00, 5.05, "M")  # 0.05s < min_seconds default 0.1
    seg.words = []

    recover_orphan_turns(model, tmp_path / "a.wav", [seg], [], min_seconds=0.1)

    assert seg.words == []
    assert model.calls == []  # too short to transcribe
