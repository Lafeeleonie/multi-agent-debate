from debate.models import DebateConfig

PRESET_NAMES = (
    "Débat philosophique",
    "Peer review scientifique",
    "Conception technique",
    "Red team intellectuelle",
)


def get_preset(name: str) -> DebateConfig:
    config = DebateConfig()
    if name == "Débat philosophique":
        return config
    if name == "Peer review scientifique":
        config.topic = "Cette hypothèse et son protocole permettent-ils une conclusion fiable ?"
        config.agent_a.role = "Auteur scientifique"
        config.agent_a.objective = (
            "Défendre l'hypothèse et le protocole, reconnaître leurs limites."
        )
        config.agent_b.role = "Reviewer sceptique"
        config.agent_b.objective = (
            "Examiner validité, biais, reproductibilité et contre-hypothèses."
        )
        judge_role = "éditeur scientifique"
    elif name == "Conception technique":
        config.topic = "Quelle architecture retenir pour un service de sauvegarde local ?"
        config.agent_a.role = "Ingénieur proposant une solution"
        config.agent_a.objective = "Proposer une solution concrète et justifier les compromis."
        config.agent_b.role = "Reviewer technique"
        config.agent_b.objective = (
            "Chercher les failles, coûts, risques et problèmes d'exploitation."
        )
        judge_role = "responsable technique"
    elif name == "Red team intellectuelle":
        config.topic = "Cette hypothèse résiste-t-elle à une tentative de falsification ?"
        config.agent_a.role = "Porteur d'hypothèse"
        config.agent_a.objective = "Formuler une hypothèse testable et la défendre avec rigueur."
        config.agent_b.role = "Contradicteur chargé de la falsification"
        config.agent_b.objective = "Chercher des tests discriminants et des contre-exemples."
        judge_role = "arbitre impartial"
    else:
        raise ValueError(f"Preset inconnu : {name}")
    config.judge.enabled = True
    config.judge.system_prompt = (
        f"Tu interviens en tant que {judge_role}.\n" + config.judge.system_prompt
    )
    return config
