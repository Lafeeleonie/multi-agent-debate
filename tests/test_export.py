import json

from debate.engine import DebateEngine
from debate.export import export_json, export_markdown
from tests.conftest import FakeClient, turn


def test_json_roundtrip_contains_human_messages_and_control_history(config):
    engine = DebateEngine(config)
    engine.add_human_message("Mon avis : l'écologie compte aussi.")
    engine.update_controls("Français", "Court", "Exemples", "Objections")
    engine.step(FakeClient([turn(note="Mémoire dynamique privée")]))
    data = json.loads(export_json(engine))
    assert data["schema_version"] == 1
    assert data["messages"][0]["speaker_id"] == "human"
    assert "écologie" in data["messages"][0]["content"]
    assert data["parameters"]["constraints"] == "Court"
    assert data["initial_config"]["constraints"] == ""
    assert data["control_events"][0]["after_message"] == 1
    assert data["created_at"]
    assert data["judge_result"] is None
    assert "Mémoire dynamique privée" not in export_json(engine)


def test_markdown_contains_discussion_and_constraints(config):
    engine = DebateEngine(config)
    engine.add_human_message("Voici mon opinion.")
    engine.update_controls("Français", "Rester concis", "", "")
    engine.step(FakeClient([turn("Argument avec accents : liberté.")]))
    text = export_markdown(engine)
    assert "# " + config.topic in text
    assert "Intervention humaine : Vous" in text
    assert "Voici mon opinion." in text
    assert "liberté" in text
    assert "Rester concis" in text
    assert "Modifications des consignes" in text
