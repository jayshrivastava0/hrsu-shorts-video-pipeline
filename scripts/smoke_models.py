"""Confirm each model answers a trivial JSON prompt through the same client path the pipeline
uses. Needed because this account's cloud access varies by model (kimi-k2.7-code returned 403;
see harness/cordis.yml). Usage: python scripts/smoke_models.py [model ...]
Default models: gemma4:31b-cloud glm-5.2:cloud glm-5.1:cloud nemotron-3-ultra:cloud minimax-m3:cloud
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "_shorts_engine_impl"))
sys.path.insert(0, str(ROOT))

DEFAULTS = ["gemma4:31b-cloud", "glm-5.2:cloud", "glm-5.1:cloud",
            "nemotron-3-ultra:cloud", "minimax-m3:cloud"]


def main(models: list[str]) -> int:
    from video_agent.ollama_client import OllamaClient
    bad = 0
    for model in models:
        try:
            client = OllamaClient(model=model)
            if "cloud" in model:
                client.generate = lambda p, system=None, _c=client, **_k: _c._generate_via_sdk(p, system)
            out = client.generate_json('Reply with exactly {"ok": true}', retries=1)
            print(f"OK    {model}: {out}")
        except Exception as exc:
            bad += 1
            print(f"FAIL  {model}: {exc}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:] or DEFAULTS))
