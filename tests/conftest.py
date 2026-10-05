import json
from collections import deque

import pytest

from debate.models import DebateConfig
from debate.ollama_client import ChatResult


class FakeClient:
    def __init__(self, replies=()):
        self.replies = deque(replies)
        self.calls = []

    def chat(self, model, messages, **options):
        self.calls.append({"model": model, "messages": messages, **options})
        if self.replies:
            reply = self.replies.popleft()
            if isinstance(reply, Exception):
                raise reply
            return ChatResult(reply, 42)
        return ChatResult("Résumé équilibré des arguments et avis humains.", 10)

    def require_model(self, model, installed=None):
        return None


def turn(response="Argument public", vote=True, note="Note privée"):
    return json.dumps({"response": response, "continue_debate": vote, "private_note": note})


@pytest.fixture
def config():
    return DebateConfig()
