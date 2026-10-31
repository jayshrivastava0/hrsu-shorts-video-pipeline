import subprocess
import sys

import pytest

from scheduler import nowindow


@pytest.mark.skipif(sys.platform != "win32", reason="Windows console flags")
def test_popen_gets_no_window_flag(monkeypatch):
    seen = {}
    monkeypatch.setattr(subprocess.Popen, "__init__", lambda self, *a, **k: seen.update(k) or None)
    monkeypatch.setattr(nowindow, "_installed", False)
    monkeypatch.delenv("HRSU_SHOW_CONSOLES", raising=False)
    assert nowindow.install()
    subprocess.Popen(["x"])
    assert seen["creationflags"] & subprocess.CREATE_NO_WINDOW
    seen.clear()
    subprocess.Popen(["x"], creationflags=subprocess.CREATE_NEW_CONSOLE)
    assert not seen["creationflags"] & subprocess.CREATE_NO_WINDOW


@pytest.mark.skipif(sys.platform != "win32", reason="Windows console flags")
def test_child_runs_hidden_and_output_still_captured():
    r = subprocess.run([sys.executable, "-c", "print('ok')"], capture_output=True, text=True)
    assert r.stdout.strip() == "ok"
    assert subprocess.Popen.__init__.__qualname__.startswith("install.")
