"""Optional live check. Uses an installed model, never downloads anything."""

import argparse
import sys
from pathlib import Path

# Also supports `python scripts/smoke_ollama.py` from a clone without installation.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from debate.engine import DebateEngine  # noqa: E402
from debate.judge import run_judge  # noqa: E402
from debate.models import DEFAULT_MODEL, DebateConfig  # noqa: E402
from debate.ollama_client import OllamaClient  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--url", default="http://localhost:11434")
    parser.add_argument("--context", type=int, default=8192)
    args = parser.parse_args()
    client = OllamaClient(args.url)
    client.require_model(args.model)
    config = DebateConfig(
        topic="Faut-il développer les transports en commun ?",
        model=args.model,
        ollama_url=args.url,
        max_rounds=1,
        max_tokens=512,
        context_tokens=args.context,
        constraints="Réponds en deux phrases maximum.",
    )
    config.judge.model = args.model
    engine = DebateEngine(config)
    first = engine.step(client)
    print(f"A ({first.duration_seconds:.1f}s, {first.tokens} tokens) : {first.content}")
    engine.add_human_message("Il faut également penser aux personnes vivant à la campagne.")
    engine.update_controls(
        "English", "At most two sentences. Address the human's rural concern.", "", ""
    )
    second = engine.step(client)
    print(f"B ({second.duration_seconds:.1f}s, {second.tokens} tokens) : {second.content}")
    report = run_judge(engine, client)
    print(f"Judge ({report.duration_seconds:.1f}s, {report.tokens} tokens) : {report.content}")
    assert engine.agent_turns == 2 and engine.finished
    assert [m.speaker_id for m in engine.state.messages] == ["a", "human", "b"]
    print("OK : deux contextes, avis humain, changement de langue, sortie structurée et juge.")


if __name__ == "__main__":
    main()
