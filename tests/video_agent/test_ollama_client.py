import pytest
import responses
from video_agent.ollama_client import OllamaClient, OllamaError
from video_agent.config import OLLAMA_HOST


@responses.activate
def test_generate_returns_response_text():
    responses.add(
        responses.POST,
        f"{OLLAMA_HOST}/api/generate",
        json={"response": "hello world", "done": True},
        status=200,
    )
    client = OllamaClient()
    out = client.generate("hi", system="be brief")
    assert out == "hello world"


@responses.activate
def test_generate_json_parses():
    responses.add(
        responses.POST,
        f"{OLLAMA_HOST}/api/generate",
        json={"response": '{"key": "val"}', "done": True},
        status=200,
    )
    client = OllamaClient()
    out = client.generate_json("get json", system="json only")
    assert out == {"key": "val"}


@responses.activate
def test_generate_json_strips_markdown_fence():
    responses.add(
        responses.POST,
        f"{OLLAMA_HOST}/api/generate",
        json={"response": '```json\n{"a":1}\n```', "done": True},
        status=200,
    )
    client = OllamaClient()
    assert client.generate_json("x") == {"a": 1}


@responses.activate
def test_generate_raises_when_unreachable():
    responses.add(
        responses.POST,
        f"{OLLAMA_HOST}/api/generate",
        body=ConnectionError("boom"),
    )
    client = OllamaClient()
    with pytest.raises(OllamaError, match="not running"):
        client.generate("hi")


def test_sampling_defaults_are_gemma4_values(monkeypatch):
    """generate() should send temp=1.0, top_p=0.95, top_k=64 when caller doesn't override."""
    from video_agent.ollama_client import OllamaClient
    captured = {}

    class _FakeResp:
        def raise_for_status(self): pass
        def json(self): return {"response": "ok"}

    def fake_post(url, json, timeout):
        captured["body"] = json
        return _FakeResp()

    monkeypatch.setattr("video_agent.ollama_client.requests.post", fake_post)
    OllamaClient(think_mode=False).generate("hi")
    opts = captured["body"]["options"]
    assert opts["temperature"] == 1.0
    assert opts["top_p"] == 0.95
    assert opts["top_k"] == 64


def test_think_mode_prepends_think_token(monkeypatch):
    from video_agent.ollama_client import OllamaClient
    captured = {}

    class _FakeResp:
        def raise_for_status(self): pass
        def json(self): return {"response": "answer"}

    def fake_post(url, json, timeout):
        captured["body"] = json
        return _FakeResp()

    monkeypatch.setattr("video_agent.ollama_client.requests.post", fake_post)
    OllamaClient(model="gemma4:12b", think_mode=True).generate("hi", system="you are a helper")
    assert captured["body"]["system"].startswith("<|think|>")
    assert "you are a helper" in captured["body"]["system"]


def test_thoughts_are_stripped_before_returning(monkeypatch):
    from video_agent.ollama_client import OllamaClient

    class _FakeResp:
        def raise_for_status(self): pass
        def json(self):
            return {"response":
                    "<|channel>thought\nI should answer politely<channel|>Hello!"}

    monkeypatch.setattr("video_agent.ollama_client.requests.post",
                        lambda url, json, timeout: _FakeResp())
    out = OllamaClient(think_mode=True).generate("hi")
    assert out == "Hello!"
    assert "thought" not in out.lower()


def test_generate_tool_calls_rejects_unknown_tool(monkeypatch):
    from video_agent.ollama_client import OllamaClient, OllamaError

    class _FakeResp:
        def raise_for_status(self): pass
        def json(self):
            return {"response":
                    '{"calls":[{"tool":"evil_tool","args":{}}]}'}

    monkeypatch.setattr("video_agent.ollama_client.requests.post",
                        lambda url, json, timeout: _FakeResp())
    client = OllamaClient(think_mode=False)
    import pytest
    with pytest.raises(OllamaError, match="unknown tool"):
        client.generate_tool_calls("p", system="s",
                                   allowed_tools={"set_color_grade"})


def test_generate_tool_calls_returns_valid_calls(monkeypatch):
    from video_agent.ollama_client import OllamaClient

    class _FakeResp:
        def raise_for_status(self): pass
        def json(self):
            return {"response":
                    '{"calls":[{"tool":"set_color_grade",'
                    '"args":{"scene":0,"palette":"red_tension"}}]}'}

    monkeypatch.setattr("video_agent.ollama_client.requests.post",
                        lambda url, json, timeout: _FakeResp())
    client = OllamaClient(think_mode=False)
    calls = client.generate_tool_calls("p", system="s",
                                       allowed_tools={"set_color_grade"})
    assert calls == [{"tool": "set_color_grade",
                      "args": {"scene": 0, "palette": "red_tension"}}]


def test_think_mode_skipped_for_gemma3(monkeypatch):
    """think_mode=True should NOT prepend <|think|> for gemma3 models."""
    from video_agent.ollama_client import OllamaClient
    captured = {}

    class _FakeResp:
        def raise_for_status(self): pass
        def json(self): return {"response": "ok"}

    def fake_post(url, json, timeout):
        captured["body"] = json
        return _FakeResp()

    monkeypatch.setattr("video_agent.ollama_client.requests.post", fake_post)
    OllamaClient(model="gemma3:4b", think_mode=True).generate("hi", system="sys")
    assert not captured["body"]["system"].startswith("<|think|>")


def test_think_mode_applied_for_gemma4(monkeypatch):
    """think_mode=True SHOULD prepend <|think|> for gemma4 models."""
    from video_agent.ollama_client import OllamaClient
    captured = {}

    class _FakeResp:
        def raise_for_status(self): pass
        def json(self): return {"response": "ok"}

    def fake_post(url, json, timeout):
        captured["body"] = json
        return _FakeResp()

    monkeypatch.setattr("video_agent.ollama_client.requests.post", fake_post)
    OllamaClient(model="gemma4:12b", think_mode=True).generate("hi", system="sys")
    assert captured["body"]["system"].startswith("<|think|>")


def test_think_mode_off_skips_prefix_even_for_gemma4(monkeypatch):
    """think_mode=False should NOT prepend <|think|> even for gemma4 models."""
    from video_agent.ollama_client import OllamaClient
    captured = {}

    class _FakeResp:
        def raise_for_status(self): pass
        def json(self): return {"response": "ok"}

    def fake_post(url, json, timeout):
        captured["body"] = json
        return _FakeResp()

    monkeypatch.setattr("video_agent.ollama_client.requests.post", fake_post)
    OllamaClient(model="gemma4:12b", think_mode=False).generate("hi", system="sys")
    assert not captured["body"]["system"].startswith("<|think|>")
