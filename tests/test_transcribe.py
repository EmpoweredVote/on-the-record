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
