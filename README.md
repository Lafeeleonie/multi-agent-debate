# Multi-agent Debate · Agora locale

Application **locale** de débat avec deux agents IA Ollama, votre participation et un juge
optionnel. Interface Streamlit en français, langue des réponses librement configurable.
Pas de clé API, de base de données ou de service cloud nécessaire. Licence MIT pour le code ;
les poids des modèles ne sont pas inclus et conservent leurs propres licences.

## Démarrer sous Windows

Prérequis : **Python 3.12+** ([installation](https://www.python.org/downloads/)) et
[Ollama](https://ollama.com/download/windows), lancé en arrière-plan.

1. Clonez ce dépôt ou décompressez son ZIP dans un dossier.
2. Si vous n'avez pas déjà de modèle local, téléchargez-en un volontairement :

   ```powershell
   ollama pull huihui_ai/qwen3.5-abliterated:35b
   ```

3. **Double-cliquez sur `lancer.bat`.** Le lanceur prépare `.venv`, installe les dépendances
   au premier lancement ou lorsqu'elles changent et ouvre `http://localhost:8501`.
   Gardez la fenêtre ouverte ; `Ctrl+C` arrête le serveur.

L'installation initiale des dépendances Python nécessite Internet. Ensuite, le débat se déroule
sur votre machine. Le lanceur **ne télécharge jamais de modèle** et ne démarre pas une seconde
instance d'Ollama. L'application utilise uniquement une adresse de boucle locale
(`localhost`, `127.0.0.1`, `[::1]`) et écarte les modèles cloud, y compris les alias indiquant
un serveur distant dans `/api/show`.

Le modèle par défaut est `huihui_ai/qwen3.5-abliterated:35b`, mais **tout modèle conversationnel
local installé et compatible avec l'API chat Ollama peut être sélectionné**. Le terme
« abliterated » ne garantit ni une meilleure qualité ni une absence totale de refus.

### Installation manuelle

Depuis le dossier du projet, dans PowerShell :

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m streamlit run app.py --server.headless=false
```

Ou, dans un environnement Python 3.12+ déjà préparé :

```powershell
pip install -r requirements.txt
streamlit run app.py
```

Sous Linux/WSL2, installez Ollama depuis [son site](https://ollama.com/download/linux), puis :

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

Sous WSL2, Ollama doit être accessible sur la boucle locale de cet environnement. Le plus simple
est d'y exécuter aussi Ollama. Un serveur Windows exposé sur une adresse réseau n'est pas accepté.

## Interface et utilisation

La barre latérale contient le sujet, les règles, la langue imposée, les contraintes communes,
les configurations A/B, les limites de génération, la mémoire et le juge. Le **sélecteur de modèles**
est rempli depuis votre Ollama ; « Actualiser les modèles » rafraîchit la liste. Le juge peut
utiliser le même modèle ou un autre modèle installé. Si Ollama est absent, une erreur indique
de lancer son application ou `ollama serve`. Un modèle manquant affiche sa commande de téléchargement.

La page principale affiche A en bleu, B en orange et vos messages avec une icône de personne,
avec le numéro de tour, la durée de génération et le compteur de tokens fourni par Ollama.
Le rapport du juge et les exports apparaissent à la suite.

1. Choisissez un preset, cliquez sur « Remplir les champs », puis adaptez chaque champ.
2. Définissez le sujet, la langue et les contraintes. Exemple : « Français », « 150 mots maximum,
   distinguer faits et hypothèses », avec « un exemple concret » pour A et « une objection » pour B.
3. Cliquez sur **Lancer**. Par défaut, une seule réponse est générée.
4. Écrivez votre avis dans le champ de discussion pendant une pause. Il est intégré à l'historique
   public ; les deux agents le voient lors de leurs prochaines interventions.
5. Cliquez sur **Réponse suivante / reprendre**. Votre avis ne consomme pas le tour d'un agent.
6. Pour enchaîner les réponses, activez le mode automatique avant de lancer ou reprendre.
   **Pause** conserve la réponse en cours et empêche l'appel suivant. La saisie humaine est disponible
   lorsque l'appel est terminé et que le débat est en pause.
7. Pendant une pause, ouvrez **Modifier la langue et les contraintes en pause** et appliquez vos
   nouvelles consignes. Elles valent pour les prochaines réponses et le juge, sans réécrire les
   anciennes interventions. Le changement est enregistré dans les exports.
8. **Arrêter** termine le débat après l'appel en cours. **Réinitialiser** efface la session courante,
   une fois l'appel terminé ; exportez d'abord si vous souhaitez la conserver.

Un **tour complet = une intervention de A puis de B** : 10 tours permettent donc 20 réponses
d'agents, auxquelles s'ajoutent vos interventions. L'application affiche les réponses complètes
au fur et à mesure ; cette version utilise des appels sans streaming pour valider chaque sortie
avant de l'inscrire dans l'historique. Pendant les générations, l'interface reste disponible.

Les consignes sont transmises dans les prompts système. Leur respect sémantique dépend du modèle :
une limite de mots en langage naturel n'est pas un validateur strict. La limite de tokens est,
elle, transmise comme limite de génération à Ollama.

## Agents, mémoire et arrêt anticipé

Les agents partagent **le modèle chargé par Ollama**, mais possèdent des historiques logiques
et des mémoires privées distincts. Le moteur appelle A puis B, jamais les deux simultanément.
Les réponses passées d'un agent ont le rôle `assistant` **uniquement dans son propre contexte**.
Les messages de l'adversaire et de l'humain ont le rôle `user`, avec une identité explicite.
Les consignes de langue et de contraintes restent dans le message `system` à chaque appel.

L'historique public est conservé intégralement pour l'affichage et les exports. Seul le contexte
envoyé au modèle est réduit lorsqu'il devient trop long :

- sans compression, le moteur conserve les échanges les plus récents ; un avertissement le signale ;
- avec compression, il résume progressivement les anciens échanges en lots bornés, puis garde
  les échanges récents intacts autant que le budget le permet ; un avertissement signale la réduction ;
- les instructions et la dernière intervention ne sont jamais tronquées silencieusement : si elles
  sont trop longues, une erreur demande d'ajuster la taille du contexte ;
- le budget d'entrée utilise une estimation prudente par octets UTF-8, avec une réserve pour la
  réponse et l'enveloppe de messages. Ce n'est pas le tokenizer exact du modèle.

La mémoire privée est une courte note initiale, propre à chaque agent, bornée à 2048 octets UTF-8.
Lorsque l'arrêt structuré est actif, l'agent peut mettre à jour cette note. L'adversaire et le juge
ne la reçoivent jamais. Les notes privées dynamiques ne figurent pas dans les exports ; les notes
**initiales**, qui appartiennent à la configuration saisie, figurent dans le JSON.

L'arrêt anticipé demande une réponse conforme à un schéma JSON : `response`, `continue_debate`
(booléen strict) et `private_note`. Aucun mot dans le texte ne peut déclencher l'arrêt.
Par défaut, **les deux agents** doivent voter `false` pendant **deux tours complets consécutifs**.
Un avis humain ou un changement de consignes remet ce consensus à zéro, y compris les votes du
tour en cours. Si votre modèle ne gère pas bien les sorties structurées, désactivez cette fonction
avant un nouveau débat : les réponses deviennent libres et la mémoire privée initiale reste fixe.
Une sortie invalide ou une erreur réseau ne consomme pas de tour ; vous pouvez réessayer.

## Juge et exports

Le juge optionnel intervient à la fin, également après un arrêt manuel. Il utilise un contexte
indépendant contenant le débat public, sans mémoire privée et sans modifier les historiques A/B.
Son prompt par défaut demande un résumé, les arguments, réfutations, concessions, erreurs,
deux notes sur 10 et les questions ouvertes. Il peut désigner un vainqueur si l'écart est net.
La langue et les contraintes communes s'appliquent aussi au rapport et priment sur son format.

Si le débat entier ne tient pas dans le contexte du juge, le moteur **refuse une analyse tronquée**.
Activez la compression dans les consignes en pause, puis cliquez sur « Générer / refaire le rapport
du juge ». Avec compression, le juge lit un résumé de tous les échanges anciens et les échanges
récents ; cette limite est signalée. Les résumés peuvent omettre des nuances et ne remplacent pas
une lecture humaine. Un autre modèle de juge peut nécessiter un rechargement et davantage de VRAM.

Le panneau **Exporter le débat** propose :

- **Markdown** : texte lisible, intervenants, consignes, changements, métadonnées et rapport ;
- **JSON** : version du schéma, sujet, dates UTC, configuration initiale et actuelle, messages
  ordonnés (y compris vos avis), changements de consignes, juge et métadonnées de mémoire.

Les exports sont des téléchargements du navigateur, jamais des fichiers ajoutés automatiquement
au dépôt. Ils peuvent contenir ce que vous avez saisi : choisissez ce que vous souhaitez partager.
Les fichiers `.env`, les exports dans `exports/`, environnements virtuels, secrets Streamlit,
poids de modèles et caches usuels sont ignorés par Git. `.env.example` documente une variable
optionnelle `OLLAMA_URL` ; les fichiers `.env` ne sont pas lus automatiquement.

## Performances et matériel

Valeurs initiales : 10 tours, température 0,7, 1200 tokens de réponse, contexte Ollama de **32768**
tokens. L'application n'utilise pas 256k par défaut. La cible inclut RTX 5090 32 Go, Ryzen 9 9950X
et 64 Go de RAM, mais la mémoire requise dépend du modèle, de sa quantification et du contexte.

En cas de manque de VRAM, commencez par **8192 ou 16384 tokens de contexte**, un modèle plus petit
ou une quantification plus légère. La taille du contexte affecte le cache KV. `num_ctx` et
`num_predict` sont envoyés à chaque appel et `keep_alive` est fixé à cinq minutes. Le mode de
raisonnement est désactivé via `think: false` quand le modèle le prend en charge. Le compteur de
tokens comprend l'enveloppe JSON éventuelle. Le résumé ajoute des appels lorsqu'il est activé.

Le projet ne lance aucun processus de modèle : **Ollama** gère le chargement, la réutilisation et
l'éventuel déchargement des poids. Les deux agents utilisent normalement la même instance du
même modèle. Les autres applications connectées à Ollama restent indépendantes et peuvent
consommer de la mémoire supplémentaire. Plusieurs onglets de débat peuvent aussi générer des
appels concurrents ; gardez une seule session active pour des performances prévisibles.

## Architecture

```text
app.py                  Interface Streamlit et configuration
lancer.bat / lancer.ps1 Lanceur Windows, environnement Python local
debate/
  models.py             Dataclasses de configuration et historique
  agents.py             Prompts et attribution correcte des rôles
  ollama_client.py       API HTTP locale, erreurs et contrôle des modèles
  memory.py             Budget de contexte, mémoire et résumés progressifs
  engine.py             Alternance A/B, avis humains, consignes et consensus
  runner.py             Un appel en arrière-plan, pause et reprise
  judge.py              Analyse indépendante à la fin
  presets.py            Quatre configurations éditables
  export.py             Markdown et JSON
tests/                  Tests du moteur, mémoire, client, interface et exports
scripts/smoke_ollama.py  Vérification réelle facultative avec un modèle installé
```

Le cœur Python ne dépend pas de Streamlit et peut être utilisé ou testé séparément. Les sources
et mémoires sont déjà séparées : un RAG, des documents propres aux agents ou une persistance
pourraient être ajoutés aux points de construction de contexte sans changer l'attribution des rôles.

## Exemples de débats

| Preset | Exemple de sujet | Votre intervention possible |
| --- | --- | --- |
| Débat philosophique | Une IA peut-elle être moralement responsable ? | « Définissez d'abord la responsabilité. » |
| Peer review scientifique | Une étude observationnelle permet-elle cette conclusion ? | « Examinez le biais de sélection. » |
| Conception technique | Sauvegarde locale : snapshots ou copie incrémentale ? | « Le budget est limité à 200 €. » |
| Red team intellectuelle | Le télétravail améliore-t-il toujours la productivité ? | « Quel test pourrait falsifier cette hypothèse ? » |

Les presets remplissent des champs, sans verrouiller les choix après leur application.

## Tests

Dans l'environnement virtuel :

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m ruff format --check .
```

Les tests ordinaires simulent Ollama et ne requièrent ni GPU ni réseau. Ils vérifient notamment
l'attribution des rôles, l'isolation des mémoires, les avis humains, les consignes, le consensus,
les erreurs, les limites de contexte, les exports, les pauses et le parcours Streamlit.

Pour un test réel volontaire, avec le modèle déjà installé (trois générations courtes) :

```powershell
.\.venv\Scripts\python.exe scripts/smoke_ollama.py
```

Ce script utilise 8192 tokens de contexte et 512 tokens par réponse pour limiter la consommation.
Il vérifie A, un avis humain, B après un changement de langue, puis le juge. Options :
`--model NOM`, `--url URL_LOCALE`, `--context 16384`. Il n'évalue pas automatiquement la qualité
du débat ni le respect sémantique de la langue : lisez les réponses affichées.

## Limites et suite possible

- Pas de streaming dans cette première version ; les interventions validées apparaissent une à une.
- Une pause ou un arrêt n'annule pas un appel HTTP déjà envoyé ; délai maximal de génération : 600 s.
- Les sessions sont en RAM. Un redémarrage ou une nouvelle session navigateur perd l'état : exportez
  avant de fermer. Pas d'import ou de reprise depuis un JSON dans cette version.
- Les modèles peuvent halluciner, manquer une objection ou négliger une contrainte. Le juge ne
  constitue pas une preuve de vérité. Les sorties structurées doivent être prises en charge par le modèle.
- Résumés susceptibles de perdre des détails et estimation de contexte volontairement prudente.
- Le nombre de tours et les modèles restent fixes pendant un débat ; langue et contraintes sont modifiables.
- Pistes futures : streaming validé, sauvegarde/reprise, sources distinctes, RAG et tokenizer adapté.

API utilisées : [chat Ollama](https://docs.ollama.com/api/chat),
[sorties structurées](https://docs.ollama.com/capabilities/structured-outputs),
[fragments Streamlit](https://docs.streamlit.io/develop/api-reference/execution-flow/st.fragment).
