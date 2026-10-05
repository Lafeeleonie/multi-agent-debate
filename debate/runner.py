"""One background request per session; only the UI thread installs completed state."""

import copy
from concurrent.futures import Future
from threading import Thread
from time import monotonic

from debate.engine import DebateEngine
from debate.judge import run_judge
from debate.ollama_client import OllamaClient
from debate.storage import ConversationStore, HistoryError


class DebateRunner:
    def __init__(self, engine: DebateEngine, store: ConversationStore | None = None):
        self.engine = engine
        self.future: Future[DebateEngine] | None = None
        self.job: str | None = None
        self.running = False
        self.stop_requested = False
        self.error: str | None = None
        self.next_at = 0.0
        self.judge_attempted = False
        self.store = store
        self.storage_error: str | None = None
        self.save()

    def _save_engine(self, engine: DebateEngine) -> None:
        if self.store is not None:
            try:
                self.store.save(engine)
                self.storage_error = None
            except HistoryError as exc:
                self.storage_error = str(exc)

    def save(self) -> None:
        if not self.busy:
            self._save_engine(self.engine)

    @property
    def busy(self) -> bool:
        return self.future is not None

    def advance(self, *, automatic: bool = False) -> None:
        if self.busy or self.engine.finished:
            return
        self.running = automatic
        self.error = None
        self._submit("agent")

    def pause(self) -> None:
        self.running = False

    def stop(self) -> None:
        self.running = False
        if self.busy:
            self.stop_requested = True
        else:
            self.engine.finish()
            self.save()

    def judge(self) -> None:
        if not self.busy and self.engine.finished:
            self.error = None
            self.judge_attempted = True
            self._submit("judge")

    def _submit(self, job: str) -> None:
        # Working on a copy prevents a partial response or a failed call from corrupting
        # the visible state. The background thread never calls a Streamlit function.
        working = copy.deepcopy(self.engine)
        future: Future[DebateEngine] = Future()
        self.future, self.job = future, job

        def generate() -> None:
            try:
                client = OllamaClient(working.config.ollama_url)
                if job == "agent":
                    working.step(client)
                else:
                    client.require_model(working.config.judge.model)
                    run_judge(working, client)
                if self.stop_requested:
                    working.finish()
                # Persist even if the browser has closed and no UI polling occurs.
                self._save_engine(working)
                future.set_result(working)
            except Exception as exc:
                future.set_exception(exc)

        Thread(target=generate, name="ollama-debate", daemon=True).start()

    def poll(self) -> None:
        if self.future is not None and self.future.done():
            try:
                self.engine = self.future.result()
            except Exception as exc:
                self.error = str(exc)
                self.running = False
            self.future = None
            self.job = None
            self.next_at = monotonic() + self.engine.config.delay_seconds
            if self.stop_requested:
                if not self.engine.finished:
                    self.engine.finish()
                    self.save()
                self.stop_requested = False
        if self.busy:
            return
        if self.engine.finished:
            self.running = False
            if (
                self.engine.config.judge.enabled
                and not self.judge_attempted
                and self.engine.state.messages
                and self.error is None
            ):
                self.judge()
        elif self.running and monotonic() >= self.next_at:
            self._submit("agent")
