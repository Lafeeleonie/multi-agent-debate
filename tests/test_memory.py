import copy

import pytest

from debate.memory import SummaryMemory, clip_text, messages_cost, prepare_context
from debate.models import DebateMessage
from tests.conftest import FakeClient


def history(size=12, length=700):
    return [
        DebateMessage(
            "a" if i % 2 == 0 else "b",
            f"Agent {i % 2}",
            i // 2 + 1,
            f"argument-{i} " + "x" * length,
        )
        for i in range(size)
    ]


def test_context_is_bounded_but_public_history_unchanged(config):
    config.context_tokens = 4096
    config.max_tokens = 512
    public = history()
    before = copy.deepcopy(public)
    result = prepare_context(
        "instructions", "réponds", public, "a", SummaryMemory(), config, FakeClient()
    )
    assert messages_cost(result.messages) <= config.context_tokens - config.max_tokens - 256
    assert result.reduced
    assert public == before
    assert "argument-11" in str(result.messages)
    assert "argument-0 " not in str(result.messages)


def test_compression_preserves_recent_messages(config):
    config.context_tokens = 4096
    config.max_tokens = 512
    config.summary_tokens = 256
    config.compression = True
    config.recent_messages = 2
    public = history()
    client = FakeClient()
    result = prepare_context(
        "instructions", "réponds", public, "a", SummaryMemory(), config, client
    )
    assert result.summary.covered_messages == 10
    assert "argument-10" in str(result.messages)
    assert "argument-11" in str(result.messages)
    assert result.summary.text
    assert len(client.calls) >= 2  # Older messages are summarized in bounded batches.
    for call in client.calls:
        assert (
            messages_cost(call["messages"]) <= config.context_tokens - config.summary_tokens - 256
        )


def test_judge_refuses_silent_truncation(config):
    config.context_tokens = 4096
    config.max_tokens = 512
    with pytest.raises(ValueError, match="contexte du juge"):
        prepare_context(
            "juge",
            "analyse",
            history(),
            None,
            SummaryMemory(),
            config,
            FakeClient(),
            require_complete=True,
        )


def test_judge_can_use_complete_summary_and_recent_messages(config):
    config.context_tokens = 4096
    config.max_tokens = 512
    config.summary_tokens = 256
    config.compression = True
    config.recent_messages = 2
    result = prepare_context(
        "juge",
        "analyse",
        history(),
        None,
        SummaryMemory(),
        config,
        FakeClient(),
        require_complete=True,
    )
    assert result.reduced
    assert result.summary.covered_messages == 10
    assert "argument-11" in str(result.messages)


def test_oversize_prompt_or_last_message_is_not_silently_cut(config):
    config.context_tokens = 4096
    config.max_tokens = 512
    with pytest.raises(ValueError, match="consignes"):
        prepare_context("x" * 4096, "réponds", [], "a", SummaryMemory(), config, FakeClient())
    with pytest.raises(ValueError, match="dernière intervention"):
        prepare_context(
            "instructions", "réponds", history(1, 4096), "a", SummaryMemory(), config, FakeClient()
        )


def test_unicode_clipping_does_not_split_codepoints():
    text = clip_text("🙂é漢字" * 100, 101)
    assert len(text.encode("utf-8")) <= 101
    assert "�" not in text
