"""Exports are downloaded by the user; nothing is written to the project directory."""

import json
from dataclasses import asdict

from debate.engine import DebateEngine


def export_json(engine: DebateEngine) -> str:
    return json.dumps(
        {
            "schema_version": 1,
            "conversation_id": engine.state.conversation_id,
            "topic": engine.config.topic,
            "created_at": engine.state.created_at,
            "finished_at": engine.state.finished_at,
            "initial_config": asdict(engine.initial_config),
            "parameters": asdict(engine.config),
            "messages": [asdict(message) for message in engine.state.messages],
            "judge_result": asdict(engine.state.judge_result)
            if engine.state.judge_result
            else None,
            "control_events": [asdict(event) for event in engine.state.control_events],
            "metadata": {
                "agent_turns": engine.agent_turns,
                "stop_reason": engine.state.stop_reason,
                "warnings": engine.state.warnings,
                "judge_context_reduced": engine.state.judge_context_reduced,
                "summary": asdict(engine.summary),
            },
        },
        ensure_ascii=False,
        indent=2,
    )


def export_markdown(engine: DebateEngine) -> str:
    config = engine.config
    lines = [
        f"# {config.topic}",
        "",
        f"Date : {engine.state.created_at}",
        f"Modèle : {config.model}",
        f"Langue actuelle : {config.language}",
        "",
        "## Contexte initial",
        "",
        config.initial_context or "Aucun.",
        "",
        "## Règles communes",
        "",
        config.rules,
        "",
        "## Contraintes actuelles",
        "",
        config.constraints or "Aucune.",
        "",
    ]
    for label, agent in (("A", config.agent_a), ("B", config.agent_b)):
        lines.extend(
            [
                f"### {label} — {agent.name}",
                "",
                f"Rôle : {agent.role}",
                f"Objectif : {agent.objective}",
                f"Contraintes : {agent.constraints or 'Aucune.'}",
                "",
            ]
        )
    if engine.state.control_events:
        lines.extend(["## Modifications des consignes", ""])
        for event in engine.state.control_events:
            lines.extend(
                [
                    f"Après le message {event.after_message} ({event.timestamp}) :",
                    "",
                    f"Langue : {event.language}",
                    f"Communes : {event.constraints or 'Aucune.'}",
                    f"A : {event.constraints_a or 'Aucune.'}",
                    f"B : {event.constraints_b or 'Aucune.'}",
                    f"Recherche web : {event.web_enabled} · A : {event.web_a} · B : {event.web_b}",
                    "",
                ]
            )
    lines.extend(["## Discussion", ""])
    for message in engine.state.messages:
        label = "Intervention humaine" if message.speaker_id == "human" else "Agent"
        lines.extend(
            [
                f"### Tour {message.round_number} — {label} : {message.speaker_name}",
                "",
                message.content,
                "",
                f"_{message.duration_seconds:.1f} s · {message.tokens or '—'} tokens_",
                "",
            ]
        )
        if message.research:
            lines.extend(["#### Recherches web", ""])
            for query in message.research.queries:
                lines.extend([f"Requête : {query}", ""])
            for error in message.research.errors:
                lines.extend([f"> {error}", ""])
            for source in message.research.sources:
                title = source.title.replace("[", "\\[").replace("]", "\\]")
                lines.extend(
                    [
                        f"[{source.source_id} — {title}](<{source.url}>)",
                        "",
                        source.snippet,
                        "",
                        f"_Extrait obtenu le {source.retrieved_at}_",
                        "",
                    ]
                )
    if engine.state.judge_result:
        lines.extend(["## Rapport du juge", "", engine.state.judge_result.content, ""])
        if engine.state.judge_context_reduced:
            lines.extend(["_Le juge a utilisé un résumé des échanges anciens._", ""])
    lines.extend([f"Fin : {engine.state.stop_reason or 'Débat en cours / en pause'}", ""])
    for warning in engine.state.warnings:
        lines.extend([f"> {warning}", ""])
    return "\n".join(lines)
