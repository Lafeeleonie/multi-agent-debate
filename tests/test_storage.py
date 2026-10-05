import copy
import json
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path
from uuid import uuid4

import pytest

from debate.engine import DebateEngine
from debate.export import export_json
from debate.judge import run_judge
from debate.memory import SummaryMemory
from debate.models import WebResearch, WebSource
from debate.runner import DebateRunner
from debate.storage import (
    MAX_FILE_BYTES,
    ConversationStore,
    HistoryError,
    checkpoint_json,
    restore_json,
)
from tests.conftest import FakeClient, turn


def populated_engine(config):
    engine = DebateEngine(config)
    engine.add_human_message("Mon avis initial.")
    engine.step(FakeClient([turn("Position A", vote=False, note="Mémoire privée de A")]))
    engine.step(FakeClient([turn("Position B", vote=False, note="Mémoire privée de B")]))
    engine.step(FakeClient([turn("Réponse suivante A", vote=False, note="Mémoire actualisée A")]))
    engine.summary = SummaryMemory("Résumé des deux premiers messages", 2)
    engine.state.messages[1].research = WebResearch(
        queries=["preuve"],
        sources=[
            WebSource("A1-1", "Exemple", "https://example.org", "Donnée publique.", "preuve"),
        ],
    )
    return engine


def test_complete_checkpoint_restores_every_resume_field(config):
    engine = populated_engine(config)
    restored = restore_json(checkpoint_json(engine))
    assert asdict(restored.state) == asdict(engine.state)
    assert restored.config == engine.config
    assert restored.initial_config == engine.initial_config
    assert restored.private_memories == engine.private_memories
    assert restored.summary == engine.summary
    assert restored.consensus_streak == 1
    assert restored.round_votes == {"a": False}
    assert restored.next_speaker == "b" and restored.next_round == 2
    restored.step(FakeClient([turn(vote=False)]))
    assert restored.finished and "Consensus" in restored.state.stop_reason


def test_changed_controls_and_human_votes_are_preserved(config):
    engine = populated_engine(config)
    engine.update_controls("English", "Keep it short", "", "", web_enabled=True, web_b=False)
    engine.add_human_message("Nouvelle objection.")
    restored = restore_json(checkpoint_json(engine))
    assert restored.config.language == "English" and restored.config.web.enabled
    assert not restored.config.agent_b.web_access
    assert restored.state.control_events == engine.state.control_events
    assert restored.consensus_streak == 0 and restored.round_votes == {}
    restored.step(FakeClient([turn(vote=False)]))
    assert not restored.finished


def test_judge_is_restored_without_changing_private_memories(config):
    engine = populated_engine(config)
    engine.finish()
    run_judge(engine, FakeClient(["Rapport du juge"]))
    restored = restore_json(checkpoint_json(engine))
    assert restored.finished
    assert restored.state.judge_result.content == "Rapport du juge"
    assert restored.private_memories == engine.private_memories


def test_shared_export_and_old_export_can_be_imported(config):
    engine = populated_engine(config)
    payload = json.loads(export_json(engine))
    payload.pop("conversation_id")
    for data in (payload["parameters"], payload["initial_config"]):
        data.pop("web")
        data["agent_a"].pop("web_access")
        data["agent_b"].pop("web_access")
    restored = restore_json(json.dumps(payload))
    assert restored.state.messages == engine.state.messages
    assert restored.next_speaker == "b"
    assert restored.consensus_streak == 0 and not restored.round_votes
    assert restored.private_memories["a"] == config.agent_a.private_memory
    assert any("mémoires privées dynamiques" in warning for warning in restored.state.warnings)
    assert "Mémoire actualisée A" not in export_json(engine)


def test_imported_backup_gets_a_new_identity(config):
    engine = populated_engine(config)
    restored = restore_json(checkpoint_json(engine), new_identity=True)
    assert restored.state.conversation_id != engine.state.conversation_id
    assert restored.private_memories == engine.private_memories


def test_local_store_saves_and_lists_debates_with_identical_subjects(config, tmp_path):
    store = ConversationStore(tmp_path / "history")
    first, second = populated_engine(config), DebateEngine(config)
    store.save(first)
    store.save(second)
    listed = store.list_conversations()
    assert {entry.conversation_id for entry in listed} == {
        first.state.conversation_id,
        second.state.conversation_id,
    }
    assert len(list(store.directory.glob("*.json"))) == 2
    assert store.load(first.state.conversation_id).private_memories == first.private_memories
    assert not list(store.directory.glob("*.tmp"))


def test_failed_atomic_replace_preserves_previous_backup(config, tmp_path, monkeypatch):
    store = ConversationStore(tmp_path)
    engine = DebateEngine(config)
    store.save(engine)
    path = tmp_path / f"{engine.state.conversation_id}.json"
    previous = path.read_bytes()
    engine.add_human_message("Une nouvelle intervention.")

    def fail(*args):
        raise PermissionError("locked")

    monkeypatch.setattr("debate.storage.os.replace", fail)
    with pytest.raises(HistoryError, match="sauvegarder"):
        store.save(engine)
    assert path.read_bytes() == previous
    assert not list(tmp_path.glob("*.tmp"))
    assert not store.load(engine.state.conversation_id).state.messages


def test_invalid_history_file_is_reported_without_hiding_valid_debates(config, tmp_path):
    store = ConversationStore(tmp_path)
    engine = DebateEngine(config)
    store.save(engine)
    (tmp_path / f"{uuid4()}.json").write_text("broken", encoding="utf-8")
    assert len(store.list_conversations()) == 1
    assert store.warnings


@pytest.mark.parametrize("identity", ["../outside", "..\\outside", "not-a-uuid"])
def test_conversation_identity_cannot_escape_history_directory(tmp_path, identity):
    with pytest.raises(HistoryError, match="Identifiant"):
        ConversationStore(tmp_path).load(identity)


@pytest.mark.parametrize(
    "field,value",
    [
        ("schema_version", 99),
        ("schema_version", True),
        ("parameters", []),
        ("messages", "invalid"),
        ("conversation_id", "../../outside"),
        ("created_at", "invalid-date"),
        ("metadata", []),
    ],
)
def test_malformed_import_is_rejected(config, field, value):
    payload = json.loads(checkpoint_json(DebateEngine(config)))
    payload[field] = value
    with pytest.raises(HistoryError):
        restore_json(json.dumps(payload))


@pytest.mark.parametrize(
    "change",
    [
        lambda p: p["parameters"].update(max_rounds="10"),
        lambda p: p["parameters"]["agent_a"].update(temperature=float("nan")),
        lambda p: p["parameters"]["agent_a"].update(temperature=10**400),
        lambda p: p["parameters"].update(ollama_url="https://ollama.com"),
        lambda p: p["messages"][0].update(speaker_id="judge"),
        lambda p: p["messages"][1].update(round_number=99),
        lambda p: p["metadata"].update(agent_turns=88),
        lambda p: p["metadata"]["summary"].update(covered_messages=999),
        lambda p: p["checkpoint"].update(round_votes={"b": False}),
        lambda p: p["checkpoint"].update(consensus_streak=-1),
        lambda p: p["checkpoint"].update(private_memories={"a": "note"}),
        lambda p: p["messages"][1]["research"]["sources"][0].update(url="javascript:alert(1)"),
    ],
)
def test_incoherent_or_unsafe_resume_state_is_rejected(config, change):
    payload = json.loads(checkpoint_json(populated_engine(config)))
    change(payload)
    with pytest.raises(HistoryError):
        restore_json(json.dumps(payload))


def test_large_or_corrupt_json_is_rejected():
    for data in (b"x" * (MAX_FILE_BYTES + 1), b"broken", b"\xff"):
        with pytest.raises(HistoryError):
            restore_json(data)


def test_finished_debate_can_be_extended_without_losing_history(config):
    config.max_rounds = 1
    engine = DebateEngine(config)
    client = FakeClient([turn(), turn(), turn()])
    engine.step(client)
    engine.step(client)
    restored = restore_json(checkpoint_json(engine))
    before = copy.deepcopy(restored.state.messages)
    restored.reopen(3)
    assert restored.config.max_rounds == 4
    assert restored.next_round == 2 and restored.next_speaker == "a"
    assert restored.state.messages == before
    assert not restored.finished
    restored.step(client)
    assert len(restored.state.messages) == 3


def test_resume_survives_a_new_python_process(config, tmp_path):
    engine = populated_engine(config)
    store = ConversationStore(tmp_path)
    store.save(engine)
    code = (
        "import sys; from pathlib import Path; from debate.storage import ConversationStore; "
        "from tests.conftest import FakeClient, turn; "
        "e = ConversationStore(Path(sys.argv[1])).load(sys.argv[2]); "
        "assert e.next_speaker == 'b'; assert e.private_memories['a'] == 'Mémoire actualisée A'; "
        "e.step(FakeClient([turn(vote=False)])); assert e.finished; print('restored-and-resumed')"
    )
    result = subprocess.run(
        [sys.executable, "-c", code, str(tmp_path), engine.state.conversation_id],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        timeout=10,
        check=True,
    )
    assert "restored-and-resumed" in result.stdout


def test_background_reply_is_saved_even_without_ui_polling(config, tmp_path, monkeypatch):
    client = FakeClient([turn(note="Mémoire sauvegardée")])
    monkeypatch.setattr("debate.runner.OllamaClient", lambda url: client)
    store = ConversationStore(tmp_path)
    runner = DebateRunner(DebateEngine(config), store)
    runner.advance(automatic=True)
    runner.future.result(timeout=3)
    # Visible state hasn't been installed, but disk has the complete response.
    assert runner.engine.agent_turns == 0
    restored = store.load(runner.engine.state.conversation_id)
    assert restored.agent_turns == 1 and restored.next_speaker == "b"
    assert restored.private_memories["a"] == "Mémoire sauvegardée"


def test_disk_failure_never_discards_a_generated_response(config, monkeypatch):
    client = FakeClient([turn()])
    monkeypatch.setattr("debate.runner.OllamaClient", lambda url: client)

    class FailingStore:
        def save(self, engine):
            raise HistoryError("Disque indisponible")

    runner = DebateRunner(DebateEngine(config), FailingStore())
    runner.advance()
    runner.future.result(timeout=3)
    runner.poll()
    assert runner.engine.agent_turns == 1
    assert runner.storage_error == "Disque indisponible"
    assert runner.error is None
