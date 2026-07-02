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
    for flag in ("--title", "--clip", "--city", "--num-speakers"):
        assert flag not in cmd
    # compute/diarizer default through to the flags (explicit is fine)
    assert cmd[cmd.index("--compute") + 1] == "local"


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
