"""Sequential A/B engine; human interventions never consume an agent's turn."""

import copy
import json
from time import monotonic

from debate.agents import TURN_SCHEMA, system_prompt
from debate.memory import (
    PRIVATE_MEMORY_BUDGET,
    SummaryMemory,
    clip_text,
    messages_cost,
    prepare_context,
)
from debate.models import ControlEvent, DebateConfig, DebateMessage, DebateState, utc_now
from debate.ollama_client import OllamaClient, OllamaError
from debate.web_search import WebSearchClient, bound_research, research_turn


class DebateEngine:
    def __init__(self, config: DebateConfig):
        config.validate()
        self.config = copy.deepcopy(config)
        self.initial_config = copy.deepcopy(config)
        self.state = DebateState()
        self.private_memories = {
            "a": clip_text(config.agent_a.private_memory, PRIVATE_MEMORY_BUDGET),
            "b": clip_text(config.agent_b.private_memory, PRIVATE_MEMORY_BUDGET),
        }
        self.summary = SummaryMemory()
        self.agent_turns = 0
        self.consensus_streak = 0
        self.round_votes: dict[str, bool] = {}

    @property
    def finished(self) -> bool:
        return self.state.stop_reason is not None

    @property
    def next_speaker(self) -> str:
        return "a" if self.agent_turns % 2 == 0 else "b"

    @property
    def next_round(self) -> int:
        return self.agent_turns // 2 + 1

    def finish(self, reason: str = "Arrêt demandé par l'utilisateur") -> None:
        if not self.finished:
            self.state.stop_reason = reason
            self.state.finished_at = utc_now()

    def add_human_message(self, text: str) -> None:
        if self.finished:
            raise ValueError("Ce débat est terminé. Réinitialisez pour en lancer un autre.")
        if not text.strip():
            raise ValueError("Votre intervention ne peut pas être vide.")
        if len(text.encode("utf-8")) > self.config.context_tokens // 3:
            raise ValueError("Votre intervention est trop longue pour le contexte choisi.")
        self.state.messages.append(
            DebateMessage(
                "human",
                self.config.human_name,
                self.next_round,
                text.strip(),
            )
        )
        self._reset_consensus()

    def _reset_consensus(self) -> None:
        self.consensus_streak = 0
        self.round_votes.clear()

    def update_controls(
        self,
        language: str,
        constraints: str,
        constraints_a: str,
        constraints_b: str,
        compression: bool | None = None,
        web_enabled: bool | None = None,
        web_a: bool | None = None,
        web_b: bool | None = None,
    ) -> None:
        if not language.strip():
            raise ValueError("La langue est obligatoire.")
        self.config.language = language.strip()
        self.config.constraints = constraints
        self.config.agent_a.constraints = constraints_a
        self.config.agent_b.constraints = constraints_b
        if compression is not None:
            self.config.compression = compression
        if web_enabled is not None:
            self.config.web.enabled = web_enabled
        if web_a is not None:
            self.config.agent_a.web_access = web_a
        if web_b is not None:
            self.config.agent_b.web_access = web_b
        self.state.control_events.append(
            ControlEvent(
                self.config.language,
                constraints,
                constraints_a,
                constraints_b,
                len(self.state.messages),
                web_enabled=self.config.web.enabled,
                web_a=self.config.agent_a.web_access,
                web_b=self.config.agent_b.web_access,
            )
        )
        self._reset_consensus()
        self.state.judge_result = None

    def step(
        self, client: OllamaClient, web_client: WebSearchClient | None = None
    ) -> DebateMessage:
        if self.finished:
            raise ValueError("Le débat est terminé.")
        speaker = self.next_speaker
        agent = self.config.agent_a if speaker == "a" else self.config.agent_b
        prompt = system_prompt(self.config, agent, self.private_memories[speaker])
        instruction = (
            f"Tour {self.next_round}. Interviens maintenant en tant que {agent.name}. "
            "Réponds aux arguments récents et aux avis humains. "
            "S'il n'y a pas encore de message, présente ta position initiale."
        )
        started = monotonic()
        research = None
        if self.config.web.enabled and agent.web_access:
            research = research_turn(
                self.config,
                agent,
                self.state.messages,
                speaker,
                self.next_round,
                client,
                web_client if web_client is not None else WebSearchClient(),
            )
            fixed_cost = messages_cost(
                [
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": instruction},
                ]
            )
            evidence_budget = min(
                6000,
                self.config.context_tokens // 4,
                max(0, self.config.context_tokens - self.config.max_tokens - 512 - fixed_cost),
            )
            evidence = bound_research(research, evidence_budget)
            if evidence:
                instruction += "\n\n" + evidence
        context = prepare_context(
            prompt,
            instruction,
            self.state.messages,
            speaker,
            self.summary,
            self.config,
            client,
        )
        result = client.chat(
            self.config.model,
            context.messages,
            temperature=agent.temperature,
            context_tokens=self.config.context_tokens,
            max_tokens=self.config.max_tokens,
            schema=TURN_SCHEMA if self.config.early_stop else None,
        )
        if self.config.early_stop:
            response, vote, note = self._parse_turn(result.content)
        else:
            response, vote, note = result.content, None, None
        message = DebateMessage(
            speaker,
            agent.name,
            self.next_round,
            response,
            duration_seconds=monotonic() - started,
            tokens=result.tokens,
            continue_debate=vote,
            research=research,
        )
        # Commit only a valid complete response; failed calls leave the turn unconsumed.
        self.state.messages.append(message)
        self.summary = context.summary
        if note is not None:
            self.private_memories[speaker] = clip_text(note, PRIVATE_MEMORY_BUDGET)
        self.agent_turns += 1
        if context.reduced:
            warning = (
                "Le contexte envoyé aux agents est réduit : résumé et/ou échanges récents. "
                "L'historique public complet reste disponible dans les exports."
            )
            if warning not in self.state.warnings:
                self.state.warnings.append(warning)
        if result.done_reason == "length":
            self.state.warnings.append(
                f"{agent.name} a atteint la limite de tokens au tour {message.round_number}."
            )
        if vote is not None:
            self.round_votes[speaker] = vote
        if speaker == "b":
            both_finished = self.round_votes == {"a": False, "b": False}
            self.consensus_streak = self.consensus_streak + 1 if both_finished else 0
            self.round_votes.clear()
            if self.config.early_stop and self.consensus_streak >= self.config.consensus_rounds:
                self.finish("Consensus répété : aucun nouvel argument substantiel")
        if self.agent_turns >= self.config.max_rounds * 2:
            self.finish("Nombre maximal de tours atteint")
        return message

    @staticmethod
    def _parse_turn(content: str) -> tuple[str, bool, str]:
        try:
            data = json.loads(content)
        except json.JSONDecodeError as exc:
            raise OllamaError(
                "Sortie structurée invalide. Réessayez ou désactivez l'arrêt anticipé "
                "pour ce modèle dans un nouveau débat."
            ) from exc
        if (
            not isinstance(data, dict)
            or not isinstance(data.get("response"), str)
            or not data["response"].strip()
            or type(data.get("continue_debate")) is not bool
            or not isinstance(data.get("private_note"), str)
        ):
            raise OllamaError(
                "Sortie structurée invalide : réponse, booléen et note privée attendus."
            )
        return data["response"].strip(), data["continue_debate"], data["private_note"]
