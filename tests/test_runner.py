import threading
import time

from debate.engine import DebateEngine
from debate.ollama_client import OllamaError
from debate.runner import DebateRunner
from tests.conftest import FakeClient, turn


def wait_for_result(runner):
    assert runner.future is not None
    runner.future.result(timeout=3)
    runner.poll()


def test_pause_keeps_inflight_response_without_starting_next(config, monkeypatch):
    entered, release = threading.Event(), threading.Event()

    class SlowClient(FakeClient):
        def chat(self, *args, **kwargs):
            entered.set()
            assert release.wait(3)
            return super().chat(*args, **kwargs)

    client = SlowClient([turn()])
    monkeypatch.setattr("debate.runner.OllamaClient", lambda url: client)
    runner = DebateRunner(DebateEngine(config))
    runner.advance(automatic=True)
    assert entered.wait(3)
    runner.pause()
    assert not runner.engine.state.messages
    release.set()
    wait_for_result(runner)
    assert runner.engine.agent_turns == 1
    assert not runner.busy and not runner.running
    assert len(client.calls) == 1


def test_stop_retains_completed_response(config, monkeypatch):
    client = FakeClient([turn()])
    monkeypatch.setattr("debate.runner.OllamaClient", lambda url: client)
    runner = DebateRunner(DebateEngine(config))
    runner.advance(automatic=True)
    runner.stop()
    wait_for_result(runner)
    assert runner.engine.finished
    assert runner.engine.agent_turns == 1
    assert not runner.busy


def test_runner_error_does_not_discard_previous_history(config, monkeypatch):
    client = FakeClient([turn(), OllamaError("hors ligne"), turn()])
    monkeypatch.setattr("debate.runner.OllamaClient", lambda url: client)
    runner = DebateRunner(DebateEngine(config))
    runner.advance()
    wait_for_result(runner)
    runner.advance()
    deadline = time.monotonic() + 3
    while not runner.future.done() and time.monotonic() < deadline:
        time.sleep(0.01)
    runner.poll()
    assert runner.error == "hors ligne"
    assert runner.engine.agent_turns == 1
    runner.advance()
    wait_for_result(runner)
    assert runner.engine.agent_turns == 2
    assert runner.error is None
