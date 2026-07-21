from __future__ import annotations

import pytest

from gui.runner import RunParams, build_run_command, derive_meeting_id


def test_derive_meeting_id_from_date_and_type():
    p = RunParams(input="https://x/v", date="2026-02-10", meeting_type="Regular Session",
                  event_kind="council")
    assert derive_meeting_id(p) == "2026-02-10-regular-session"


def test_derive_meeting_id_custom_wins():
    p = RunParams(input="x", date="2026-02-10", meeting_type="Regular Session",
                  event_kind="council", meeting_id="my-custom-id")
    assert derive_meeting_id(p) == "my-custom-id"


def test_derive_meeting_id_rejects_unsafe():
    p = RunParams(input="x", date="2026-02-10", meeting_type="a/b", event_kind="council")
    # slug strips the slash -> safe single component
    assert derive_meeting_id(p) == "2026-02-10-a-b"
    with pytest.raises(ValueError):
        derive_meeting_id(RunParams(input="x", date="", meeting_type="", event_kind="council"))


def test_build_run_command_core_and_optional_flags():
    p = RunParams(input="https://x/v", date="2026-02-10", meeting_type="Regular Session",
                  event_kind="council", city="Bloomington", compute="modal", diarizer="api",
                  title="Budget Hearing", clip_start="10:00", clip_end="20:00", num_speakers=5)
    cmd = build_run_command("/venv/bin/python", "/repo/run_local.py", p, "2026-02-10-regular-session")
    # core
    assert cmd[:2] == ["/venv/bin/python", "/repo/run_local.py"]
    assert "--input" in cmd and "https://x/v" in cmd
    assert cmd[cmd.index("--meeting-id") + 1] == "2026-02-10-regular-session"
    assert cmd[cmd.index("--event-kind") + 1] == "council"
    assert cmd[cmd.index("--compute") + 1] == "modal"
    assert cmd[cmd.index("--diarizer") + 1] == "api"
    assert cmd[cmd.index("--title") + 1] == "Budget Hearing"
    assert cmd[cmd.index("--num-speakers") + 1] == "5"
    # --clip takes two values START END
    ci = cmd.index("--clip")
    assert cmd[ci + 1] == "10:00" and cmd[ci + 2] == "20:00"


def test_build_run_command_omits_absent_optionals():
    p = RunParams(input="x", date="2026-02-10", meeting_type="Regular", event_kind="council")
    cmd = build_run_command("py", "s", p, "2026-02-10-regular")
    for flag in ("--title", "--clip", "--city", "--num-speakers", "--congressional-record"):
        assert flag not in cmd


def test_build_run_command_congressional_record_reuses_meeting_date():
    # picking a chamber emits --congressional-record DATE CHAMBER, using the
    # meeting date as the CREC date (a floor session's date IS its CREC date).
    p = RunParams(input="https://youtu.be/x", date="2026-03-27", meeting_type="House Floor",
                  event_kind="other", crec_chamber="house")
    cmd = build_run_command("py", "s", p, "2026-03-27-house-floor")
    ci = cmd.index("--congressional-record")
    assert cmd[ci + 1] == "2026-03-27" and cmd[ci + 2] == "house"
    # compute/diarizer default through to the flags (explicit is fine)
    assert cmd[cmd.index("--compute") + 1] == "local"


from gui.runner import RunParams, derive_meeting_id, build_run_command


def _p(**kw):
    base = dict(input="https://x/v", date="2026-05-01", meeting_type="Interview",
                event_kind="news_clip")
    base.update(kw)
    return RunParams(**base)


def test_derive_id_council_prefers_body_then_city():
    p = _p(event_kind="council", meeting_type="Regular Session",
           body_slug="bloomington-city-council", city="Bloomington")
    assert derive_meeting_id(p) == "2026-05-01-bloomington-city-council-regular-session"
    p2 = _p(event_kind="council", meeting_type="Special Session", city="Monroe")
    assert derive_meeting_id(p2) == "2026-05-01-monroe-special-session"


def test_derive_id_floor_uses_label_only():
    p = _p(event_kind="floor", meeting_type="House Floor")
    assert derive_meeting_id(p) == "2026-05-01-house-floor"


def test_derive_id_interview_guest_before_race():
    p = _p(event_kind="news_clip", meeting_type="Interview",
           guest="Xavier Becerra", race_slug="ca-governor")
    assert derive_meeting_id(p) == "2026-05-01-becerra-ca-governor-interview" \
        or derive_meeting_id(p) == "2026-05-01-xavier-becerra-ca-governor-interview"


def test_derive_id_interview_guest_only_then_org():
    p = _p(event_kind="news_clip", meeting_type="Interview", guest="Xavier Becerra")
    assert derive_meeting_id(p) == "2026-05-01-xavier-becerra-interview"
    p2 = _p(event_kind="news_clip", meeting_type="Interview", event_orgs=["CBS"])
    assert derive_meeting_id(p2) == "2026-05-01-cbs-interview"


def test_derive_id_forum_prefers_race():
    p = _p(event_kind="forum", meeting_type="Candidate Forum", race_slug="tx-senate",
           event_orgs=["LWV"])
    assert derive_meeting_id(p) == "2026-05-01-tx-senate-candidate-forum"


def test_derive_id_overlap_dedup():
    # label already contains the locus -> locus dropped, no doubling
    p = _p(event_kind="council", meeting_type="Bloomington Regular Session",
           city="Bloomington")
    mid = derive_meeting_id(p)
    assert mid == "2026-05-01-bloomington-regular-session"


def test_derive_id_length_capped():
    p = _p(event_kind="news_clip", meeting_type="Interview",
           guest="A" * 120)
    assert len(derive_meeting_id(p)) <= 80


def test_build_run_command_includes_race_id():
    p = _p(event_kind="news_clip", race_id="uuid-123")
    cmd = build_run_command("py", "run_local.py", p, "2026-05-01-x-interview")
    assert "--race-id" in cmd and "uuid-123" in cmd
    # absent when no race_id
    p2 = _p(event_kind="news_clip")
    assert "--race-id" not in build_run_command("py", "run_local.py", p2, "m")


import json
from pathlib import Path


class _FakePopen:
    """Stand-in for subprocess.Popen: records args, controllable exit."""
    def __init__(self, cmd, **kw):
        self.cmd = cmd
        self.kw = kw
        self.pid = 4321
        self._rc = None
        # write a marker to the provided stdout so the log-tail path has content
        out = kw.get("stdout")
        if out is not None and hasattr(out, "write"):
            out.write(b"STAGE 1: Audio Ingestion\n")
            out.flush()

    def poll(self):
        return self._rc

    def finish(self, rc=0):
        self._rc = rc


def test_launch_run_spawns_and_records(tmp_meetings_dir):
    from gui import runner
    runner._RUNS.clear()
    captured = {}

    def fake_popen(cmd, **kw):
        captured["cmd"] = cmd
        captured["stdin_devnull"] = kw.get("stdin") is not None
        return _FakePopen(cmd, **kw)

    p = runner.RunParams(input="https://x/v", date="2026-02-10", meeting_type="Regular",
                         event_kind="council")
    mid = runner.launch_run(p, python_exe="py", script="run_local.py", popen=fake_popen)

    assert mid == "2026-02-10-regular"
    assert mid in runner._RUNS
    mdir = tmp_meetings_dir / mid
    assert (mdir / "gui_run.log").exists()      # stdout captured here
    side = json.loads((mdir / "gui_run.json").read_text())
    assert side["status"] == "running" and side["pid"] == 4321
    assert captured["cmd"][0] == "py"


def test_run_status_reports_stage_and_liveness(tmp_meetings_dir):
    from gui import runner
    runner._RUNS.clear()
    p = runner.RunParams(input="x", date="2026-02-10", meeting_type="Regular", event_kind="council")
    proc_box = {}

    def fake_popen(cmd, **kw):
        proc = _FakePopen(cmd, **kw)
        proc_box["p"] = proc
        return proc

    mid = runner.launch_run(p, python_exe="py", script="s", popen=fake_popen)
    mdir = tmp_meetings_dir / mid
    # simulate the pipeline having written progress
    (mdir / "pipeline_state.json").write_text(json.dumps({"completed_stage": 2}))

    st = runner.run_status(mid)
    assert st["running"] is True
    assert st["completed_stage"] == 2
    assert st["stage_label"]                       # human label present
    assert "STAGE 1" in st["log_tail"]

    proc_box["p"].finish(rc=0)
    st2 = runner.run_status(mid)
    assert st2["running"] is False
    assert st2["exit_code"] == 0

    assert runner.run_status("no-such-meeting") is None


def test_run_status_liveness_fallback_via_sidecar_pid(tmp_meetings_dir, monkeypatch):
    """After a GUI restart (_RUNS empty), liveness is recovered from the sidecar
    pid via os.kill(pid, 0)."""
    from gui import runner
    runner._RUNS.clear()
    mid = "2026-02-10-regular"
    mdir = tmp_meetings_dir / mid
    mdir.mkdir(parents=True, exist_ok=True)
    (mdir / "gui_run.json").write_text(json.dumps({"pid": 9999, "cmd": [], "status": "running"}))
    (mdir / "pipeline_state.json").write_text(json.dumps({"completed_stage": 2}))

    # os.kill succeeds -> process alive -> running True
    monkeypatch.setattr(runner.os, "kill", lambda pid, sig: None)
    st = runner.run_status(mid)
    assert st is not None
    assert st["running"] is True
    assert st["completed_stage"] == 2

    # os.kill raises ProcessLookupError -> process dead -> running False
    def _dead(pid, sig):
        raise ProcessLookupError
    monkeypatch.setattr(runner.os, "kill", _dead)
    st2 = runner.run_status(mid)
    assert st2["running"] is False


def test_find_meeting_by_source_matches_state_key(tagged_meeting_dir, tmp_meetings_dir):
    from gui.runner import find_meeting_by_source
    mdir = tagged_meeting_dir("x", meeting_id="2026-02-10-regular", completed_stage=4)
    # record a source_key on the meeting's state
    from src.checkpoint import PipelineState
    st = PipelineState(mdir)
    st.source_key = "youtube:abc123"
    st.save()

    assert find_meeting_by_source("https://youtu.be/abc123") == "2026-02-10-regular"
    assert find_meeting_by_source("https://youtu.be/different") is None


def test_find_meeting_by_source_fallback_to_audio_source(tagged_meeting_dir, tmp_meetings_dir):
    import json
    from gui.runner import find_meeting_by_source
    mdir = tagged_meeting_dir("x", meeting_id="2026-03-01-regular", completed_stage=4)
    # NO source_key in state; only a transcript_named.json with audio_source
    (mdir / "transcript_named.json").write_text(json.dumps(
        {"audio_source": "https://www.youtube.com/watch?v=zzz999"}))
    assert find_meeting_by_source("https://youtu.be/zzz999") == "2026-03-01-regular"


def test_find_meeting_by_source_blank_returns_none(tmp_meetings_dir):
    from gui.runner import find_meeting_by_source
    assert find_meeting_by_source("") is None


def test_collapse_progress_keeps_final_overwrite():
    from gui.runner import _collapse_progress
    raw = ("[download] 1% of 56MiB\r[download] 50% of 56MiB\r"
           "[download] 100% of 56MiB in 5s\n[Merger] merging\n")
    assert _collapse_progress(raw) == "[download] 100% of 56MiB in 5s\n[Merger] merging\n"
    # plain line untouched; \r\n endings must not blank the line
    assert _collapse_progress("STAGE 1\r\nnext\n") == "STAGE 1\nnext\n"


def test_log_tail_collapses_download_spam(tmp_path):
    from gui.runner import _log_tail, _LOG_NAME
    spam = "".join(f"[download] {p}% of 56MiB\r" for p in range(0, 100, 5))
    spam += "[download] 100% of 56MiB in 5s\nDone.\n"
    (tmp_path / _LOG_NAME).write_bytes(spam.encode())
    tail = _log_tail(tmp_path)
    assert "100% of 56MiB in 5s" in tail
    assert tail.count("[download]") == 1   # collapsed to one line, not ~20
    assert "Done." in tail


def test_launch_run_sets_unbuffered_env(tmp_meetings_dir):
    from gui import runner
    runner._RUNS.clear()
    captured = {}

    def fake_popen(cmd, **kw):
        captured.update(kw)
        return _FakePopen(cmd, **kw)

    p = runner.RunParams(input="x", date="2026-02-10", meeting_type="Regular", event_kind="council")
    runner.launch_run(p, python_exe="py", script="s", popen=fake_popen)
    assert captured["env"].get("PYTHONUNBUFFERED") == "1"


def test_run_status_works_without_sidecar(tagged_meeting_dir, tmp_meetings_dir):
    from gui import runner
    runner._RUNS.clear()
    # a meeting with pipeline_state but NO gui_run.json sidecar (e.g. CLI-processed
    # or reviewed) still returns a status snapshot, not None.
    mdir = tagged_meeting_dir("x", meeting_id="2026-02-04-council", completed_stage=5)
    st = runner.run_status("2026-02-04-council")
    assert st is not None
    assert st["completed_stage"] == 5
    assert st["running"] is False
    # truly-unknown meeting is still None
    assert runner.run_status("no-such-meeting") is None


def test_build_redo_command():
    from gui.runner import build_redo_command
    cmd = build_redo_command("py", "run_local.py", "2026-02-04-council", "diarize")
    assert cmd == ["py", "run_local.py", "--resume", "2026-02-04-council", "--redo", "diarize"]


def test_launch_redo_spawns_resume_redo(tagged_meeting_dir, tmp_meetings_dir):
    from gui import runner
    runner._RUNS.clear()
    tagged_meeting_dir("x", meeting_id="2026-02-04-council", completed_stage=4)
    captured = {}

    def fake_popen(cmd, **kw):
        captured["cmd"] = cmd
        captured["env_unbuffered"] = kw.get("env", {}).get("PYTHONUNBUFFERED")
        return _FakePopen(cmd, **kw)

    mid = runner.launch_redo("2026-02-04-council", "transcribe",
                             python_exe="py", script="run_local.py", popen=fake_popen)
    assert mid == "2026-02-04-council"
    assert captured["cmd"] == ["py", "run_local.py", "--resume", "2026-02-04-council",
                               "--redo", "transcribe"]
    assert captured["env_unbuffered"] == "1"           # reuses the unbuffered launch
    assert mid in runner._RUNS
    mdir = tmp_meetings_dir / mid
    assert (mdir / "gui_run.log").exists() and (mdir / "gui_run.json").exists()


def test_launch_redo_guards(tagged_meeting_dir, tmp_meetings_dir):
    from gui import runner
    runner._RUNS.clear()
    tagged_meeting_dir("x", meeting_id="2026-02-04-council", completed_stage=4)
    assert runner.launch_redo("2026-02-04-council", "bogus", python_exe="p", script="s") is None  # bad stage
    assert runner.launch_redo("ghost", "diarize", python_exe="p", script="s") is None            # no meeting
    assert runner.launch_redo("../x", "diarize", python_exe="p", script="s") is None              # unsafe id


def test_existing_launch_run_still_works(tmp_meetings_dir):
    # the _spawn refactor must not change launch_run behavior
    from gui import runner
    runner._RUNS.clear()
    p = runner.RunParams(input="x", date="2026-02-10", meeting_type="Regular", event_kind="council")
    mid = runner.launch_run(p, python_exe="py", script="s", popen=_FakePopen)
    assert mid == "2026-02-10-regular" and mid in runner._RUNS


def test_build_resume_command():
    from gui.runner import build_resume_command
    assert build_resume_command("py", "s", "m") == ["py", "s", "--resume", "m", "--no-publish"]
    assert build_resume_command("py", "s", "m", override_gate=True) == \
        ["py", "s", "--resume", "m", "--no-publish", "--publish-anyway"]


def test_resume_command_never_publishes():
    """Safety: Continue must never publish — --no-publish is always present, in
    both plain and gate-override modes (--resume auto-enables publish otherwise)."""
    from gui.runner import build_resume_command
    assert "--no-publish" in build_resume_command("py", "s", "m")
    assert "--no-publish" in build_resume_command("py", "s", "m", override_gate=True)


def test_launch_resume_spawns(tagged_meeting_dir, tmp_meetings_dir):
    from gui import runner
    runner._RUNS.clear()
    tagged_meeting_dir("x", meeting_id="2026-02-04-council", completed_stage=4)
    captured = {}

    def fake_popen(cmd, **kw):
        captured["cmd"] = cmd
        return _FakePopen(cmd, **kw)

    mid = runner.launch_resume("2026-02-04-council", python_exe="py", script="s", popen=fake_popen)
    assert mid == "2026-02-04-council"
    assert captured["cmd"] == ["py", "s", "--resume", "2026-02-04-council", "--no-publish"]
    assert mid in runner._RUNS


def test_launch_resume_override_adds_flag(tagged_meeting_dir, tmp_meetings_dir):
    from gui import runner
    runner._RUNS.clear()
    tagged_meeting_dir("x", meeting_id="2026-02-04-council", completed_stage=4)
    captured = {}
    runner.launch_resume("2026-02-04-council", override_gate=True,
                         python_exe="py", script="s",
                         popen=lambda cmd, **kw: captured.setdefault("cmd", cmd) or _FakePopen(cmd, **kw))
    assert "--publish-anyway" in captured["cmd"]


def test_launch_resume_guards(tagged_meeting_dir, tmp_meetings_dir):
    from gui import runner
    assert runner.launch_resume("ghost", python_exe="p", script="s") is None       # no meeting
    assert runner.launch_resume("../x", python_exe="p", script="s") is None         # unsafe id


def _set_source(mdir, source_key_value):
    from src.checkpoint import PipelineState
    st = PipelineState(mdir); st.source_key = source_key_value; st.save()


def test_unique_meeting_id_free_base(tmp_meetings_dir):
    from gui.runner import _unique_meeting_id
    assert _unique_meeting_id("2026-05-15-interview", "youtube:AAA") == "2026-05-15-interview"


def test_unique_meeting_id_same_source_reuses(tagged_meeting_dir, tmp_meetings_dir):
    from gui.runner import _unique_meeting_id
    mdir = tagged_meeting_dir("x", meeting_id="2026-05-15-interview", completed_stage=4)
    _set_source(mdir, "youtube:AAA")
    # same video re-submitted -> reuse the existing id (no new dir)
    assert _unique_meeting_id("2026-05-15-interview", "youtube:AAA") == "2026-05-15-interview"


def test_unique_meeting_id_bumps_on_different_source(tagged_meeting_dir, tmp_meetings_dir):
    from gui.runner import _unique_meeting_id
    mdir = tagged_meeting_dir("x", meeting_id="2026-05-15-interview", completed_stage=4)
    _set_source(mdir, "youtube:AAA")
    # a DIFFERENT video, same date+label -> must not collide
    assert _unique_meeting_id("2026-05-15-interview", "youtube:ZZZ") == "2026-05-15-interview-2"


def test_unique_meeting_id_bumps_past_multiple(tagged_meeting_dir, tmp_meetings_dir):
    from gui.runner import _unique_meeting_id
    _set_source(tagged_meeting_dir("x", meeting_id="2026-05-15-interview", completed_stage=4), "youtube:AAA")
    _set_source(tagged_meeting_dir("x", meeting_id="2026-05-15-interview-2", completed_stage=4), "youtube:BBB")
    assert _unique_meeting_id("2026-05-15-interview", "youtube:ZZZ") == "2026-05-15-interview-3"


def test_launch_run_bumps_colliding_id(tagged_meeting_dir, tmp_meetings_dir):
    from gui import runner
    runner._RUNS.clear()
    _set_source(tagged_meeting_dir("x", meeting_id="2026-05-15-interview", completed_stage=4), "youtube:AAA")
    p = runner.RunParams(input="https://youtu.be/ZZZ", date="2026-05-15",
                         meeting_type="Interview", event_kind="news_clip")
    mid = runner.launch_run(p, python_exe="py", script="s", popen=_FakePopen)
    assert mid == "2026-05-15-interview-2"           # new video -> distinct meeting
    assert (tmp_meetings_dir / "2026-05-15-interview-2").exists()


def test_build_run_command_includes_event_orgs():
    from gui.runner import RunParams, build_run_command
    p = RunParams(input="x", date="2026-05-15", meeting_type="Interview", event_kind="news_clip",
                  event_orgs=["CBS", "NBC"])
    cmd = build_run_command("py", "s", p, "2026-05-15-interview")
    # one --event-org per org, in order
    assert cmd.count("--event-org") == 2
    i = cmd.index("--event-org")
    assert cmd[i + 1] == "CBS"
    assert "NBC" in cmd


def test_build_run_command_omits_event_orgs_when_empty():
    from gui.runner import RunParams, build_run_command
    p = RunParams(input="x", date="2026-05-15", meeting_type="Interview", event_kind="news_clip")
    assert "--event-org" not in build_run_command("py", "s", p, "m")


def test_build_run_command_includes_body():
    from gui.runner import RunParams, build_run_command
    p = RunParams(input="x", date="2026-02-04", meeting_type="Regular Session",
                  event_kind="council", body_slug="bloomington-common-council")
    cmd = build_run_command("py", "s", p, "2026-02-04-regular-session")
    assert cmd[cmd.index("--body") + 1] == "bloomington-common-council"


def test_build_run_command_omits_body_when_absent():
    from gui.runner import RunParams, build_run_command
    p = RunParams(input="x", date="2026-02-04", meeting_type="Regular", event_kind="council")
    assert "--body" not in build_run_command("py", "s", p, "m")
