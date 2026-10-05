from unittest.mock import patch

import pytest
import requests

from debate.ollama_client import OllamaClient, OllamaError, validate_url


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:11434",
        "http://127.0.0.1:11434/",
        "http://[::1]:11434",
    ],
)
def test_loopback_urls_allowed(url):
    assert validate_url(url) == url.rstrip("/")


@pytest.mark.parametrize(
    "url",
    [
        "https://ollama.com",
        "http://user:password@localhost:11434",
        "file:///tmp/ollama",
        "http://localhost:11434/api",
        "http://localhost:11434?token=secret",
        "http://localhost:bad",
        "http://localhost:11434/#x",
    ],
)
def test_cloud_credentials_and_invalid_urls_rejected(url):
    with pytest.raises(ValueError):
        OllamaClient(url)


def test_unavailable_ollama_has_actionable_error():
    with patch("requests.Session.request", side_effect=requests.ConnectionError("offline")):
        with pytest.raises(OllamaError, match="ollama serve"):
            OllamaClient().list_models()


def test_timeout_has_actionable_error():
    with patch("requests.Session.request", side_effect=requests.Timeout()):
        with pytest.raises(OllamaError, match="délai"):
            OllamaClient().list_models()


def test_missing_model_never_downloads():
    with patch("requests.Session.request") as request:
        with pytest.raises(OllamaError, match="ollama pull absent:latest"):
            OllamaClient().require_model("absent:latest", ["installed:latest"])
        request.assert_not_called()


def test_model_list_is_loaded_from_local_api():
    with patch("requests.Session.request") as request:
        request.return_value.ok = True
        request.return_value.json.return_value = {"models": [{"name": "b"}, {"name": "a"}]}
        assert OllamaClient().list_models() == ["a", "b"]
        assert request.call_args.args == ("GET", "http://localhost:11434/api/tags")


def test_model_selector_excludes_remote_entries():
    with patch("requests.Session.request") as request:
        request.return_value.ok = True
        request.return_value.json.return_value = {
            "models": [
                {"name": "local:latest"},
                {"name": "large:cloud"},
                {"name": "disguised:latest", "remote_host": "https://ollama.com"},
            ]
        }
        assert OllamaClient().list_models() == ["local:latest"]


def test_remote_alias_is_rejected_before_sending_discussion():
    with patch("requests.Session.request") as request:
        request.return_value.ok = True
        request.return_value.json.return_value = {"remote_host": "https://ollama.com"}
        with pytest.raises(OllamaError, match="serveur distant"):
            OllamaClient().chat(
                "alias:latest",
                [{"role": "user", "content": "private"}],
                temperature=0.7,
                context_tokens=4096,
                max_tokens=512,
            )
        assert request.call_count == 1
        assert request.call_args.args[1].endswith("/api/show")
        assert request.call_args.kwargs["json"] == {"model": "alias:latest"}
        assert request.call_args.kwargs["allow_redirects"] is False


def test_chat_uses_sequential_local_options_and_schema():
    with patch("requests.Session.request") as request:
        request.return_value.ok = True
        request.return_value.json.return_value = {
            "message": {"content": "Bonjour"},
            "eval_count": 7,
            "done_reason": "stop",
        }
        result = OllamaClient().chat(
            "local:model",
            [{"role": "user", "content": "Bonjour"}],
            temperature=0.7,
            context_tokens=32768,
            max_tokens=1200,
            schema={"type": "object"},
        )
        payload = request.call_args.kwargs["json"]
        assert payload["stream"] is False
        assert payload["think"] is False
        assert payload["options"]["num_ctx"] == 32768
        assert payload["options"]["num_predict"] == 1200
        assert payload["format"] == {"type": "object"}
        assert result.tokens == 7


@pytest.mark.parametrize("data", [{}, {"message": {}}, {"message": {"content": " "}}, []])
def test_malformed_response_is_a_clean_error(data):
    with patch("requests.Session.request") as request:
        request.return_value.ok = True
        request.return_value.json.return_value = data
        with pytest.raises(OllamaError):
            OllamaClient().chat("model", [], temperature=0.7, context_tokens=4096, max_tokens=512)
