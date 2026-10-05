"""Streamlit UI. Run with: streamlit run app.py"""

import os
from dataclasses import asdict

import streamlit as st

from debate.engine import DebateEngine
from debate.export import export_json, export_markdown
from debate.models import AgentConfig, DebateConfig, JudgeConfig, WebSearchConfig
from debate.ollama_client import OllamaClient, OllamaError
from debate.presets import PRESET_NAMES, get_preset
from debate.runner import DebateRunner
from debate.web_search import BACKENDS

st.set_page_config(page_title="Agora locale · Débat IA", page_icon="💬", layout="wide")


def seed_widgets(config: DebateConfig, *, replace: bool = False) -> None:
    values = asdict(config)
    for group in ("agent_a", "agent_b", "judge", "web"):
        nested = values.pop(group)
        values.update({f"{group}_{key}": value for key, value in nested.items()})
    for key, value in values.items():
        widget_key = "cfg_" + key
        if replace or widget_key not in st.session_state:
            st.session_state[widget_key] = value


def apply_preset() -> None:
    preset = get_preset(st.session_state.preset)
    # A preset only fills fields. Preserve the user's Ollama endpoint and model choices.
    preset.ollama_url = st.session_state.cfg_ollama_url
    preset.model = st.session_state.cfg_model
    preset.judge.model = st.session_state.cfg_judge_model
    for key in asdict(preset.web):
        setattr(preset.web, key, st.session_state["cfg_web_" + key])
    seed_widgets(preset, replace=True)


@st.cache_data(ttl=15, show_spinner=False)
def available_models(url: str) -> list[str]:
    return OllamaClient(url).list_models()


def agent_fields(prefix: str, *, disabled: bool) -> AgentConfig:
    return AgentConfig(
        name=st.text_input("Nom", key=f"cfg_{prefix}_name", disabled=disabled),
        role=st.text_input("Rôle", key=f"cfg_{prefix}_role", disabled=disabled),
        objective=st.text_area("Objectif", key=f"cfg_{prefix}_objective", disabled=disabled),
        system_prompt=st.text_area(
            "Prompt système",
            key=f"cfg_{prefix}_system_prompt",
            disabled=disabled,
        ),
        constraints=st.text_area(
            "Contraintes propres",
            key=f"cfg_{prefix}_constraints",
            disabled=disabled,
            placeholder="Ex. : une objection à la fois, aucun argument d'autorité…",
        ),
        temperature=st.slider(
            "Température",
            0.0,
            2.0,
            step=0.05,
            key=f"cfg_{prefix}_temperature",
            disabled=disabled,
        ),
        private_memory=st.text_area(
            "Mémoire privée initiale",
            key=f"cfg_{prefix}_private_memory",
            disabled=disabled,
            help="Visible uniquement par cet agent dans les appels au modèle. "
            "Cette configuration initiale figure dans l'export JSON.",
        ),
        web_access=st.checkbox(
            "Autoriser les recherches web de cet agent",
            key=f"cfg_{prefix}_web_access",
            disabled=disabled,
        ),
    )


def configuration() -> DebateConfig:
    locked = st.session_state.get("runner") is not None
    with st.sidebar:
        st.title("💬 Agora locale")
        st.caption("Deux agents locaux, votre voix, un accès web optionnel.")
        st.selectbox("Preset", PRESET_NAMES, key="preset", disabled=locked)
        st.button("Remplir les champs", on_click=apply_preset, disabled=locked, width="stretch")
        if locked:
            st.caption("Configuration initiale. Les consignes se modifient dans le débat en pause.")
        with st.expander("Ollama et modèles", expanded=True):
            url = st.text_input("URL Ollama locale", key="cfg_ollama_url", disabled=locked)
            if st.button("Actualiser les modèles", disabled=locked):
                available_models.clear()
            try:
                models = available_models(url)
                st.success(f"Ollama connecté · {len(models)} modèle(s)")
            except (OllamaError, ValueError) as exc:
                models = []
                st.error(str(exc))
            choices = list(dict.fromkeys([st.session_state.cfg_model, *models]))
            model = st.selectbox("Modèle des agents", choices, key="cfg_model", disabled=locked)
            if models and model not in models:
                st.warning("Ce modèle n'est pas installé. Aucun téléchargement automatique.")
                st.code(f"ollama pull {model}", language="powershell")
        with st.expander("Sujet et consignes", expanded=True):
            topic = st.text_area("Sujet du débat", key="cfg_topic", disabled=locked)
            initial = st.text_area("Contexte initial", key="cfg_initial_context", disabled=locked)
            language = st.text_input("Langue imposée", key="cfg_language", disabled=locked)
            constraints = st.text_area(
                "Contraintes communes",
                key="cfg_constraints",
                disabled=locked,
                placeholder="Ex. : 150 mots maximum, citer les hypothèses, pas de listes…",
            )
            rules = st.text_area("Règles communes", key="cfg_rules", height=200, disabled=locked)
            human_name = st.text_input("Votre nom", key="cfg_human_name", disabled=locked)
        with st.expander("Agent A"):
            agent_a = agent_fields("agent_a", disabled=locked)
        with st.expander("Agent B"):
            agent_b = agent_fields("agent_b", disabled=locked)
        with st.expander("Recherche web", expanded=True):
            web_enabled = st.checkbox(
                "Activer la recherche web",
                key="cfg_web_enabled",
                disabled=locked,
            )
            backend_values = list(BACKENDS.values())
            web_backend = st.selectbox(
                "Moteur de recherche",
                backend_values,
                key="cfg_web_backend",
                format_func=lambda value: next(k for k, v in BACKENDS.items() if v == value),
                disabled=locked,
            )
            web_queries = st.number_input(
                "Requêtes maximum par intervention",
                1,
                3,
                key="cfg_web_max_queries",
                disabled=locked,
            )
            web_results = st.number_input(
                "Résultats maximum par requête",
                1,
                5,
                key="cfg_web_max_results",
                disabled=locked,
            )
            web_timeout = st.number_input(
                "Délai web par moteur (secondes)",
                3,
                30,
                key="cfg_web_timeout_seconds",
                disabled=locked,
            )
            st.caption(
                "Sans clé API. Les agents choisissent leurs requêtes ; seuls les termes "
                "recherchés sont envoyés aux moteurs. Les résultats fournissent des "
                "liens et extraits, pas une lecture intégrale des pages."
            )
        with st.expander("Tours et performances"):
            rounds = st.number_input(
                "Tours maximum (un tour = A puis B)",
                1,
                100,
                key="cfg_max_rounds",
                disabled=locked,
            )
            max_tokens = st.number_input(
                "Tokens maximum par réponse",
                128,
                8192,
                step=128,
                key="cfg_max_tokens",
                disabled=locked,
            )
            context_tokens = st.number_input(
                "Contexte Ollama (tokens)",
                4096,
                131072,
                step=4096,
                key="cfg_context_tokens",
                disabled=locked,
            )
            delay = st.number_input(
                "Délai entre les réponses (secondes)",
                0.0,
                60.0,
                step=0.5,
                key="cfg_delay_seconds",
                disabled=locked,
            )
            early_stop = st.checkbox(
                "Arrêt anticipé structuré", key="cfg_early_stop", disabled=locked
            )
            consensus = st.number_input(
                "Tours consécutifs de consensus pour arrêter",
                1,
                10,
                key="cfg_consensus_rounds",
                disabled=locked,
            )
            st.caption("Les appels à Ollama sont séquentiels, avec des contextes séparés.")
        with st.expander("Mémoire et compression"):
            compression = st.checkbox(
                "Résumer les échanges anciens si nécessaire",
                key="cfg_compression",
                disabled=locked,
            )
            recent = st.number_input(
                "Échanges récents à préserver avant résumé",
                2,
                50,
                key="cfg_recent_messages",
                disabled=locked,
            )
            summary_tokens = st.number_input(
                "Tokens maximum du résumé",
                128,
                4096,
                step=128,
                key="cfg_summary_tokens",
                disabled=locked,
            )
            st.caption("L'export garde tout. Sans compression, seul le contexte ancien est écarté.")
        with st.expander("Juge optionnel"):
            judge_enabled = st.checkbox("Activer le juge", key="cfg_judge_enabled", disabled=locked)
            judge_choices = list(dict.fromkeys([st.session_state.cfg_judge_model, *models]))
            judge_model = st.selectbox(
                "Modèle du juge",
                judge_choices,
                key="cfg_judge_model",
                disabled=locked,
            )
            judge_prompt = st.text_area(
                "Prompt du juge",
                key="cfg_judge_system_prompt",
                height=240,
                disabled=locked,
            )
            judge_temperature = st.slider(
                "Température du juge",
                0.0,
                2.0,
                step=0.05,
                key="cfg_judge_temperature",
                disabled=locked,
            )
    return DebateConfig(
        topic=topic,
        initial_context=initial,
        rules=rules,
        language=language,
        constraints=constraints,
        human_name=human_name,
        model=model,
        ollama_url=url,
        max_rounds=int(rounds),
        max_tokens=int(max_tokens),
        context_tokens=int(context_tokens),
        delay_seconds=float(delay),
        early_stop=early_stop,
        consensus_rounds=int(consensus),
        compression=compression,
        recent_messages=int(recent),
        summary_tokens=int(summary_tokens),
        agent_a=agent_a,
        agent_b=agent_b,
        judge=JudgeConfig(judge_enabled, judge_model, judge_prompt, judge_temperature),
        web=WebSearchConfig(
            web_enabled, int(web_queries), int(web_results), int(web_timeout), web_backend
        ),
    )


def live_controls(runner: DebateRunner) -> None:
    config = runner.engine.config
    disabled = runner.busy or runner.running
    with st.expander("Modifier la langue et les contraintes en pause"):
        with st.form("live_controls"):
            language = st.text_input("Langue pour les prochaines réponses", value=config.language)
            constraints = st.text_area("Contraintes pour tous les agents", value=config.constraints)
            col_a, col_b = st.columns(2)
            with col_a:
                constraints_a = st.text_area("Contraintes de A", value=config.agent_a.constraints)
            with col_b:
                constraints_b = st.text_area("Contraintes de B", value=config.agent_b.constraints)
            compression = st.checkbox(
                "Activer la compression si nécessaire", value=config.compression
            )
            web_enabled = st.checkbox(
                "Recherche web pour les prochaines réponses", value=config.web.enabled
            )
            col_web_a, col_web_b = st.columns(2)
            with col_web_a:
                web_a = st.checkbox("Accès web de A", value=config.agent_a.web_access)
            with col_web_b:
                web_b = st.checkbox("Accès web de B", value=config.agent_b.web_access)
            apply = st.form_submit_button("Appliquer les consignes", disabled=disabled)
        if apply:
            try:
                runner.engine.update_controls(
                    language,
                    constraints,
                    constraints_a,
                    constraints_b,
                    compression,
                    web_enabled=web_enabled,
                    web_a=web_a,
                    web_b=web_b,
                )
                runner.error = None
                st.success("Consignes appliquées aux prochaines réponses et au juge.")
            except ValueError as exc:
                st.error(str(exc))
        st.caption(
            "Les consignes sont envoyées au modèle. Leur respect dépend aussi de ses capacités."
        )


def show_history(engine: DebateEngine) -> None:
    avatars = {"a": "🔵", "b": "🟠", "human": "👤"}
    for message in engine.state.messages:
        with st.chat_message(
            "user" if message.speaker_id == "human" else "assistant",
            avatar=avatars[message.speaker_id],
        ):
            st.caption(f"Tour {message.round_number} · {message.speaker_name}")
            st.markdown(message.content)
            if message.research:
                with st.expander(
                    f"Recherches web · {message.speaker_name} · tour {message.round_number}"
                ):
                    if message.research.queries:
                        st.write("Requêtes : " + " ; ".join(message.research.queries))
                    elif not message.research.errors:
                        st.caption("Aucune recherche nécessaire selon l'agent.")
                    for error in message.research.errors:
                        st.warning(error)
                    for source in message.research.sources:
                        st.link_button(f"[{source.source_id}] {source.title}", source.url)
                        st.write(source.snippet)
                        st.caption(f"Extrait de recherche obtenu le {source.retrieved_at}")
            if message.speaker_id != "human":
                st.caption(f"{message.duration_seconds:.1f} s · {message.tokens or '—'} tokens")
    if engine.state.judge_result:
        with st.chat_message("assistant", avatar="⚖️"):
            st.caption("Rapport du juge")
            st.markdown(engine.state.judge_result.content)
            if engine.state.judge_context_reduced:
                st.info("Le juge a analysé les échanges récents et un résumé des échanges anciens.")


@st.fragment(run_every=0.5)
def debate_panel(config: DebateConfig) -> None:
    runner: DebateRunner | None = st.session_state.get("runner")
    if runner:
        runner.poll()
    st.title("Faites avancer les idées.")
    st.write("Deux points de vue, un débat local. Prenez la parole et fixez les règles.")
    automatic = st.toggle("Enchaîner automatiquement les réponses", value=False, key="automatic")
    if runner and runner.running and not automatic:
        runner.pause()
    st.caption(
        "En mode pas à pas, chaque clic produit une réponse. En automatique, "
        "mettez en pause pour intervenir ou changer les consignes."
    )
    buttons = st.columns(5)
    if buttons[0].button("Lancer", disabled=runner is not None, type="primary", width="stretch"):
        try:
            config.validate()
            client = OllamaClient(config.ollama_url)
            installed = client.list_models()
            client.require_model(config.model, installed)
            if config.judge.enabled:
                client.require_model(config.judge.model, installed)
            runner = DebateRunner(DebateEngine(config))
            st.session_state.runner = runner
            runner.advance(automatic=automatic)
            st.rerun()
        except (ValueError, OllamaError) as exc:
            st.error(str(exc))
    can_advance = runner is not None and not runner.busy and not runner.engine.finished
    if buttons[1].button("Réponse suivante / reprendre", disabled=not can_advance, width="stretch"):
        runner.advance(automatic=automatic)
    if buttons[2].button(
        "Pause",
        disabled=runner is None or (not runner.busy and not runner.running),
        width="stretch",
    ):
        runner.pause()
    if buttons[3].button(
        "Arrêter", disabled=runner is None or runner.engine.finished, width="stretch"
    ):
        runner.stop()
    if buttons[4].button("Réinitialiser", disabled=runner is None or runner.busy, width="stretch"):
        del st.session_state.runner
        st.rerun()
    if runner is None:
        st.info("Choisissez le sujet et les agents dans la barre latérale, puis lancez le débat.")
        return

    engine = runner.engine
    progress = engine.agent_turns / (engine.config.max_rounds * 2)
    st.progress(
        min(1.0, progress),
        text=f"{engine.agent_turns} / {engine.config.max_rounds * 2} réponses d'agents",
    )
    if runner.error:
        st.error(runner.error)
    if runner.busy:
        name = (
            "Juge"
            if runner.job == "judge"
            else (
                engine.config.agent_a.name
                if engine.next_speaker == "a"
                else engine.config.agent_b.name
            )
        )
        st.info(
            f"{name} prépare sa réponse et ses recherches éventuelles… "
            "La pause ou l'arrêt prend effet après cette intervention."
        )
    elif engine.finished:
        st.success(engine.state.stop_reason)
    elif runner.running:
        st.caption("En attente de la prochaine réponse…")
    else:
        st.info(
            "En pause : donnez votre avis, modifiez les consignes ou demandez la réponse suivante."
        )

    live_controls(runner)
    for warning in engine.state.warnings:
        st.warning(warning)
    show_history(engine)
    human_text = st.chat_input(
        "Votre avis ou votre question aux agents…",
        disabled=runner.busy or runner.running or engine.finished,
    )
    if human_text:
        try:
            engine.add_human_message(human_text)
            runner.error = None
            st.rerun()
        except ValueError as exc:
            st.error(str(exc))
    if engine.finished and engine.config.judge.enabled:
        if st.button("Générer / refaire le rapport du juge", disabled=runner.busy):
            runner.judge()
    if engine.state.messages:
        with st.expander("Exporter le débat", expanded=engine.finished):
            col_md, col_json = st.columns(2)
            stamp = engine.state.created_at[:19].replace(":", "-")
            col_md.download_button(
                "Exporter Markdown",
                export_markdown(engine),
                f"debate-{stamp}.md",
                "text/markdown",
                width="stretch",
            )
            col_json.download_button(
                "Exporter JSON",
                export_json(engine),
                f"debate-{stamp}.json",
                "application/json",
                width="stretch",
            )
            st.caption(
                "Les exports contiennent votre discussion et sa configuration. "
                "Ils restent sur votre machine jusqu'à ce que vous les partagiez."
            )


defaults = DebateConfig(ollama_url=os.environ.get("OLLAMA_URL", "http://localhost:11434"))
seed_widgets(defaults)
debate_panel(configuration())
