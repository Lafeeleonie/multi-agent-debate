"""Judge context is independent of both agents and their private memories."""

from time import monotonic

from debate.agents import common_instructions
from debate.engine import DebateEngine
from debate.memory import SummaryMemory, prepare_context
from debate.models import DebateMessage
from debate.ollama_client import OllamaClient


def run_judge(engine: DebateEngine, client: OllamaClient) -> DebateMessage:
    if not engine.finished or not engine.state.messages:
        raise ValueError("Le juge intervient après un débat comportant au moins un message.")
    config = engine.config
    prompt = (
        f"{config.judge.system_prompt}\n{common_instructions(config)}\n"
        f"Sujet : {config.topic}\nContexte initial : {config.initial_context}\n"
        f"Agent A : {config.agent_a.name}, rôle : {config.agent_a.role}, "
        f"contraintes : {config.agent_a.constraints}\n"
        f"Agent B : {config.agent_b.name}, rôle : {config.agent_b.role}, "
        f"contraintes : {config.agent_b.constraints}\n"
        "Les éventuels résumés sont des synthèses, pas des citations exactes."
        "\nLes exigences de langue et les contraintes de l'utilisateur priment sur le "
        "format du rapport demandé. Adapte ce format si nécessaire pour les respecter."
    )
    started = monotonic()
    context = prepare_context(
        prompt,
        f"Produis maintenant ton rapport impartial EXCLUSIVEMENT en {config.language}. "
        f"Contraintes obligatoires pour ce rapport : {config.constraints or 'Aucune.'}. "
        "Traduis aussi tous les titres dans la langue imposée.",
        engine.state.messages,
        None,
        SummaryMemory(),
        config,
        client,
        require_complete=True,
    )
    result = client.chat(
        config.judge.model,
        context.messages,
        temperature=config.judge.temperature,
        context_tokens=config.context_tokens,
        max_tokens=config.max_tokens,
    )
    report = DebateMessage(
        "judge",
        "Juge",
        max(1, (engine.agent_turns + 1) // 2),
        result.content,
        duration_seconds=monotonic() - started,
        tokens=result.tokens,
    )
    engine.state.judge_result = report
    engine.state.judge_context_reduced = context.reduced
    if result.done_reason == "length":
        engine.state.warnings.append("Le rapport du juge a atteint la limite de tokens.")
    return report
