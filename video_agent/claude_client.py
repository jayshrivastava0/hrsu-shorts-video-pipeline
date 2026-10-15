"""Text-LLM backend that shells out to the Claude Code CLI (`claude -p`).

Drop-in for OllamaClient where the pipeline only needs `generate` / `generate_json`.
Enabled with env HRSU_LLM_BACKEND=claude; the model per role comes from
HRSU_CLAUDE_MODEL_<ROLE> (planner|writer|verifier|critic), then HRSU_CLAUDE_MODEL,
default "sonnet". Tools, skills, hooks and MCP are all off: it is a pure text call.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import subprocess

from video_agent.ollama_client import OllamaClient, OllamaError, _strip_terminal_noise

log = logging.getLogger(__name__)

CLAUDE_TIMEOUT_S = 600


def backend_is_claude() -> bool:
    return os.environ.get("HRSU_LLM_BACKEND", "").strip().lower() == "claude"


def claude_model_for(role: str | None) -> str:
    if role:
        specific = os.environ.get(f"HRSU_CLAUDE_MODEL_{role.upper()}")
        if specific:
            return specific
    return os.environ.get("HRSU_CLAUDE_MODEL", "sonnet")


class ClaudeCliClient(OllamaClient):
    def __init__(self, model: str = "sonnet", timeout: float | None = None):
        super().__init__(model=model, timeout=timeout)
        self.json_schema: dict | None = None  # set by text_llm; enforces valid JSON server-side

    def generate(self, prompt: str, system: str | None = None, **_kw) -> str:
        cmd = [shutil.which("claude") or "claude", "-p", "--model", self.model,
               "--tools", "", "--disable-slash-commands", "--no-session-persistence",
               "--setting-sources", ""]
        if system:
            cmd += ["--system-prompt", system]
        if self.json_schema is not None:
            cmd += ["--output-format", "json", "--json-schema", json.dumps(self.json_schema)]
        try:
            r = subprocess.run(cmd, input=prompt, capture_output=True, text=True,
                               encoding="utf-8", timeout=self.timeout or CLAUDE_TIMEOUT_S)
        except subprocess.TimeoutExpired as e:
            raise OllamaError(f"claude CLI timed out after {e.timeout}s") from e
        except OSError as e:
            raise OllamaError(f"claude CLI could not start: {e}") from e
        out = (r.stdout or "").strip()
        if r.returncode != 0 or not out:
            raise OllamaError(
                f"claude CLI failed (exit {r.returncode}): {(r.stderr or out)[:300]}")
        if self.json_schema is not None:
            try:
                env = json.loads(out)
                if env.get("structured_output") is not None:
                    return json.dumps(env["structured_output"])
                out = (env.get("result") or "").strip()
            except json.JSONDecodeError:
                pass
        return _strip_terminal_noise(out)
