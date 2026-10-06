import time
from pathlib import Path

from streamlit.testing.v1 import AppTest

from debate.engine import DebateEngine
from debate.models import DEFAULT_MODEL
from debate.ollama_client import OllamaError
from debate.storage import ConversationStore, checkpoint_json
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
    button(app, "Reprendre").click().run()
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


def test_new_session_restores_history_in_pause_and_resumes_next_agent(monkeypatch, config):
    mock_models(monkeypatch)
    engine = DebateEngine(config)
    engine.step(FakeClient([turn("Premier argument", note="Souvenir privé de A")]))
    engine.add_human_message("Mon objection conservée.")
    engine.update_controls("English", "Keep it brief", "", "", web_b=False)
    store = ConversationStore()
    store.save(engine)
    client = FakeClient([turn("Second argument")])
    monkeypatch.setattr("debate.runner.OllamaClient", lambda url: client)

    app = AppTest.from_file(APP_PATH, default_timeout=10).run()
    button(app, "Restaurer la conversation").click().run()
    assert not app.exception
    restored = app.session_state.runner
    assert not restored.running and not restored.busy and not client.calls
    assert not app.toggle(key="automatic").value
    assert app.text_input(key="cfg_language").value == "English"
    assert restored.engine.private_memories["a"] == "Souvenir privé de A"
    assert restored.engine.next_speaker == "b"
    assert len(app.chat_message) == 2

    button(app, "Reprendre").click().run()
    wait_for_reply(app)
    assert app.session_state.runner.engine.state.messages[-1].speaker_id == "b"
    prompt = client.calls[0]["messages"][0]["content"]
    assert "English" in prompt and "Keep it brief" in prompt
    assert any("Mon objection conservée." in m["content"] for m in client.calls[0]["messages"])
    assert "Souvenir privé de A" not in str(client.calls)
    assert store.load(engine.state.conversation_id).agent_turns == 2


def test_ui_reset_preserves_human_messages_and_controls_in_history(monkeypatch):
    mock_models(monkeypatch)
    client = FakeClient([turn("Réponse enregistrée")])
    monkeypatch.setattr("debate.runner.OllamaClient", lambda url: client)
    app = AppTest.from_file(APP_PATH, default_timeout=10).run()
    button(app, "Lancer").click().run()
    wait_for_reply(app)
    identity = app.session_state.runner.engine.state.conversation_id
    app.chat_input[0].set_value("Mon avis sauvegardé.").run()
    next(w for w in app.text_input if w.label == "Langue pour les prochaines réponses").set_value(
        "Español"
    )
    button(app, "Appliquer les consignes").click().run()
    button(app, "Réinitialiser").click().run()
    assert not app.exception
    assert "runner" not in app.session_state
    saved = ConversationStore().load(identity)
    assert saved.state.messages[-1].content == "Mon avis sauvegardé."
    assert saved.config.language == "Español"
    assert len(saved.state.control_events) == 1

    reopened = AppTest.from_file(APP_PATH, default_timeout=10).run()
    button(reopened, "Restaurer la conversation").click().run()
    assert not reopened.exception
    assert reopened.session_state.runner.engine.state.conversation_id == identity
    assert reopened.session_state.runner.engine.state.messages == saved.state.messages


def test_finished_history_restores_without_automatic_judge_and_can_be_extended(monkeypatch, config):
    mock_models(monkeypatch)
    config.max_rounds = 1
    config.judge.enabled = True
    engine = DebateEngine(config)
    engine.step(FakeClient([turn()]))
    engine.step(FakeClient([turn()]))
    ConversationStore().save(engine)
    client = FakeClient([turn("Nouvelle intervention")])
    monkeypatch.setattr("debate.runner.OllamaClient", lambda url: client)
    app = AppTest.from_file(APP_PATH, default_timeout=10).run()
    button(app, "Restaurer la conversation").click().run()
    app.run()
    assert not app.exception
    assert app.session_state.runner.engine.finished
    assert not client.calls and not app.session_state.runner.busy
    assert button(app, "Reprendre").disabled

    app.number_input(key="additional_rounds").set_value(2)
    button(app, "Rouvrir le débat en pause").click().run()
    assert not app.exception
    assert not app.session_state.runner.engine.finished
    assert app.session_state.runner.engine.config.max_rounds == 3
    assert not client.calls
    button(app, "Reprendre").click().run()
    wait_for_reply(app)
    assert app.session_state.runner.engine.agent_turns == 3
    assert app.session_state.runner.engine.state.messages[-1].speaker_id == "a"


def test_import_button_restores_a_complete_backup_as_a_separate_conversation(monkeypatch, config):
    mock_models(monkeypatch)
    engine = DebateEngine(config)
    engine.step(FakeClient([turn(note="Note de sauvegarde")]))
    store = ConversationStore()
    store.save(engine)

    class UploadedBackup:
        data = checkpoint_json(engine).encode("utf-8")
        size = len(data)

        def getvalue(self):
            return self.data

    monkeypatch.setattr("streamlit.file_uploader", lambda *args, **kwargs: UploadedBackup())
    client = FakeClient()
    monkeypatch.setattr("debate.runner.OllamaClient", lambda url: client)
    app = AppTest.from_file(APP_PATH, default_timeout=10).run()
    button(app, "Restaurer le fichier JSON").click().run()
    assert not app.exception
    restored = app.session_state.runner.engine
    assert restored.state.conversation_id != engine.state.conversation_id
    assert restored.private_memories["a"] == "Note de sauvegarde"
    assert not app.session_state.runner.running and not client.calls
    assert len(store.list_conversations()) == 2
