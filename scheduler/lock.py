"""Single-run lock file (stale after N hours)."""
from __future__ import annotations

import os
import time
from pathlib import Path


class LockBusy(RuntimeError):
    pass


class RunLock:
    def __init__(self, path: Path | str, stale_after_s: float = 6 * 3600):
        self.path = Path(path)
        self.stale_after_s = stale_after_s

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for _ in range(2):
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            except FileExistsError:
                try:
                    age = time.time() - self.path.stat().st_mtime
                except FileNotFoundError:
                    continue
                if age < self.stale_after_s:
                    raise LockBusy(f"another run holds {self.path} (age {age / 60:.0f} min)")
                try:
                    self.path.unlink()
                except FileNotFoundError:
                    pass
                continue
            with os.fdopen(fd, "w") as fh:
                fh.write(str(os.getpid()))
            return
        raise LockBusy(f"could not acquire {self.path}")

    def release(self) -> None:
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *exc):
        self.release()
