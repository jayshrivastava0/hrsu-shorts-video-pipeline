"""Thin wrapper around Ollama HTTP API with JSON-mode + tool-call helpers.

Gemma 4 specifics:
  * Sampling defaults: temperature=1.0, top_p=0.95, top_k=64 (per Google spec).
  * Thinking mode: enabled by prepending '<|think|>' to the system prompt;
    output begins with '<|channel>thought\\n...<channel|>' then the answer.
    We strip that block before returning.
  * Multi-turn rule: model output passed back as history must NOT include
    prior thought blocks. We don't currently use multi-turn — comment flag
    only.
"""
import json
import re
import subprocess
import logging
import requests
from video_agent.config import OLLAMA_HOST, OLLAMA_MODEL, OLLAMA_RETRY_MAX

log = logging.getLogger(__name__)


class OllamaError(RuntimeError):
    pass


_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE | re.MULTILINE)
_THOUGHT_RE = re.compile(
    r"<\|channel>thought\n.*?<channel\|>", re.DOTALL,
)
_TRAILING_COMMA_RE = re.compile(r",(\s*[}\]])")
# Adjacent array items missing the separating comma — small LLMs frequently
# emit `}\n  {` or `]\n  [` inside arrays. The whitespace MUST contain a
# newline (or comma already), so we won't accidentally split nested objects
# on the same line.
_MISSING_COMMA_RE = re.compile(r"([}\]])(\s*\n\s*)([{\[])")


def _extract_outermost_json(text: str) -> str | None:
    """Find the largest balanced {...} or [...] block in text. Small LLMs
    often emit prose before/after JSON or truncate trailing whitespace; this
    isolates the actual JSON payload so json.loads has a chance."""
    # Prefer { over [ (object responses are our standard)
    for open_ch, close_ch in (("{", "}"), ("[", "]")):
        start = text.find(open_ch)
        if start < 0:
            continue
        depth = 0
        in_str = False
        esc = False
        for i in range(start, len(text)):
            ch = text[i]
            if esc:
                esc = False
                continue
            if ch == "\\":
                esc = True
                continue
            if ch == '"':
                in_str = not in_str
                continue
            if in_str:
                continue
            if ch == open_ch:
                depth += 1
            elif ch == close_ch:
                depth -= 1
                if depth == 0:
                    return text[start:i + 1]
        # Unbalanced — try the other bracket type
    return None


_CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _repair_json(raw: str) -> str:
    """Best-effort JSON repair for small-LLM output.
    1. Strip raw control characters (CLI terminal noise) that break json.loads
    2. Isolate outermost balanced JSON block (drops surrounding prose)
    3. Strip trailing commas before } or ]
    Returns the repaired string; caller decides whether to retry parsing."""
    # Remove control chars except \t (0x09), \n (0x0a), \r (0x0d) which are
    # valid in JSON strings / whitespace.
    raw = _CONTROL_CHAR_RE.sub("", raw)
    extracted = _extract_outermost_json(raw) or raw
    extracted = _MISSING_COMMA_RE.sub(r"\1,\2\3", extracted)
    return _TRAILING_COMMA_RE.sub(r"\1", extracted)

# Gemma 4 sampling defaults (Google spec, applied unless caller overrides).
_GEMMA4_DEFAULTS = {"temperature": 1.0, "top_p": 0.95, "top_k": 64}


_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*[mGKHF]")
_THINK_BLOCK_RE = re.compile(
    r"(<\|channel>thought\n.*?<channel\|>|<\|think\|>.*?<\|/think\|>)",
    re.DOTALL,
)


def _strip_terminal_noise(raw: str) -> str:
    """Remove ANSI escape codes and Gemma 4 think blocks from CLI output."""
    cleaned = _ANSI_ESCAPE_RE.sub("", raw)
    cleaned = _THINK_BLOCK_RE.sub("", cleaned)
    return cleaned.strip()


class OllamaClient:
    def __init__(self, host: str = OLLAMA_HOST, model: str = OLLAMA_MODEL,
                 timeout: float | None = None, think_mode: bool = True):
        self.host = host
        self.model = model
        self.timeout = timeout
        self.think_mode = think_mode

    def _generate_via_sdk(self, prompt: str, system: str | None = None) -> str:
        """Call `ollama.chat()` via the Python SDK — correct path for cloud
        models (e.g. gemma4:31b-cloud) that reject /api/generate. Avoids all
        Windows CLI encoding / terminal-escape issues."""
        try:
            from ollama import chat as _ollama_chat
        except ImportError as e:
            raise OllamaError(
                "ollama Python SDK not installed — run: pip install ollama"
            ) from e
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        try:
            response = _ollama_chat(model=self.model, messages=messages)
        except Exception as e:
            raise OllamaError(f"ollama SDK chat failed: {e}") from e
        raw = response.message.content or ""
        return _strip_terminal_noise(raw)

    def _generate_via_cli(self, prompt: str, system: str | None = None) -> str:
        """Call `ollama run MODEL PROMPT` as a subprocess — fallback for cloud
        models that reject the /api/generate endpoint for text-only calls.
        Strips ANSI codes and Gemma 4 think blocks before returning."""
        combined = f"{system}\n\n{prompt}" if system else prompt
        cmd = ["ollama", "run", self.model, combined]
        try:
            result = subprocess.run(
                cmd, capture_output=True,
                encoding="utf-8", errors="replace",
                timeout=self.timeout or 120,
                stdin=subprocess.DEVNULL,
                env={**__import__("os").environ, "TERM": "dumb"},
            )
        except subprocess.TimeoutExpired as e:
            raise OllamaError(
                f"ollama run timed out for model {self.model}"
            ) from e
        except FileNotFoundError as e:
            raise OllamaError("ollama CLI not found on PATH") from e
        if result.returncode != 0:
            raise OllamaError(
                f"ollama run exited {result.returncode}: {result.stderr[:200]}"
            )
        return _strip_terminal_noise(result.stdout)

    def generate(self, prompt: str, system: str | None = None,
                 temperature: float | None = None,
                 top_p: float | None = None,
                 top_k: int | None = None,
                 images: list[str] | None = None) -> str:
        """Send a single-turn generation. Strips Gemma 4 thought blocks
        from the response before returning.

        Routes text-only calls for smart/cloud models via CLI when
        SMART_TEXT_TRANSPORT == 'cli' (no images; images always use API)."""
        import video_agent.config as _cfg
        if images is None and self.model == _cfg.SMART_TEXT_MODEL:
            if _cfg.SMART_TEXT_TRANSPORT == "sdk":
                return self._generate_via_sdk(prompt, system)
            if _cfg.SMART_TEXT_TRANSPORT == "cli":
                return self._generate_via_cli(prompt, system)

        opts = dict(_GEMMA4_DEFAULTS)
        if temperature is not None:
            opts["temperature"] = temperature
        if top_p is not None:
            opts["top_p"] = top_p
        if top_k is not None:
            opts["top_k"] = top_k

        sys_prompt = system or ""
        _is_gemma4 = self.model.lower().startswith(("gemma4", "gemma-4"))
        if self.think_mode and _is_gemma4:
            sys_prompt = "<|think|>" + sys_prompt

        body = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": opts,
        }
        if sys_prompt:
            body["system"] = sys_prompt
        if images:
            body["images"] = images

        try:
            r = requests.post(
                f"{self.host}/api/generate", json=body, timeout=self.timeout,
            )
            r.raise_for_status()
        except (requests.ConnectionError, ConnectionError) as e:
            raise OllamaError(
                f"Ollama not running at {self.host} — start with: ollama serve"
            ) from e
        except requests.Timeout as e:
            raise OllamaError(
                f"Ollama call timed out — model ({self.model}) took too long"
            ) from e
        except requests.HTTPError as e:
            body = e.response.text if e.response is not None else ""
            raise OllamaError(f"Ollama HTTP error: {e} — {body}") from e

        raw = r.json().get("response", "")
        # Strip any Gemma 4 thought blocks before returning.
        stripped = _THOUGHT_RE.sub("", raw).strip()
        return stripped

    def generate_json(self, prompt: str, system: str | None = None,
                      retries: int = OLLAMA_RETRY_MAX,
                      images: list[str] | None = None) -> dict | list:
        sys = (system or "") + "\nRespond with raw JSON only. No prose, no markdown."
        last_err = None
        for attempt in range(1, retries + 1):
            raw = self.generate(prompt, system=sys, images=images)
            cleaned = _FENCE_RE.sub("", raw).strip()
            try:
                return json.loads(cleaned)
            except json.JSONDecodeError as e:
                # Best-effort repair: isolate outermost balanced JSON and
                # strip trailing commas. Catches the common small-LLM
                # failure modes (prose around JSON, ", }" / ", ]").
                repaired = _repair_json(cleaned)
                if repaired != cleaned:
                    try:
                        return json.loads(repaired)
                    except json.JSONDecodeError as e2:
                        last_err = e2
                        log.warning("Ollama JSON parse failed (attempt %d, "
                                    "repair also failed): %s", attempt, e2)
                        continue
                last_err = e
                log.warning("Ollama JSON parse failed (attempt %d): %s", attempt, e)
        raise OllamaError(f"Ollama JSON parse failed after {retries} retries: {last_err}")

    def generate_tool_calls(self, prompt: str, system: str,
                            allowed_tools: set[str],
                            retries: int = OLLAMA_RETRY_MAX) -> list[dict]:
        """Forced-JSON tool-call shim for small models whose native function-
        calling is unreliable. Expects model to return:
            {"calls": [{"tool": "<name>", "args": {...}}, ...]}
        Returns the validated list of calls. Raises OllamaError on:
          - malformed JSON / missing 'calls' key
          - any call referencing a tool not in allowed_tools
          - any call missing 'tool' or 'args'
        """
        result = self.generate_json(prompt, system=system, retries=retries)
        if not isinstance(result, dict) or "calls" not in result:
            raise OllamaError(
                f"tool-call response missing 'calls' key: {result!r}"
            )
        calls = result["calls"]
        if not isinstance(calls, list):
            raise OllamaError(f"'calls' must be a list, got {type(calls)}")
        for c in calls:
            if not isinstance(c, dict) or "tool" not in c or "args" not in c:
                raise OllamaError(f"malformed call entry: {c!r}")
            if c["tool"] not in allowed_tools:
                raise OllamaError(
                    f"unknown tool {c['tool']!r}; allowed={sorted(allowed_tools)}"
                )
        return calls


def smart_client(**kwargs) -> "OllamaClient":
    """OllamaClient bound to the capable model when USE_SMART_TEXT_MODEL, else
    the cheap local model. Use for high-value reasoning (script, judgment)."""
    from video_agent.config import (
        SMART_TEXT_MODEL, OLLAMA_MODEL, USE_SMART_TEXT_MODEL,
    )
    model = SMART_TEXT_MODEL if USE_SMART_TEXT_MODEL else OLLAMA_MODEL
    return OllamaClient(model=model, **kwargs)
