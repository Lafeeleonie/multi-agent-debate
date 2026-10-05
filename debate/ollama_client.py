"""Small local-only HTTP client. Never downloads a model."""

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import requests


class OllamaError(RuntimeError):
    """Actionable error safe to show in the interface."""


def validate_url(url: str) -> str:
    url = url.strip().rstrip("/")
    parsed = urlsplit(url)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
        or parsed.path
    ):
        raise ValueError("Utilisez une URL Ollama sans identifiants, chemin ni paramètres.")
    if parsed.hostname.lower() not in {"localhost", "127.0.0.1", "::1"}:
        raise ValueError(
            "Cette application locale accepte uniquement une adresse de boucle locale."
        )
    try:
        _ = parsed.port
    except ValueError as exc:
        raise ValueError("Le port Ollama est invalide.") from exc
    return url


@dataclass
class ChatResult:
    content: str
    tokens: int | None = None
    done_reason: str | None = None


class OllamaClient:
    def __init__(self, base_url: str = "http://localhost:11434", timeout: float = 600):
        self.base_url = validate_url(base_url)
        self.timeout = timeout
        self._local_models: set[str] = set()

    def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        # Local Ollama must work even if the environment defines a corporate proxy.
        try:
            with requests.Session() as session:
                session.trust_env = False
                response = session.request(
                    method,
                    self.base_url + path,
                    timeout=kwargs.pop("timeout", self.timeout),
                    allow_redirects=False,
                    **kwargs,
                )
                if not response.ok:
                    try:
                        detail = response.json().get("error", "Erreur du serveur Ollama")
                    except (ValueError, AttributeError):
                        detail = "Réponse HTTP inattendue"
                    raise OllamaError(f"Ollama HTTP {response.status_code} : {detail}")
                data = response.json()
        except requests.Timeout as exc:
            raise OllamaError(
                "Ollama a dépassé le délai de génération. Réduisez le contexte ou les tokens."
            ) from exc
        except requests.RequestException as exc:
            raise OllamaError(
                f"Ollama est inaccessible à {self.base_url}. Démarrez l'application Ollama "
                "ou exécutez `ollama serve`, puis vérifiez le port."
            ) from exc
        except ValueError as exc:
            raise OllamaError("Ollama a renvoyé une réponse JSON invalide.") from exc
        if not isinstance(data, dict):
            raise OllamaError("Réponse Ollama inattendue : objet JSON attendu.")
        if data.get("error"):
            raise OllamaError(f"Ollama : {data['error']}")
        return data

    def list_models(self) -> list[str]:
        data = self._request("GET", "/api/tags", timeout=5)
        models = data.get("models")
        if not isinstance(models, list):
            raise OllamaError("La liste des modèles Ollama est invalide.")
        return sorted(
            {
                m["name"]
                for m in models
                if isinstance(m, dict)
                and isinstance(m.get("name"), str)
                and not m.get("remote_host")
                and not m.get("remote_model")
                and not m["name"].endswith((":cloud", "-cloud"))
            }
        )

    def require_model(self, model: str, installed: list[str] | None = None) -> None:
        models = installed if installed is not None else self.list_models()
        if model not in models:
            raise OllamaError(
                f"Modèle absent : {model}. Téléchargez-le vous-même avec `ollama pull {model}`."
            )
        self._require_local(model)

    def _require_local(self, model: str) -> None:
        if model in self._local_models:
            return
        if model.endswith((":cloud", "-cloud")):
            raise OllamaError(
                "Les modèles cloud sont exclus : choisissez un modèle local installé."
            )
        info = self._request("POST", "/api/show", json={"model": model}, timeout=30)
        if info.get("remote_host") or info.get("remote_model"):
            raise OllamaError("Ce modèle utilise un serveur distant. Choisissez un modèle local.")
        self._local_models.add(model)

    def chat(
        self,
        model: str,
        messages: list[dict[str, str]],
        *,
        temperature: float,
        context_tokens: int,
        max_tokens: int,
        schema: dict[str, Any] | None = None,
    ) -> ChatResult:
        self._require_local(model)
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": False,
            "think": False,
            "keep_alive": "5m",
            "options": {
                "temperature": temperature,
                "num_ctx": context_tokens,
                "num_predict": max_tokens,
            },
        }
        if schema is not None:
            payload["format"] = schema
        data = self._request("POST", "/api/chat", json=payload)
        message = data.get("message")
        if not isinstance(message, dict) or not isinstance(message.get("content"), str):
            raise OllamaError("Ollama n'a pas renvoyé de message exploitable.")
        content = message["content"].strip()
        if not content:
            raise OllamaError("Réponse vide. Augmentez la limite de tokens ou changez de modèle.")
        count = data.get("eval_count")
        return ChatResult(
            content, count if isinstance(count, int) else None, data.get("done_reason")
        )
