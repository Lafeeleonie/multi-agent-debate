"""Configuration and exportable state, using only standard-library dataclasses."""

from dataclasses import dataclass, field
from datetime import UTC, datetime

DEFAULT_MODEL = "huihui_ai/qwen3.5-abliterated:35b"
DEFAULT_RULES = """Répondre directement aux arguments de l'adversaire et de l'utilisateur.
Ne pas ignorer une objection importante. Éviter les répétitions.
Distinguer faits, hypothèses et opinions. Reconnaître un argument adverse valide.
Signaler les incertitudes. Ne pas inventer de sources.
Faire progresser la discussion plutôt que chercher à avoir le dernier mot."""
DEFAULT_JUDGE_PROMPT = """Tu es un arbitre impartial. Analyse le débat sans favoriser un camp.
Produis : 1. résumé très court ; 2. arguments de A ; 3. arguments de B ;
4. arguments réfutés ; 5. arguments sans réponse ; 6. concessions ;
7. sophismes ou erreurs ; 8. note d'argumentation sur 10 pour A ;
9. note sur 10 pour B ; 10. vainqueur seulement si l'écart est net ;
11. questions ouvertes. Tiens compte des interventions humaines sans leur attribuer
une note. Si le contexte est résumé, précise les limites de ton analyse."""


def utc_now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class AgentConfig:
    name: str
    role: str
    objective: str
    system_prompt: str = ""
    constraints: str = ""
    temperature: float = 0.7
    private_memory: str = ""
    web_access: bool = True


@dataclass
class WebSearchConfig:
    enabled: bool = False
    max_queries: int = 1
    max_results: int = 3
    timeout_seconds: int = 10
    backend: str = "duckduckgo,bing,brave"

    def validate(self) -> None:
        if not 1 <= self.max_queries <= 3 or not 1 <= self.max_results <= 5:
            raise ValueError("Limitez la recherche à 1–3 requêtes et 1–5 résultats par requête.")
        if not 3 <= self.timeout_seconds <= 30:
            raise ValueError("Le délai de recherche doit être compris entre 3 et 30 secondes.")
        if self.backend not in {"duckduckgo,bing,brave", "duckduckgo", "bing", "brave"}:
            raise ValueError("Moteur de recherche non pris en charge.")


@dataclass
class JudgeConfig:
    enabled: bool = False
    model: str = DEFAULT_MODEL
    system_prompt: str = DEFAULT_JUDGE_PROMPT
    temperature: float = 0.3


@dataclass
class DebateConfig:
    topic: str = "Le progrès technique améliore-t-il nécessairement nos vies ?"
    initial_context: str = ""
    rules: str = DEFAULT_RULES
    language: str = "Français"
    constraints: str = ""
    human_name: str = "Vous"
    model: str = DEFAULT_MODEL
    ollama_url: str = "http://localhost:11434"
    max_rounds: int = 10
    max_tokens: int = 1200
    context_tokens: int = 32768
    delay_seconds: float = 0.0
    early_stop: bool = True
    consensus_rounds: int = 2
    compression: bool = False
    recent_messages: int = 6
    summary_tokens: int = 1000
    agent_a: AgentConfig = field(
        default_factory=lambda: AgentConfig(
            "Agent A",
            "Défenseur de la thèse",
            "Construire les arguments les plus solides en faveur de la proposition, "
            "répondre aux objections et concéder les arguments valides.",
        )
    )
    agent_b: AgentConfig = field(
        default_factory=lambda: AgentConfig(
            "Agent B",
            "Opposant à la thèse",
            "Contester la proposition, chercher les failles et contre-exemples, "
            "répondre aux arguments et concéder les arguments valides.",
        )
    )
    judge: JudgeConfig = field(default_factory=JudgeConfig)
    web: WebSearchConfig = field(default_factory=WebSearchConfig)

    def validate(self) -> None:
        if not self.topic.strip() or not self.language.strip() or not self.model.strip():
            raise ValueError("Le sujet, la langue et le modèle sont obligatoires.")
        if not self.human_name.strip():
            raise ValueError("Le nom du participant humain est obligatoire.")
        if not 1 <= self.max_rounds <= 100:
            raise ValueError("Le nombre de tours doit être compris entre 1 et 100.")
        if not 128 <= self.max_tokens <= 8192:
            raise ValueError("La limite de réponse doit être comprise entre 128 et 8192 tokens.")
        if not 4096 <= self.context_tokens <= 131072:
            raise ValueError("Le contexte doit être compris entre 4096 et 131072 tokens.")
        if self.max_tokens + 512 >= self.context_tokens:
            raise ValueError("Le contexte doit dépasser la réponse d'au moins 512 tokens.")
        if not 0 <= self.delay_seconds <= 60 or not 1 <= self.consensus_rounds <= 10:
            raise ValueError("Délai ou seuil d'arrêt anticipé invalide.")
        if not 2 <= self.recent_messages <= 50 or not 128 <= self.summary_tokens <= 4096:
            raise ValueError("Paramètres de mémoire invalides.")
        for agent in (self.agent_a, self.agent_b):
            if not agent.name.strip() or not agent.role.strip() or not agent.objective.strip():
                raise ValueError("Chaque agent doit avoir un nom, un rôle et un objectif.")
            if not 0 <= agent.temperature <= 2:
                raise ValueError("La température doit être comprise entre 0 et 2.")
        if not 0 <= self.judge.temperature <= 2:
            raise ValueError("La température du juge doit être comprise entre 0 et 2.")
        if self.judge.enabled and not self.judge.model.strip():
            raise ValueError("Le modèle du juge est obligatoire.")
        self.web.validate()


@dataclass
class WebSource:
    source_id: str
    title: str
    url: str
    snippet: str
    query: str
    retrieved_at: str = field(default_factory=utc_now)


@dataclass
class WebResearch:
    queries: list[str] = field(default_factory=list)
    sources: list[WebSource] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    timestamp: str = field(default_factory=utc_now)


@dataclass
class DebateMessage:
    speaker_id: str
    speaker_name: str
    round_number: int
    content: str
    timestamp: str = field(default_factory=utc_now)
    duration_seconds: float = 0.0
    tokens: int | None = None
    continue_debate: bool | None = None
    research: WebResearch | None = None


@dataclass
class ControlEvent:
    language: str
    constraints: str
    constraints_a: str
    constraints_b: str
    after_message: int
    timestamp: str = field(default_factory=utc_now)
    web_enabled: bool = False
    web_a: bool = True
    web_b: bool = True


@dataclass
class DebateState:
    messages: list[DebateMessage] = field(default_factory=list)
    created_at: str = field(default_factory=utc_now)
    finished_at: str | None = None
    stop_reason: str | None = None
    judge_result: DebateMessage | None = None
    judge_context_reduced: bool = False
    control_events: list[ControlEvent] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
