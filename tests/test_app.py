import time
from pathlib import Path

from streamlit.testing.v1 import AppTest

from debate.models import DEFAULT_MODEL
from debate.ollama_client import OllamaError
from tests.conftest import FakeClient, turn
from tests.test_web_search import FakeSearch

APP_PATH = Path(__file__).resolve().parents[1] / "app.py"


def mock_models(monkeypatch):
    monkeypatch.setattr(
        "debate.ollama_client.OllamaClient.list_models",
        lambda self: [DEFAULT_MODEL, "autre:latest"],
    )
    monkeypatch.setattr(
        "debate.ollama_client.OllamaClient._require_local", lambda self, model: None
    )


def button(app, label):
    return next(b for b in app.button if b.label == label)


def wait_for_reply(app):
    runner = app.session_state.runner
    deadline = time.monotonic() + 3
    while runner.future is not None and not runner.future.done() and time.monotonic() < deadline:
        time.sleep(0.01)
    app.run()
    assert not app.exception


def test_interface_model_selector_and_missing_ollama(monkeypatch):
    mock_models(monkeypatch)
    app = AppTest.from_file(APP_PATH, default_timeout=10).run()
    assert not app.exception
    assert app.selectbox(key="cfg_model").options == [DEFAULT_MODEL, "autre:latest"]
    assert not button(app, "Lancer").disabled

    def offline(self):
        raise OllamaError("Ollama inaccessible : ollama serve")

    monkeypatch.setattr("debate.ollama_client.OllamaClient.list_models", offline)
    # Change the cache key rather than depend on another app's global cache.
    app.text_input(key="cfg_ollama_url").set_value("http://127.0.0.1:11434").run()
    assert not app.exception
    assert any("ollama serve" in err.value for err in app.error)


def test_human_participation_and_live_constraints_in_ui(monkeypatch):
    mock_models(monkeypatch)
    client = FakeClient([turn("Première réponse"), turn("Seconde réponse")])
    monkeypatch.setattr("debate.runner.OllamaClient", lambda url: client)
    app = AppTest.from_file(APP_PATH, default_timeout=10).run()
    button(app, "Lancer").click().run()
    wait_for_reply(app)
    assert app.session_state.runner.engine.agent_turns == 1
    app.chat_input[0].set_value("Voici mon avis humain.").run()
    assert not app.exception
    assert app.session_state.runner.engine.state.messages[-1].speaker_id == "human"
    next(w for w in app.text_input if w.label == "Langue pour les prochaines réponses").set_value(
        "English"
    )
    next(w for w in app.text_area if w.label == "Contraintes pour tous les agents").set_value(
        "At most 100 words"
    )
    button(app, "Appliquer les consignes").click().run()
    assert not app.exception
    button(app, "Réponse suivante / reprendre").click().run()
    wait_for_reply(app)
    prompt = client.calls[-1]["messages"][0]["content"]
    assert "English" in prompt and "At most 100 words" in prompt
    assert any("Voici mon avis humain." in m["content"] for m in client.calls[-1]["messages"])


def test_interface_web_controls_and_source_display(monkeypatch):
    mock_models(monkeypatch)
    client = FakeClient(['{"queries":["preuve"]}', turn("Argument sourcé [A1-1]")])
    monkeypatch.setattr("debate.runner.OllamaClient", lambda url: client)
    search = FakeSearch()
    monkeypatch.setattr("debate.engine.WebSearchClient", lambda: search)
    app = AppTest.from_file(APP_PATH, default_timeout=10).run()
    app.checkbox(key="cfg_web_enabled").check().run()
    button(app, "Lancer").click().run()
    wait_for_reply(app)
    assert not app.exception
    assert search.queries == ["preuve"]
    assert any("Requêtes : preuve" in text.value for text in app.markdown)
    assert app.session_state.runner.engine.state.messages[0].research.sources
    next(w for w in app.checkbox if w.label == "Accès web de B").uncheck()
    button(app, "Appliquer les consignes").click().run()
    assert not app.session_state.runner.engine.config.agent_b.web_access
