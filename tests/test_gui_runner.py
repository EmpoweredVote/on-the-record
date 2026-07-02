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
