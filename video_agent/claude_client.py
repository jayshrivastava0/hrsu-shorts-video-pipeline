"""Text-LLM backend that shells out to the Claude Code CLI (`claude -p`).

Drop-in for OllamaClient where the pipeline only needs `generate` / `generate_json`.
Default backend (HRSU_LLM_BACKEND=ollama switches back to Ollama); the model per role comes from
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
    # Claude is the default text backend; set HRSU_LLM_BACKEND=ollama to go back to Gemma/Nemotron.
    return os.environ.get("HRSU_LLM_BACKEND", "claude").strip().lower() == "claude"


def claude_model_for(role: str | None) -> str:
    if role:
        specific = os.environ.get(f"HRSU_CLAUDE_MODEL_{role.upper()}")
        if specific:
            return specific
    return os.environ.get("HRSU_CLAUDE_MODEL", "sonnet")


def _log_cost(model: str, envelope: dict) -> None:
    """Append one line per call (API-equivalent USD reported by the CLI) to HRSU_COST_LOG, if set."""
    path = os.environ.get("HRSU_COST_LOG")
    if not path:
        return
    try:
        u = envelope.get("usage") or {}
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"model": model, "usd": envelope.get("total_cost_usd"),
                                "in": u.get("input_tokens"), "out": u.get("output_tokens"),
                                "cache_read": u.get("cache_read_input_tokens"),
                                "cache_write": u.get("cache_creation_input_tokens")}) + "\n")
    except OSError:
        pass


class ClaudeCliClient(OllamaClient):
    def __init__(self, model: str = "sonnet", timeout: float | None = None):
        super().__init__(model=model, timeout=timeout)
        self.json_schema: dict | None = None  # set by text_llm; enforces valid JSON server-side

    def generate(self, prompt: str, system: str | None = None, **_kw) -> str:
        cmd = [shutil.which("claude") or "claude", "-p", "--model", self.model,
               "--tools", "", "--disable-slash-commands", "--no-session-persistence",
               "--setting-sources", "", "--strict-mcp-config"]   # no MCP: ~39k tokens of tool defs per call
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
                _log_cost(self.model, env)
                if env.get("structured_output") is not None:
                    return json.dumps(env["structured_output"])
                out = (env.get("result") or "").strip()
            except json.JSONDecodeError:
                pass
        return _strip_terminal_noise(out)
