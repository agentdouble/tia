# Contrat des skills TIA

## Structure minimale

```text
nom-du-skill/
└── SKILL.md
```

Chaque enfant direct de `.agents/skills` ou `~/.config/tia/skills` est considéré comme un
skill et doit donc contenir un `SKILL.md` valide.

## Structure complète

```text
nom-du-skill/
├── SKILL.md
├── agents/
│   └── openai.yaml
├── scripts/
├── references/
└── assets/
```

- `SKILL.md` contient les métadonnées et la procédure chargée à l'activation.
- `agents/openai.yaml` contient des métadonnées facultatives destinées aux interfaces.
- `scripts/` contient le code exécutable et réutilisable.
- `references/` contient les informations à lire seulement quand elles sont utiles.
- `assets/` contient les fichiers utilisés pour produire une sortie.

## SKILL.md

```markdown
---
name: nom-du-skill
description: Action du skill et situations précises qui doivent le déclencher.
---

# Procédure

Instructions impératives et concises.
```

Contraintes :

- nom identique au dossier ;
- minuscules, chiffres et tirets seulement ;
- 64 caractères maximum ;
- description non vide ;
- corps Markdown non vide ;
- encodage UTF-8 ;
- taille maximale de `SKILL.md` : 131 072 caractères ;
- inventaire maximal par défaut : 512 fichiers ;
- aucun lien symbolique ne peut sortir du dossier du skill.

## Chargement

TIA injecte uniquement `name` et `description` dans le catalogue permanent. Le
`SKILL.md` complet et l'inventaire du dossier sont chargés après une activation
explicite `$nom-du-skill` ou un appel du tool `load_skill`.

Le code embarqué ne crée aucune permission. Il doit être exécuté avec les tools déjà
accordés à TIA, par exemple `run_bash`.
