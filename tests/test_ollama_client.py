import json
from unittest.mock import patch

import pytest

from llm.ollama_client import OllamaClient

PLAN = {"age": [18, 120], "sex": [], "death": "any", "op": "or", "rules": []}


class FakeResponse:
    def __init__(self, status_code, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        return self._payload


def _ok():
    return FakeResponse(200, {"message": {"content": json.dumps(PLAN)}})


def test_thinking_is_disabled_by_default():
    with patch("llm.ollama_client.httpx.post", return_value=_ok()) as post:
        OllamaClient("http://x", "qwen3:8b").plan("adults with cancer")

    assert post.call_args[1]["json"]["think"] is False


def test_falls_back_when_the_model_has_no_thinking_mode():
    responses = [
        FakeResponse(400, text="llama3.1:8b does not support thinking"),
        _ok(),
    ]
    with patch("llm.ollama_client.httpx.post", side_effect=responses) as post:
        plan = OllamaClient("http://x", "llama3.1:8b").plan("adults with cancer")

    assert plan == PLAN
    assert "think" not in post.call_args_list[1][1]["json"]


def test_schema_and_zero_temperature_are_sent():
    with patch("llm.ollama_client.httpx.post", return_value=_ok()) as post:
        OllamaClient("http://x", "qwen3:8b").plan("adults with cancer")

    body = post.call_args[1]["json"]
    assert body["stream"] is False
    assert body["options"]["temperature"] == 0
    assert body["format"]["required"] == ["age", "sex", "death", "op", "rules"]


def test_per_request_model_overrides_the_default():
    with patch("llm.ollama_client.httpx.post", return_value=_ok()) as post:
        OllamaClient("http://x", "qwen3:8b").plan("q", model="qwen3:14b")

    assert post.call_args[1]["json"]["model"] == "qwen3:14b"


def test_missing_model_error_names_the_pull_command():
    response = FakeResponse(404, text='{"error":"model \'qwen3:8b\' not found"}')
    with patch("llm.ollama_client.httpx.post", return_value=response):
        with pytest.raises(ValueError, match="ollama pull qwen3:8b"):
            OllamaClient("http://x", "qwen3:8b").plan("q")


def test_non_json_content_raises():
    response = FakeResponse(200, {"message": {"content": "sorry, I cannot"}})
    with patch("llm.ollama_client.httpx.post", return_value=response):
        with pytest.raises(ValueError, match="not valid JSON"):
            OllamaClient("http://x", "qwen3:8b").plan("q")


def test_keep_alive_and_generation_limits_are_sent():
    with patch("llm.ollama_client.httpx.post", return_value=_ok()) as post:
        OllamaClient("http://x", "qwen3:8b", keep_alive="30m").plan("q")

    body = post.call_args[1]["json"]
    assert body["keep_alive"] == "30m"
    assert body["options"]["num_ctx"] == 2048
    assert body["options"]["num_predict"] == 512
