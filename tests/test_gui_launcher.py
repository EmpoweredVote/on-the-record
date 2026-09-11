"""Tests for the cross-platform half of the GUI launcher.

The double-click wrappers (start-gui.command, start-gui.bat) must stay thin,
because they cannot be shared between platforms and so cannot be tested
together. Everything worth testing therefore lives in gui/__main__.py, and this
file is what keeps it there.
"""
import socket

import pytest

from gui.__main__ import main, open_when_ready, port_is_serving


@pytest.fixture
def listening_port():
    """A real bound, listening socket and its port."""
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(1)
    yield sock.getsockname()[1]
    sock.close()


@pytest.fixture
def closed_port():
    """A port number nothing is listening on."""
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def test_port_is_serving_sees_a_real_listener(listening_port):
    assert port_is_serving("127.0.0.1", listening_port)


def test_port_is_serving_is_false_on_a_closed_port(closed_port):
    assert not port_is_serving("127.0.0.1", closed_port, timeout=0.1)


def test_the_browser_opens_only_once_the_server_answers(monkeypatch):
    """Opening immediately would show a dead tab before uvicorn is up, which
    reads to the operator as the GUI being broken."""
    answers = iter([False, False, True])
    monkeypatch.setattr(
        "gui.__main__.port_is_serving", lambda *a, **k: next(answers)
    )
    opened = []
    assert open_when_ready(
        "http://x", "127.0.0.1", 1, opener=opened.append, sleep=lambda _: None
    )
    assert opened == ["http://x"]


def test_it_gives_up_quietly_when_the_server_never_answers(monkeypatch):
    """A browser that did not open is a far smaller problem than a launcher that
    hangs, so this must terminate and must not raise."""
    monkeypatch.setattr("gui.__main__.port_is_serving", lambda *a, **k: False)
    opened = []
    assert not open_when_ready(
        "http://x", "127.0.0.1", 1, attempts=3,
        opener=opened.append, sleep=lambda _: None,
    )
    assert opened == []


def test_an_already_running_gui_is_reported_not_restarted(listening_port, capsys):
    """Starting a second server just fails to bind, and 'address already in use'
    reads like a crash rather than 'it is already running'."""
    started = []
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr("uvicorn.run", lambda *a, **k: started.append(a))
    try:
        assert main(["--port", str(listening_port)]) == 0
    finally:
        monkeypatch.undo()
    assert started == []
    assert "already running" in capsys.readouterr().out


def test_an_already_running_gui_still_opens_the_browser_with_open(listening_port):
    opened = []
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr("uvicorn.run", lambda *a, **k: pytest.fail("must not start"))
    monkeypatch.setattr("webbrowser.open", opened.append)
    try:
        assert main(["--open", "--port", str(listening_port)]) == 0
    finally:
        monkeypatch.undo()
    assert opened == [f"http://127.0.0.1:{listening_port}"]
