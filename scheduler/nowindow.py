"""Stop child processes from flashing console windows on Windows.

Every ffmpeg/ffprobe/node/claude call the pipeline makes would otherwise open a console window for a moment.
`install()` makes subprocess.Popen add CREATE_NO_WINDOW; the child gets a hidden console that its own children
inherit, so the whole process tree stays invisible. Set HRSU_SHOW_CONSOLES=1 to turn it off when debugging.
"""
from __future__ import annotations

import os
import subprocess
import sys

_installed = False


def install() -> bool:
    global _installed
    if _installed or sys.platform != "win32" or os.environ.get("HRSU_SHOW_CONSOLES") == "1":
        return False
    no_window = subprocess.CREATE_NO_WINDOW
    own_console = subprocess.CREATE_NEW_CONSOLE | subprocess.DETACHED_PROCESS
    original = subprocess.Popen.__init__

    def init(self, *args, **kwargs):
        flags = kwargs.get("creationflags", 0) or 0
        if not flags & own_console:      # a caller that asked for its own console keeps it
            kwargs["creationflags"] = flags | no_window
        original(self, *args, **kwargs)

    subprocess.Popen.__init__ = init
    _installed = True
    return True
