import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_importing_briefs_makes_shorts_engine_importable_in_a_clean_process():
    """Real runs import shorts_engine in-process; fakes in other tests hide a missing engine path."""
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    code = ("import scheduler.briefs.research; "
            "from shorts_engine.retrieval.search import web_search; print('ok')")
    r = subprocess.run([sys.executable, "-I", "-c", f"import sys; sys.path.insert(0, {str(ROOT)!r}); {code}"],
                       cwd=str(ROOT), env=env, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr[-400:]
    assert "ok" in r.stdout
