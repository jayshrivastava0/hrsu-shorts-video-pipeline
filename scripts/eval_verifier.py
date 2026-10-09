"""Score a candidate verifier model on the claims fixture.

Usage: python scripts/eval_verifier.py <model-name>   (needs the model reachable via Ollama)
The metric that matters most is false_support_rate: how often the model calls a
contradicted/unsupported claim "supported" (that is how a wrong fact reaches a video).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "_shorts_engine_impl" / "tests" / "shorts_engine" / "fixtures" / "claims_eval.json"


def score_model(cases: list[dict], ask) -> dict:
    labels = ("supported", "contradicted", "unsupported")
    hits = {l: [0, 0] for l in labels}
    false_support = wrong_total = 0
    for c in cases:
        got = ask(c["claim"], c["passages"])
        hits[c["expected"]][1] += 1
        hits[c["expected"]][0] += got == c["expected"]
        if c["expected"] != "supported":
            wrong_total += 1
            false_support += got == "supported"
    return {"per_class": {l: (h / n if n else 0.0) for l, (h, n) in hits.items()},
            "false_support_rate": false_support / wrong_total if wrong_total else 0.0}


def _ask_factory(model: str):
    sys.path.insert(0, str(ROOT / "_shorts_engine_impl"))
    sys.path.insert(0, str(ROOT))
    from video_agent.ollama_client import OllamaClient
    from shorts_engine.llm import text_llm
    from shorts_engine.stages import verify_claims as vc
    client = OllamaClient(model=model)
    if "cloud" in model:
        client.generate = lambda prompt, system=None, **_k: client._generate_via_sdk(prompt, system)

    def ask(claim, passages):
        listing = "\n".join(f"[{i}] {p}" for i, p in enumerate(passages))
        res = text_llm.generate_schema_json(
            f"Claim: {claim}\n\nPassages:\n{listing}", vc._VERIFIER_SYSTEM, vc.VERDICT_SCHEMA,
            client_factory=lambda: client)
        return res["verdict"]
    return ask


if __name__ == "__main__":
    model = sys.argv[1]
    cases = json.loads(FIXTURE.read_text(encoding="utf-8"))
    print(model, json.dumps(score_model(cases, _ask_factory(model)), indent=2))
