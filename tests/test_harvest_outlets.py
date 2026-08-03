import importlib.util
from pathlib import Path

_PATH = Path(__file__).resolve().parents[1] / "scripts/harvest_outlets.py"
_spec = importlib.util.spec_from_file_location("harvest_outlets", _PATH)
harvest_outlets = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(harvest_outlets)


def test_collect_channel_candidates_dedupes_by_channel(tmp_path):
    for mid, url, channel in [
        ("m1", "https://www.youtube.com/watch?v=aaa11111111", "KXAN"),
        ("m2", "https://www.youtube.com/watch?v=bbb11111111", "KXAN"),
        ("m3", "https://www.youtube.com/watch?v=ccc11111111", "PBS Wisconsin"),
        ("m4", "/local/audio.wav", None),
    ]:
        d = tmp_path / mid
        d.mkdir()
        meta = {"audio_source": url}
        if channel:
            meta["processing_metadata"] = {"source_channel": channel}
        (d / "transcript_named.json").write_text(__import__("json").dumps(meta))
    cands = harvest_outlets.collect_channel_candidates(tmp_path)
    assert sorted(cands) == [("KXAN", "https://www.youtube.com/watch?v=aaa11111111"),
                             ("PBS Wisconsin", "https://www.youtube.com/watch?v=ccc11111111")]
