"""OllamaClient must pass base64 images through to /api/generate."""
from unittest.mock import patch, MagicMock
from video_agent.ollama_client import OllamaClient


def _fake_response(text='{"ok": true}'):
    r = MagicMock()
    r.json.return_value = {"response": text}
    r.raise_for_status.return_value = None
    return r


def test_generate_includes_images_in_body():
    client = OllamaClient(think_mode=False)
    with patch("video_agent.ollama_client.requests.post",
               return_value=_fake_response("a photo")) as post:
        client.generate("describe", images=["QUJD"])  # base64 "ABC"
    body = post.call_args.kwargs["json"]
    assert body["images"] == ["QUJD"]


def test_generate_omits_images_key_when_none():
    client = OllamaClient(think_mode=False)
    with patch("video_agent.ollama_client.requests.post",
               return_value=_fake_response()) as post:
        client.generate("hi")
    body = post.call_args.kwargs["json"]
    assert "images" not in body


def test_generate_json_forwards_images():
    client = OllamaClient(think_mode=False)
    with patch("video_agent.ollama_client.requests.post",
               return_value=_fake_response('{"score": 8}')) as post:
        out = client.generate_json("grade this", images=["QUJD"])
    assert out == {"score": 8}
    assert post.call_args.kwargs["json"]["images"] == ["QUJD"]
