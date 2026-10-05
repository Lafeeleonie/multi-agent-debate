import copy
import json

import pytest

from debate.agents import context_messages
from debate.engine import DebateEngine
from debate.export import export_json, export_markdown
from debate.judge import run_judge
from debate.memory import SummaryMemory, estimate_tokens, messages_cost, prepare_context
from debate.models import WebResearch, WebSource
from debate.ollama_client import OllamaError
from debate.web_search import (
    WebSearchClient,
    WebSearchError,
    bound_research,
    plan_queries,
    public_source_url,
)
from tests.conftest import FakeClient, turn


class FakeSearch:
    def __init__(self, fail=False):
        self.queries = []
        self.fail = fail

    def search(self, query, config):
        self.queries.append(query)
        if self.fail:
            raise WebSearchError("Internet indisponible")
        return [
            WebSource(
                "",
                "Source publique",
                "https://example.org/evidence",
                "Données utiles au débat.",
                query,
            )
        ]


def test_agent_plans_search_then_uses_retrieved_evidence(config):
    config.web.enabled = True
    config.agent_a.private_memory = "Une note strictement privée"
    client = FakeClient(['{"queries":["preuve de la proposition"]}', turn("Argument [A1-1]")])
    search = FakeSearch()
    engine = DebateEngine(config)
    message = engine.step(client, search)
    assert search.queries == ["preuve de la proposition"]
    assert message.research.sources[0].source_id == "A1-1"
    assert "https://example.org/evidence" in client.calls[-1]["messages"][-1]["content"]
    assert "Données utiles au débat" in client.calls[-1]["messages"][-1]["content"]
    assert "note strictement privée" not in str(client.calls[0]["messages"])
    assert "note strictement privée" in client.calls[1]["messages"][0]["content"]
    assert client.calls[0]["schema"]["required"] == ["queries"]
    assert engine.agent_turns == 1


@pytest.mark.parametrize("enabled,allowed", [(False, True), (True, False)])
def test_disabled_web_never_plans_or_searches(config, enabled, allowed):
    config.web.enabled = enabled
    config.agent_a.web_access = allowed
    search = FakeSearch()
    client = FakeClient([turn()])
    message = DebateEngine(config).step(client, search)
    assert not search.queries
    assert len(client.calls) == 1
    assert message.research is None


def test_agent_can_decide_no_search_is_needed(config):
    config.web.enabled = True
    search = FakeSearch()
    client = FakeClient(['{"queries":[]}', turn()])
    message = DebateEngine(config).step(client, search)
    assert not search.queries
    assert not message.research.sources
    assert not message.research.errors


def test_web_failure_keeps_debate_functional_and_records_error(config):
    config.web.enabled = True
    client = FakeClient(['{"queries":["preuve"]}', turn()])
    engine = DebateEngine(config)
    message = engine.step(client, FakeSearch(fail=True))
    assert engine.agent_turns == 1
    assert "Internet indisponible" in message.research.errors[0]
    assert not message.research.sources
    assert "Internet indisponible" in client.calls[-1]["messages"][-1]["content"]


@pytest.mark.parametrize(
    "plan",
    [
        "bad JSON",
        '{"queries":null}',
        '{"queries":[42]}',
        OllamaError("Modèle momentanément indisponible"),
    ],
)
def test_invalid_or_failed_plan_does_not_invent_results(config, plan):
    config.web.enabled = True
    search = FakeSearch()
    client = FakeClient([plan, turn()])
    engine = DebateEngine(config)
    message = engine.step(client, search)
    assert engine.agent_turns == 1
    assert message.research.errors
    assert not message.research.sources
    assert not search.queries


def test_query_count_length_and_duplicates_are_limited(config):
    config.web.max_queries = 2
    client = FakeClient([json.dumps({"queries": ["a", "a", "x" * 500, "b", " "]})])
    queries = plan_queries(config, config.agent_a, [], "a", client)
    assert queries == ["a", "x" * 280]


def test_duplicates_across_queries_are_removed(config):
    config.web.enabled = True
    config.web.max_queries = 2
    client = FakeClient(['{"queries":["première", "deuxième"]}', turn()])
    message = DebateEngine(config).step(client, FakeSearch())
    assert len(message.research.sources) == 1


def test_retrieved_sources_remain_user_data_for_both_agents_and_judge(config):
    config.web.enabled = True
    engine = DebateEngine(config)
    engine.step(FakeClient(['{"queries":["preuve"]}', turn("Argument A")]), FakeSearch())
    config_before = copy.deepcopy(engine.private_memories)
    # B is denied new searches but still sees the public evidence obtained by A.
    engine.config.agent_b.web_access = False
    client = FakeClient([turn("Réponse B"), "Rapport"])
    engine.step(client, FakeSearch())
    b_messages = client.calls[0]["messages"]
    assert any("example.org/evidence" in m["content"] and m["role"] == "user" for m in b_messages)
    engine.finish()
    private_before_judge = copy.deepcopy(engine.private_memories)
    run_judge(engine, client)
    assert engine.private_memories == private_before_judge
    assert config_before["a"] == private_before_judge["a"]
    assert any("example.org/evidence" in m["content"] for m in client.calls[-1]["messages"])
    a_context = context_messages(engine.state.messages[0], "a")
    assert a_context[0] == {"role": "assistant", "content": "Argument A"}
    assert a_context[1]["role"] == "user"


def test_web_metadata_is_exported(config):
    config.web.enabled = True
    engine = DebateEngine(config)
    engine.step(FakeClient(['{"queries":["preuve"]}', turn()]), FakeSearch())
    data = json.loads(export_json(engine))
    source = data["messages"][0]["research"]["sources"][0]
    assert source["query"] == "preuve"
    assert source["retrieved_at"]
    assert data["parameters"]["web"]["enabled"]
    markdown = export_markdown(engine)
    assert "Requête : preuve" in markdown
    assert "A1-1" in markdown and "https://example.org/evidence" in markdown


def test_web_evidence_is_bounded_and_preserves_valid_json():
    research = WebResearch(
        queries=["preuve"],
        sources=[
            WebSource(str(i), "Long titre", f"https://example.org/{i}", "x" * 600, "preuve")
            for i in range(5)
        ],
    )
    content = bound_research(research, 1400)
    assert estimate_tokens(content) <= 1400
    assert 0 < len(research.sources) < 5
    assert research.errors
    json.loads(content.split("\n", 1)[1])
    assert bound_research(research, 1) == ""
    assert not research.sources


def test_context_cost_includes_public_research_metadata(config):
    config.web.enabled = True
    config.context_tokens = 4096
    config.max_tokens = 512
    engine = DebateEngine(config)
    engine.step(FakeClient(['{"queries":["preuve"]}', turn()]), FakeSearch())
    result = prepare_context(
        "instructions", "réponds", engine.state.messages, "b", SummaryMemory(), config, FakeClient()
    )
    assert messages_cost(result.messages) <= 4096 - 512 - 256
    assert any(
        m["role"] == "user" and "example.org/evidence" in m["content"] for m in result.messages
    )


def test_web_settings_can_change_during_pause_and_reset_consensus(config):
    engine = DebateEngine(config)
    engine.consensus_streak = 1
    engine.update_controls("Français", "", "", "", web_enabled=True, web_a=False, web_b=True)
    assert engine.config.web.enabled
    assert not engine.config.agent_a.web_access and engine.config.agent_b.web_access
    assert engine.consensus_streak == 0
    assert engine.state.control_events[-1].web_enabled
    assert not engine.state.control_events[-1].web_a


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "file:///etc/passwd",
        "https://localhost/x",
        "http://127.0.0.1/x",
        "http://192.168.1.1",
        "http://user:password@example.org",
        "https://[::1]",
        "https://example.org:bad/x",
    ],
)
def test_nonpublic_source_links_are_rejected(url):
    assert public_source_url(url) is None


def test_source_links_are_validated_and_excerpts_are_limited(config, monkeypatch):
    class Provider:
        def __init__(self, **kwargs):
            assert kwargs["timeout"] == 10

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def text(self, query, **kwargs):
            assert kwargs["max_results"] == 3
            return [
                {"href": "javascript:alert(1)", "title": "bad", "body": "bad"},
                {"href": "https://example.org", "title": "Titre", "body": "é" * 1000},
                {"href": "https://example.org", "title": "Doublon", "body": "idem"},
            ]

    monkeypatch.setattr("ddgs.DDGS", Provider)
    sources = WebSearchClient().search("preuve", config)
    assert len(sources) == 1
    assert len(sources[0].snippet.encode("utf-8")) <= 600


def test_provider_failure_is_an_actionable_web_error(config, monkeypatch):
    from ddgs.exceptions import DDGSException

    def fail(**kwargs):
        raise DDGSException("network down")

    monkeypatch.setattr("ddgs.DDGS", fail)
    with pytest.raises(WebSearchError, match="inaccessible"):
        WebSearchClient().search("preuve", config)
