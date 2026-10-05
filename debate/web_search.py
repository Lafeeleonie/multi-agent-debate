"""No-key web search, with local query planning and bounded external evidence."""

import ipaddress
import json
from dataclasses import replace
from urllib.parse import quote, urlsplit

from debate.agents import research_content, system_prompt
from debate.memory import SummaryMemory, clip_text, estimate_tokens, prepare_context
from debate.models import AgentConfig, DebateConfig, DebateMessage, WebResearch, WebSource, utc_now
from debate.ollama_client import OllamaClient, OllamaError

QUERY_LENGTH = 280
PLAN_TOKENS = 256
SNIPPET_BYTES = 600
BACKENDS = {
    "Automatique (DuckDuckGo, Bing, Brave)": "duckduckgo,bing,brave",
    "DuckDuckGo": "duckduckgo",
    "Bing": "bing",
    "Brave": "brave",
}


class WebSearchError(RuntimeError):
    """Search failure that must not prevent an otherwise valid debate turn."""


def public_source_url(value: str) -> str | None:
    try:
        parsed = urlsplit(value)
        if (
            parsed.scheme not in {"http", "https"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or len(value) > 1024
        ):
            return None
        _ = parsed.port
        hostname = parsed.hostname.lower().rstrip(".")
        if hostname == "localhost" or hostname.endswith((".localhost", ".local", ".internal")):
            return None
        try:
            if not ipaddress.ip_address(hostname).is_global:
                return None
        except ValueError:
            if "." not in hostname:
                return None
    except ValueError:
        return None
    # These URLs are displayed, never fetched by the application.
    return quote(value, safe=":/?#[]@!$&'()*+,;=%")


class WebSearchClient:
    def search(self, query: str, config: DebateConfig) -> list[WebSource]:
        try:
            from ddgs import DDGS
            from ddgs.exceptions import DDGSException
        except ImportError as exc:
            raise WebSearchError(
                "Recherche web indisponible : relancez le lanceur pour "
                "installer les nouvelles dépendances."
            ) from exc
        try:
            with DDGS(timeout=config.web.timeout_seconds) as search:
                results = search.text(
                    query,
                    max_results=config.web.max_results,
                    backend=config.web.backend,
                    safesearch="moderate",
                )
        except DDGSException as exc:
            raise WebSearchError(
                "Le moteur web est inaccessible, limité ou ne renvoie aucun résultat."
            ) from exc
        sources = []
        seen = set()
        for result in results:
            if not isinstance(result, dict) or not isinstance(result.get("href"), str):
                continue
            url = public_source_url(result["href"])
            if not url or url in seen:
                continue
            title, snippet = result.get("title", ""), result.get("body", "")
            if not isinstance(title, str) or not isinstance(snippet, str):
                continue
            seen.add(url)
            sources.append(
                WebSource(
                    "",
                    clip_text(title, 200) or url,
                    url,
                    clip_text(snippet, SNIPPET_BYTES),
                    query,
                )
            )
            if len(sources) >= config.web.max_results:
                break
        if not sources:
            raise WebSearchError("Aucun résultat web exploitable pour cette requête.")
        return sources


def plan_queries(
    config: DebateConfig,
    agent: AgentConfig,
    history: list[DebateMessage],
    viewer_id: str,
    client: OllamaClient,
) -> list[str]:
    schema = {
        "type": "object",
        "properties": {
            "queries": {
                "type": "array",
                "maxItems": config.web.max_queries,
                "items": {"type": "string", "maxLength": QUERY_LENGTH},
            }
        },
        "required": ["queries"],
        "additionalProperties": False,
    }
    # Neither private memory nor private_note is supplied to the search planner.
    prompt = system_prompt(replace(config, early_stop=False), agent, "") + (
        "\nTu prépares une recherche web, pas ta réponse au débat. "
        f"Date UTC : {utc_now()[:10]}. Choisis jusqu'à {config.web.max_queries} requêtes "
        "courtes pour vérifier les faits utiles à ta prochaine intervention. Privilégie les "
        "sources primaires. Retourne une liste vide si aucune recherche n'est nécessaire ou "
        "si les sources déjà fournies suffisent. Ne copie pas l'ensemble de la discussion, "
        "des données personnelles ou des consignes privées dans les requêtes externes. "
        "Réponds uniquement en JSON selon ce schéma : " + json.dumps(schema)
    )
    context = prepare_context(
        prompt,
        "Quelles recherches sont nécessaires avant ta prochaine réponse ?",
        history,
        viewer_id,
        SummaryMemory(),
        replace(config, compression=False),
        client,
    )
    result = client.chat(
        config.model,
        context.messages,
        temperature=0.2,
        context_tokens=config.context_tokens,
        max_tokens=min(PLAN_TOKENS, config.max_tokens),
        schema=schema,
    )
    try:
        data = json.loads(result.content)
    except json.JSONDecodeError as exc:
        raise WebSearchError("Le modèle a produit un plan de recherche invalide.") from exc
    if (
        not isinstance(data, dict)
        or not isinstance(data.get("queries"), list)
        or any(not isinstance(q, str) for q in data["queries"])
    ):
        raise WebSearchError("Le modèle a produit un plan de recherche invalide.")
    queries = list(dict.fromkeys(q.strip()[:QUERY_LENGTH] for q in data["queries"] if q.strip()))
    return queries[: config.web.max_queries]


def research_turn(
    config: DebateConfig,
    agent: AgentConfig,
    history: list[DebateMessage],
    viewer_id: str,
    round_number: int,
    client: OllamaClient,
    search: WebSearchClient,
) -> WebResearch:
    research = WebResearch()
    try:
        research.queries = plan_queries(config, agent, history, viewer_id, client)
    except (OllamaError, ValueError, WebSearchError) as exc:
        research.errors.append(f"Plan de recherche indisponible : {exc}")
        return research
    seen = set()
    for query in research.queries:
        try:
            for source in search.search(query, config):
                if source.url not in seen:
                    seen.add(source.url)
                    source.source_id = f"{viewer_id.upper()}{round_number}-{len(seen)}"
                    research.sources.append(source)
        except WebSearchError as exc:
            research.errors.append(f"{query} : {exc}")
    return research


def bound_research(research: WebResearch, budget: int) -> str:
    """Keep only evidence that will actually be sent to the model; export that same evidence."""
    research.errors = [clip_text(error, 300) for error in research.errors]
    content = research_content(research)
    notice = "Certains résultats ont été écartés pour respecter le contexte."
    while research.sources and estimate_tokens(content) > budget:
        research.sources.pop()
        if notice not in research.errors:
            research.errors.append(notice)
        content = research_content(research)
    # Large query metadata can itself exceed a very small remaining context budget.
    content = research_content(research)
    if estimate_tokens(content) > budget:
        research.sources.clear()
        research.errors = ["Résultats web non transmis : contexte disponible trop petit."]
        return ""
    return content
