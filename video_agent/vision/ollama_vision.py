"""Shared cloud multimodal call helper.

Cloud Ollama models (e.g. gemma4:31b-cloud) reject the /api/generate `images`
field, so all image+prompt calls go through `ollama run MODEL PROMPT image.jpg`
as a subprocess. This module centralises that call plus the output parsing
(ANSI stripping, Gemma-4 think-block isolation, last-balanced-JSON extraction)
that small/cloud models require.

The parsing logic started as a mirror of
video_agent/harness/verify_vision.py::_parse_grade, but the ANSI stripping
here has since been FIXED: the old terminal-wrap heuristic re-assembled
wrapped lines and duplicated characters ("brabranded"). This module now uses
a conservative escape-sequence-only strip (see _strip_cli_noise);
verify_vision still carries the old heuristic.
"""
from __future__ import annotations
import json
import logging
import os
import re
import shutil
import subprocess
from pathlib import Path

log = logging.getLogger(__name__)

_OLLAMA = shutil.which("ollama")

# --- Parsing regexes ---------------------------------------------------------
_ANSI_CSI_RE = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
_ANSI_OSC_RE = re.compile(r"\x1b\][^\x07]*\x07")


def _strip_cli_noise(raw: str) -> str:
    """Conservative ANSI-only strip. The old terminal-wrap heuristic
    re-assembled wrapped lines and DUPLICATED characters ('brabranded');
    plain escape-sequence removal never does."""
    return _ANSI_OSC_RE.sub("", _ANSI_CSI_RE.sub("", raw))


def _parse_json_from_cli(raw: str) -> dict | list | None:
    """Strip ANSI escape sequences, isolate the post-thinking
    section, and return the LAST balanced JSON object/array in the text.
    Returns None if no valid JSON is found."""
    clean = _strip_cli_noise(raw)
    marker = "...done thinking."
    pos = clean.rfind(marker)
    if pos >= 0:
        clean = clean[pos + len(marker):]
    # Find the LAST balanced { ... } (the model's actual answer).
    i = len(clean) - 1
    while i >= 0:
        if clean[i] == "}":
            depth = 0
            for j in range(i, -1, -1):
                if clean[j] == "}":
                    depth += 1
                elif clean[j] == "{":
                    depth -= 1
                    if depth == 0:
                        try:
                            candidate = clean[j:i + 1].replace("\n", " ")
                            return json.loads(candidate)
                        except json.JSONDecodeError:
                            break
        i -= 1
    return None


def call_vision_json(
    prompt: str,
    image_path: Path,
    model: str,
    timeout_s: float,
) -> dict | list | None:
    """Run `ollama run MODEL PROMPT image` and return parsed JSON, or None on
    any failure (timeout, non-zero exit, unparseable output). NEVER raises —
    callers treat None as 'could not judge'.

    TERM=dumb + stdin=DEVNULL prevent cursor-rewrite ANSI noise corrupting the
    captured JSON (same guard verify_vision uses)."""
    if _OLLAMA is None:
        log.warning("ollama binary not found on PATH; vision call skipped")
        return None
    env = {**os.environ, "TERM": "dumb"}
    try:
        result = subprocess.run(
            [_OLLAMA, "run", model, prompt, str(image_path)],
            capture_output=True, timeout=timeout_s, env=env,
            stdin=subprocess.DEVNULL,
        )
    except subprocess.TimeoutExpired:
        log.warning("vision call timed out after %.0fs (model=%s, img=%s)",
                    timeout_s, model, image_path)
        return None
    except Exception as e:  # noqa: BLE001 — any subprocess error == no judgment
        log.warning("vision call subprocess error: %s", e)
        return None
    if result.returncode != 0:
        stderr = result.stderr.decode("utf-8", errors="replace")
        log.warning("vision call failed (exit %d): %s",
                    result.returncode, stderr[:200])
        return None
    stdout = result.stdout.decode("utf-8", errors="replace")
    return _parse_json_from_cli(stdout)


def _sdk_chat(model: str, messages: list) -> "object":
    """Seam for tests. Import inside so the SDK stays an optional dependency."""
    from ollama import chat
    return chat(model=model, messages=messages)


def call_vision_json_sdk(prompt: str, image_path: Path, model: str,
                         timeout_s: float) -> dict | list | None:
    """SDK-transport vision call. Never raises; None on any failure."""
    try:
        resp = _sdk_chat(model, [{
            "role": "user", "content": prompt, "images": [str(image_path)],
        }])
        raw = resp.message.content or ""
    except Exception as e:  # noqa: BLE001 — any failure == no judgment
        log.warning("vision SDK call failed: %s", e)
        return None
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip(),
                     flags=re.IGNORECASE | re.MULTILINE).strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        return _parse_json_from_cli(cleaned)


_TRANSPORT: str | None = None  # remembered per process after first success


def call_vision_auto(prompt: str, image_path: Path, model: str,
                     timeout_s: float) -> dict | list | None:
    """Empirical transport selection (spec §6.3a): SDK first, CLI fallback;
    the first transport that succeeds is remembered for the process."""
    global _TRANSPORT
    order = {"sdk": ["sdk"], "cli": ["cli"]}.get(_TRANSPORT, ["sdk", "cli"])
    for transport in order:
        fn = call_vision_json_sdk if transport == "sdk" else call_vision_json
        out = fn(prompt, image_path, model, timeout_s)
        if out is not None:
            _TRANSPORT = transport
            return out
    return None
