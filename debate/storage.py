"""Versioned JSON checkpoints and atomic local writes; no database or pickle."""

import json
import math
import os
from dataclasses import dataclass, fields, is_dataclass
from datetime import datetime
from pathlib import Path
from tempfile import NamedTemporaryFile
from types import UnionType
from typing import Any, get_args, get_origin, get_type_hints
from uuid import UUID, uuid4

from debate.engine import DebateEngine
from debate.export import export_json
from debate.memory import PRIVATE_MEMORY_BUDGET, SummaryMemory, estimate_tokens
from debate.models import DebateConfig, DebateState, utc_now
from debate.ollama_client import validate_url
from debate.web_search import public_source_url

MAX_FILE_BYTES = 10 * 1024 * 1024


class HistoryError(ValueError):
    """Invalid checkpoint or unsuccessful local persistence."""


@dataclass
class EngineCheckpoint:
    version: int
    private_memories: dict[str, str]
    consensus_streak: int
    round_votes: dict[str, bool]


@dataclass
class SavedConversation:
    conversation_id: str
    topic: str
    updated_ns: int
    message_count: int
    finished: bool


def _decode(annotation: Any, value: Any, label: str) -> Any:
    """Validate only the dataclass types used by the application's JSON formats."""
    origin = get_origin(annotation)
    if origin is UnionType:
        for candidate in get_args(annotation):
            try:
                return _decode(candidate, value, label)
            except HistoryError:
                pass
    elif origin is list and isinstance(value, list):
        return [_decode(get_args(annotation)[0], item, label) for item in value]
    elif origin is dict and isinstance(value, dict):
        key_type, value_type = get_args(annotation)
        return {
            _decode(key_type, k, label): _decode(value_type, v, label) for k, v in value.items()
        }
    elif is_dataclass(annotation) and isinstance(value, dict):
        hints = get_type_hints(annotation)
        kwargs = {
            f.name: _decode(hints[f.name], value[f.name], f"{label}.{f.name}")
            for f in fields(annotation)
            if f.name in value
        }
        try:
            return annotation(**kwargs)
        except TypeError as exc:
            raise HistoryError(f"Champs requis manquants : {label}.") from exc
    elif annotation is float and type(value) in {float, int} and math.isfinite(value):
        return float(value)
    elif annotation in {str, int, bool, type(None)} and type(value) is annotation:
        if annotation is str:
            try:
                value.encode("utf-8")
            except UnicodeError as exc:
                raise HistoryError(f"Texte invalide : {label}.") from exc
        return value
    raise HistoryError(f"Type de donnée invalide : {label}.")


def checkpoint_json(engine: DebateEngine) -> str:
    payload = json.loads(export_json(engine))
    payload["saved_at"] = utc_now()
    payload["checkpoint"] = {
        "version": 1,
        "private_memories": engine.private_memories,
        "consensus_streak": engine.consensus_streak,
        "round_votes": engine.round_votes,
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _validate_history(engine: DebateEngine) -> None:
    datetime.fromisoformat(engine.state.created_at)
    if engine.state.finished_at is not None:
        datetime.fromisoformat(engine.state.finished_at)
    if bool(engine.state.stop_reason) != (engine.state.finished_at is not None):
        raise HistoryError("L'état de fin du débat est incohérent.")
    turns = 0
    for message in engine.state.messages:
        datetime.fromisoformat(message.timestamp)
        if message.speaker_id == "human":
            if message.round_number != turns // 2 + 1:
                raise HistoryError("Numéro de tour humain incohérent.")
        elif message.speaker_id == ("a" if turns % 2 == 0 else "b"):
            if message.round_number != turns // 2 + 1:
                raise HistoryError("Numéro de tour d'agent incohérent.")
            turns += 1
        else:
            raise HistoryError("L'ordre des interventions A/B est incohérent.")
        if message.research:
            for source in message.research.sources:
                if public_source_url(source.url) is None:
                    raise HistoryError("Lien de source web invalide dans la conversation.")
        if not message.content.strip() or message.duration_seconds < 0:
            raise HistoryError("Intervention vide ou durée invalide.")
    if turns != engine.agent_turns or turns > engine.config.max_rounds * 2:
        raise HistoryError("Compteur de tours incohérent.")
    if not 0 <= engine.summary.covered_messages <= len(engine.state.messages):
        raise HistoryError("Le résumé dépasse l'historique disponible.")
    if engine.state.judge_result and engine.state.judge_result.speaker_id != "judge":
        raise HistoryError("Identité du juge invalide.")
    for event in engine.state.control_events:
        if not 0 <= event.after_message <= len(engine.state.messages):
            raise HistoryError("Position d'un changement de consignes invalide.")


def restore_json(raw: str | bytes, *, new_identity: bool = False) -> DebateEngine:
    try:
        if len(raw.encode("utf-8") if isinstance(raw, str) else raw) > MAX_FILE_BYTES:
            raise HistoryError("Le fichier dépasse la limite de 10 Mo.")
        payload = json.loads(raw.decode("utf-8-sig") if isinstance(raw, bytes) else raw)
        if not isinstance(payload, dict) or type(payload.get("schema_version")) is not int:
            raise HistoryError("Ce fichier n'est pas un export de débat reconnu.")
        if payload["schema_version"] != 1:
            raise HistoryError("Version d'export non prise en charge.")
        config = _decode(DebateConfig, payload["parameters"], "parameters")
        config.validate()
        config.ollama_url = validate_url(config.ollama_url)
        engine = DebateEngine(config)
        engine.initial_config = _decode(
            DebateConfig, payload.get("initial_config", payload["parameters"]), "initial_config"
        )
        engine.initial_config.validate()
        engine.initial_config.ollama_url = validate_url(engine.initial_config.ollama_url)
        metadata = payload.get("metadata", {})
        if not isinstance(metadata, dict):
            raise HistoryError("Métadonnées invalides.")
        engine.state = _decode(
            DebateState,
            {
                "messages": payload["messages"],
                "created_at": payload["created_at"],
                "finished_at": payload.get("finished_at"),
                "stop_reason": metadata.get("stop_reason"),
                "judge_result": payload.get("judge_result"),
                "judge_context_reduced": metadata.get("judge_context_reduced", False),
                "control_events": payload.get("control_events", []),
                "warnings": metadata.get("warnings", []),
                "conversation_id": payload.get("conversation_id", str(uuid4())),
            },
            "state",
        )
        engine.state.conversation_id = str(UUID(engine.state.conversation_id))
        if new_identity:
            engine.state.conversation_id = str(uuid4())
        actual_turns = sum(m.speaker_id in {"a", "b"} for m in engine.state.messages)
        engine.agent_turns = _decode(int, metadata.get("agent_turns", actual_turns), "agent_turns")
        engine.summary = _decode(SummaryMemory, metadata.get("summary", {}), "summary")
        _validate_history(engine)
        checkpoint = payload.get("checkpoint")
        if checkpoint is not None:
            saved = _decode(EngineCheckpoint, checkpoint, "checkpoint")
            if saved.version != 1:
                raise HistoryError("Version de sauvegarde non prise en charge.")
            if set(saved.private_memories) != {"a", "b"} or any(
                estimate_tokens(note) > PRIVATE_MEMORY_BUDGET
                for note in saved.private_memories.values()
            ):
                raise HistoryError("Mémoires privées invalides.")
            if (
                not 0 <= saved.consensus_streak <= config.consensus_rounds
                or set(saved.round_votes) - {"a"}
                or (engine.agent_turns % 2 == 0 and saved.round_votes)
            ):
                raise HistoryError("État du consensus incohérent.")
            engine.private_memories = saved.private_memories
            engine.consensus_streak = saved.consensus_streak
            engine.round_votes = saved.round_votes
        else:
            engine.state.warnings.append(
                "Export partagé importé : les mémoires privées dynamiques ne sont pas disponibles. "
                "Les notes initiales sont utilisées et le consensus repart de zéro."
            )
        if not engine.finished and engine.agent_turns >= config.max_rounds * 2:
            engine.finish("Nombre maximal de tours atteint")
        return engine
    except HistoryError:
        raise
    except (ValueError, KeyError, TypeError, UnicodeError, RecursionError, OverflowError) as exc:
        raise HistoryError("Fichier de conversation invalide ou incomplet.") from exc


class ConversationStore:
    def __init__(self, directory: Path | None = None):
        self.directory = (
            directory
            if directory is not None
            else Path(
                os.environ.get(
                    "DEBATE_HISTORY_DIR",
                    str(Path(__file__).resolve().parents[1] / "conversations"),
                )
            )
        )
        self.warnings: list[str] = []

    def _path(self, conversation_id: str) -> Path:
        try:
            return self.directory / f"{UUID(conversation_id)}.json"
        except ValueError as exc:
            raise HistoryError("Identifiant de conversation invalide.") from exc

    def save(self, engine: DebateEngine) -> None:
        temporary: Path | None = None
        try:
            target = self._path(engine.state.conversation_id)
            data = checkpoint_json(engine).encode("utf-8")
            if len(data) > MAX_FILE_BYTES:
                raise HistoryError("La conversation dépasse la limite de sauvegarde de 10 Mo.")
            self.directory.mkdir(parents=True, exist_ok=True)
            with NamedTemporaryFile(
                dir=self.directory, prefix=".debate-", suffix=".tmp", delete=False
            ) as output:
                temporary = Path(output.name)
                output.write(data)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, target)
        except OSError as exc:
            raise HistoryError(
                "Impossible de sauvegarder le débat sur disque. Vérifiez le dossier "
                "d'historique et l'espace disponible."
            ) from exc
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass

    def load(self, conversation_id: str) -> DebateEngine:
        try:
            path = self._path(conversation_id)
            if path.stat().st_size > MAX_FILE_BYTES:
                raise HistoryError("Le fichier dépasse la limite de 10 Mo.")
            engine = restore_json(path.read_bytes())
            if engine.state.conversation_id != str(UUID(conversation_id)):
                raise HistoryError("L'identifiant du fichier ne correspond pas à la conversation.")
            return engine
        except OSError as exc:
            raise HistoryError("Impossible de lire cette conversation enregistrée.") from exc

    def list_conversations(self) -> list[SavedConversation]:
        self.warnings = []
        entries = []
        try:
            for path in self.directory.glob("*.json"):
                try:
                    engine = self.load(path.stem)
                    entries.append(
                        SavedConversation(
                            engine.state.conversation_id,
                            engine.config.topic,
                            path.stat().st_mtime_ns,
                            len(engine.state.messages),
                            engine.finished,
                        )
                    )
                except HistoryError:
                    self.warnings.append(
                        f"Sauvegarde ignorée car illisible ou invalide : {path.name}"
                    )
        except OSError:
            self.warnings.append("Le dossier d'historique est inaccessible.")
        return sorted(entries, key=lambda item: item.updated_ns, reverse=True)
