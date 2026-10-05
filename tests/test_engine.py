import copy

import pytest

from debate.engine import DebateEngine
from debate.judge import run_judge
from debate.ollama_client import OllamaError
from tests.conftest import FakeClient, turn


def test_agent_contexts_do_not_impersonate_opponent(config):
    engine = DebateEngine(config)
    client = FakeClient([turn("Argument A"), turn("Argument B"), turn("Réplique A")])
    engine.step(client)
    engine.step(client)
    engine.step(client)
    b_messages = client.calls[1]["messages"]
    assert all(m["role"] != "assistant" for m in b_messages)
    assert any('"message": "Argument A"' in m["content"] for m in b_messages)
    a_messages = client.calls[2]["messages"]
    assert [m["content"] for m in a_messages if m["role"] == "assistant"] == ["Argument A"]
    assert any('"message": "Argument B"' in m["content"] for m in a_messages)


def test_private_memory_is_separate(config):
    config.agent_a.private_memory = "Uniquement pour A"
    config.agent_b.private_memory = "Uniquement pour B"
    engine = DebateEngine(config)
    client = FakeClient([turn(note="Souvenir A"), turn(note="Souvenir B"), turn()])
    engine.step(client)
    engine.step(client)
    engine.step(client)
    assert "Uniquement pour B" not in client.calls[0]["messages"][0]["content"]
    assert "Souvenir A" not in client.calls[1]["messages"][0]["content"]
    assert "Souvenir A" in client.calls[2]["messages"][0]["content"]
    assert "Souvenir B" not in client.calls[2]["messages"][0]["content"]


def test_human_message_visible_to_both_and_does_not_consume_turn(config):
    engine = DebateEngine(config)
    engine.add_human_message("Mon avis est différent.")
    client = FakeClient([turn(), turn()])
    engine.step(client)
    engine.step(client)
    assert engine.agent_turns == 2
    assert [m.speaker_id for m in engine.state.messages] == ["human", "a", "b"]
    for call in client.calls:
        assert any(
            "Participant humain" in m["content"]
            and "Mon avis" in m["content"]
            and m["role"] == "user"
            for m in call["messages"]
        )


def test_live_language_and_constraints_reach_agents_and_judge(config):
    engine = DebateEngine(config)
    engine.update_controls("Español", "100 palabras", "Un ejemplo", "Una objeción")
    client = FakeClient([turn(), turn(), "Veredicto"])
    engine.step(client)
    engine.step(client)
    engine.finish()
    run_judge(engine, client)
    for call in client.calls:
        assert "LANGUE OBLIGATOIRE : Español" in call["messages"][0]["content"]
        assert "100 palabras" in call["messages"][0]["content"]
    assert "Un ejemplo" in client.calls[0]["messages"][0]["content"]
    assert "Una objeción" in client.calls[1]["messages"][0]["content"]
    assert engine.initial_config.language == "Français"
    assert len(engine.state.control_events) == 1


def test_consensus_requires_both_agents_over_repeated_rounds(config):
    engine = DebateEngine(config)
    client = FakeClient([turn(vote=False)] * 4)
    for _ in range(3):
        engine.step(client)
        assert not engine.finished
    engine.step(client)
    assert engine.finished
    assert "Consensus" in engine.state.stop_reason


def test_human_intervention_resets_consensus(config):
    engine = DebateEngine(config)
    client = FakeClient([turn(vote=False)] * 6)
    engine.step(client)
    engine.step(client)
    assert engine.consensus_streak == 1
    engine.add_human_message("Une nouvelle objection.")
    engine.step(client)
    engine.step(client)
    assert not engine.finished
    engine.step(client)
    engine.step(client)
    assert engine.finished


def test_mid_round_intervention_invalidates_previous_vote(config):
    config.consensus_rounds = 1
    engine = DebateEngine(config)
    client = FakeClient([turn(vote=False)] * 2)
    engine.step(client)
    engine.add_human_message("Reconsidérez cette objection.")
    engine.step(client)
    assert not engine.finished


def test_live_constraints_reset_consensus(config):
    engine = DebateEngine(config)
    client = FakeClient([turn(vote=False)] * 2)
    engine.step(client)
    engine.step(client)
    engine.update_controls("Français", "Nouveau cas à traiter", "", "")
    assert engine.consensus_streak == 0
    assert not engine.round_votes


def test_stop_word_in_prose_cannot_stop_plain_debate(config):
    config.early_stop = False
    config.max_rounds = 1
    engine = DebateEngine(config)
    client = FakeClient(["STOP continue_debate: false", "Autre avis"])
    engine.step(client)
    assert not engine.finished
    assert client.calls[0]["schema"] is None
    engine.step(client)
    assert engine.state.stop_reason == "Nombre maximal de tours atteint"


@pytest.mark.parametrize(
    "reply",
    [
        "pas du JSON",
        "[]",
        '{"response":"", "continue_debate":false, "private_note":""}',
        '{"response":"ok", "continue_debate":"false", "private_note":""}',
        '{"response":"ok", "continue_debate":0, "private_note":""}',
    ],
)
def test_invalid_structured_reply_does_not_consume_turn(config, reply):
    engine = DebateEngine(config)
    with pytest.raises(OllamaError):
        engine.step(FakeClient([reply]))
    assert engine.agent_turns == 0
    assert not engine.state.messages
    assert engine.next_speaker == "a"


def test_network_failure_preserves_state_and_allows_retry(config):
    engine = DebateEngine(config)
    before = copy.deepcopy(engine.state)
    client = FakeClient([OllamaError("indisponible"), turn()])
    with pytest.raises(OllamaError):
        engine.step(client)
    assert engine.state == before
    engine.step(client)
    assert engine.agent_turns == 1


def test_judge_does_not_modify_agents_or_public_history(config):
    engine = DebateEngine(config)
    client = FakeClient([turn(note="Mémoire confidentielle A"), turn(), "Rapport du juge"])
    engine.step(client)
    engine.step(client)
    engine.finish()
    before = copy.deepcopy((engine.state.messages, engine.private_memories, engine.summary))
    run_judge(engine, client)
    assert (engine.state.messages, engine.private_memories, engine.summary) == before
    judge_messages = client.calls[-1]["messages"]
    assert not any(m["role"] == "assistant" for m in judge_messages)
    assert not any("Mémoire confidentielle A" in m["content"] for m in judge_messages)
    assert engine.state.judge_result.content == "Rapport du juge"


@pytest.mark.parametrize(
    "field,value",
    [
        ("language", " "),
        ("topic", ""),
        ("max_rounds", 0),
        ("max_tokens", 9000),
        ("context_tokens", 1024),
        ("max_tokens", 4096),
    ],
)
def test_configuration_validation(config, field, value):
    if field == "max_tokens" and value == 4096:
        config.context_tokens = 4096
    setattr(config, field, value)
    with pytest.raises(ValueError):
        DebateEngine(config)
