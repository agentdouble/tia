# TIA moteur

Première version d'un agent Python construit avec Pydantic AI. Le modèle peut
décider d'appeler un tool `run_bash`, lire son résultat structuré, puis poursuivre
son raisonnement jusqu'à une réponse finale.

Avant chaque tour utilisateur, le runtime relit les instructions globales puis
`AGENTS.md` à la racine du workspace et les ajoute au prompt général de l'agent.

Il découvre également des dossiers de skills. Chaque skill contient obligatoirement
un `SKILL.md` et peut embarquer du code, des scripts, des références et des assets.

## Installation

Prérequis : Python 3.11+ et `uv`.

```bash
uv sync
uv tool install --editable /Users/jeremy/projet/tia
tia init
```

`tia init` crée sans écraser les fichiers existants :

```text
~/.config/tia/
├── .env
├── agent.setup.yaml
└── skills/
```

Ajoute ensuite ta clé OpenAI dans `~/.config/tia/.env` :

```dotenv
OPENAI_API_KEY=sk-...
```

Un lancement normal initialise aussi automatiquement cette racine si elle manque.
La commande `tia` peut alors être utilisée depuis n'importe quel dossier.

Le modèle est configurable avec `TIA_MODEL`. Le préfixe explicite
`openai-responses:` utilise l'API Responses via Pydantic AI.

## Utilisation

Conversation interactive :

```bash
tia
```

Demande unique :

```bash
tia "Liste les fichiers Python du projet"
```

Autre workspace ou autre modèle :

```bash
tia --workspace /chemin/du/projet --model openai-responses:gpt-5.6-luna
```

Dans le dépôt de développement, `uv run tia` reste équivalent.

Sans `--workspace`, TIA part du dossier courant et remonte jusqu'au premier dossier
contenant `agent.setup.yaml`, `AGENTS.md` ou `.agents/`. Sans marqueur, le dossier
courant reste le workspace et seule la configuration globale est nécessaire.

Dans le mode interactif, saisis `exit` pour quitter.

Lorsqu'un tool est utilisé, la CLI affiche uniquement son nom. Pour `run_bash`,
elle ajoute la commande exécutée sur la même ligne, sans afficher le résultat brut.

## Mode headless JSONL

Une application peut lancer TIA comme sous-processus sans dépendre du rendu
terminal :

```bash
tia run --format jsonl --workspace /chemin/du/projet \
  "Liste les fichiers Python"
```

Le prompt peut aussi être transmis sur `stdin` :

```bash
printf 'Liste les fichiers Python' | tia run --format jsonl
```

Dans ce mode, `stdout` contient uniquement des événements JSONL compacts et
versionnés. Chaque événement porte `session_id`, `run_id` et une `sequence` :

```json
{"schema_version":1,"type":"run.started","session_id":"...","run_id":"...","sequence":0,"model":"test","workspace":"/tmp","restored":false}
{"schema_version":1,"type":"text.delta","session_id":"...","run_id":"...","sequence":1,"part_index":0,"content":"Bonjour"}
{"schema_version":1,"type":"run.completed","session_id":"...","run_id":"...","sequence":2,"output":"Bonjour","usage":{"input_tokens":10,"output_tokens":2,"cache_write_tokens":0,"cache_read_tokens":0,"requests":1,"tool_calls":0}}
```

Les types V1 sont `run.started`, `instructions.changed`, `skill.activated`,
`text.started`, `text.delta`, `tool.called`, `tool.completed`, `run.completed`
et `run.failed`. Les logs et erreurs de syntaxe vont sur `stderr`. Les codes de
sortie sont `0` pour un résultat, `1` pour une erreur de configuration ou de run,
`2` pour un usage invalide et `130` pour une interruption.

`--session-id` fixe l'identifiant de corrélation. Le stockage headless par défaut
reste en mémoire du processus : reprendre une conversation entre deux processus
demande un `SessionStore` persistant injecté dans le runtime.

## Coeur réutilisable

La CLI interactive et le mode headless utilisent le même coeur :

```python
from tia_moteur import TiaRuntime

runtime = TiaRuntime.from_environment(workspace="/chemin/du/projet")
session = runtime.create_session(session_id="conversation-123")

result = await session.run("Analyse le projet")

async for event in session.stream("Lance les tests"):
    print(event.type)
```

`TiaRuntime` accepte un modèle ou une `model_factory`, des tools ou toolsets statiques, des
`tool_factories`, des `toolset_factories`, un `SessionStore` et un provider de credentials. Les factories
reçoivent un contexte propre à la session ; les providers masquent leur
représentation et les valeurs de credentials connues sont retirées au mieux des
résultats et erreurs. Les payloads de tools sont bornés et doivent rester JSON. Un
tool injecté demeure toutefois une extension de confiance : il ne doit pas publier
volontairement d'autres données sensibles. `TiaSession` sérialise ses runs, conserve
l'historique et ne le persiste qu'après un résultat terminal réussi.

Un serveur MCP peut ainsi être injecté comme toolset Pydantic AI sans être couplé
au coeur de TIA. Le package embarque l'extra `mcp`; l'application hôte choisit le
transport, le serveur et les permissions réellement accordées.

## Configuration portable

TIA charge toujours `~/.config/tia`, puis ajoute la configuration optionnelle du
projet :

```text
mon-projet/
├── .env
├── agent.setup.yaml
├── AGENTS.md
└── .agents/
    └── skills/
```

Les priorités sont :

- environnement du processus, puis `.env` projet, puis `.env` global ;
- `agent.setup.yaml` projet fusionné par-dessus le setup global ;
- prompt interne, puis `AGENTS.md` global, puis `AGENTS.md` projet ;
- réunion de `~/.config/tia/skills` et `.agents/skills`, avec refus des doublons.

Un setup projet peut être partiel. Par exemple, pour désactiver seulement les
skills sans recopier le setup global :

```yaml
skills:
  enabled: false
```

## Skills

Les skills du projet vivent dans `.agents/skills`. Les skills personnels vivent
dans `~/.config/tia/skills`. Chaque enfant direct est un dossier autonome :

```text
.agents/skills/python-quality/
├── SKILL.md
├── scripts/
│   └── check.py
├── references/
└── assets/
```

`SKILL.md` commence par un frontmatter YAML et contient toujours des instructions :

```markdown
---
name: python-quality
description: Vérifie et corrige la qualité d'un projet Python.
---

# Procédure

Exécute `scripts/check.py`, corrige les erreurs puis relance la vérification.
```

Le nom doit être identique au dossier et utiliser des minuscules, chiffres ou
tirets. Les fichiers de code sont inventoriés lors du chargement du skill. Ils sont
exécutables uniquement avec les tools déjà accordés à l'agent.

Le prompt reçoit seulement les noms et descriptions au départ. Deux activations
sont disponibles :

- explicite avec `$python-quality` dans la demande ;
- automatique lorsque le modèle appelle `load_skill("python-quality")`.

Le système complet peut être désactivé dans `agent.setup.yaml` :

```yaml
skills:
  enabled: false
```

Dans ce mode, les dossiers ne sont pas scannés, aucun catalogue ou `SKILL.md` n'est
injecté, `$nom-du-skill` n'est pas activé et le tool `load_skill` n'est pas exposé.

La CLI affiche l'activation sous la forme `[skill] python-quality`. Le registre est
relu à chaque tour, refuse les doublons et bloque les liens symboliques qui sortent
du dossier. `TIA_SKILLS_DIRECTORY` et `TIA_GLOBAL_SKILLS_DIRECTORY` permettent de
changer les deux racines.

### Skill installé : skill-creator

Le projet fournit `.agents/skills/skill-creator`. Il crée, met à jour et valide
d'autres skills TIA. Il embarque son générateur, son validateur, les métadonnées UI
et les références du format. Pour l'activer explicitement :

```text
$skill-creator crée un skill pour analyser les logs Python
```

## Architecture

- `config.py` : configuration validée par Pydantic Settings ;
- `agent.setup.yaml` : activation des tools et politique des commandes ;
- `agent_setup.py` : validation Pydantic du fichier de setup ;
- `AGENTS.md` : conventions propres au workspace ;
- `workspace_instructions.py` : chargement borné et relecture par tour ;
- `portable_config.py` : bootstrap global, détection projet et couches `.env` ;
- `runtime.py` : configuration résolue, injection et création des sessions ;
- `session.py` : historique, exécution et traduction des événements provider ;
- `events.py` : protocole public JSONL versionné ;
- `session_store.py` : contrat de persistance et implémentation mémoire ;
- `credentials.py` : injection de secrets avec représentation masquée ;
- `skills/` : découverte, validation, activation et tool `load_skill` ;
- `agent.py` : prompt système et assemblage agent/outils ;
- `tools/bash.py` : exécution directe, timeout et limite de sortie ;
- `tools/bash_policy.py` : allowlist et refus avant création du processus ;
- `cli.py` : demande unique ou conversation avec historique ;
- `tests/` : tests sans consommation de tokens grâce à `TestModel`.

## Sécurité

Le fichier `agent.setup.yaml` active actuellement `run_bash` en mode
`unrestricted_shell`. Toute commande est transmise à `/bin/zsh -lc` sans filtrage :
pipes, redirections, créations, suppressions et enchaînements sont autorisés avec les
permissions complètes de l'utilisateur qui lance l'agent.

Le mode sécurisé `direct` reste disponible dans le moteur de policy. Il exécute une
allowlist sans shell et permet de définir `forbidden_executables` et `command_rules`.
Le mode non restreint actif n'est pas une sandbox et ne doit pas recevoir de demandes
non fiables.

## Tests

```bash
uv run pytest
```
