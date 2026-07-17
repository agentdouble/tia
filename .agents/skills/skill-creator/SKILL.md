---
name: skill-creator
description: Crée, structure, met à jour et valide des skills TIA autonomes contenant un SKILL.md et éventuellement du code, des scripts, des références ou des assets. Utiliser ce skill lorsque l'utilisateur demande de créer un nouveau skill, de transformer une procédure répétitive en skill, de modifier un skill existant ou de vérifier sa conformité.
---

# Créer un skill TIA

Créer un dossier de skill autonome, concis et directement exécutable par TIA.

## Choisir la destination

- Utiliser `<workspace>/.agents/skills` par défaut pour un skill propre au projet.
- Utiliser `~/.config/tia/skills` seulement si l'utilisateur demande un skill personnel ou global.
- Conserver le dossier existant lors de la mise à jour d'un skill déjà présent.
- Refuser un nom déjà utilisé dans une autre racine tant que le doublon existe.

## Comprendre le besoin

1. Identifier les demandes concrètes qui doivent déclencher le skill.
2. Déterminer le résultat attendu et sa méthode de vérification.
3. Demander uniquement les informations manquantes qui changeraient réellement le résultat.
4. Repérer le code, les références ou les modèles utiles aux exécutions répétées.

## Initialiser un nouveau skill

Normaliser le nom en minuscules avec des chiffres et des tirets, sur 64 caractères
maximum. Le dossier doit porter exactement ce nom.

Exécuter le générateur embarqué depuis le dossier de ce skill :

```bash
python scripts/init_skill.py <nom> \
  --path <racine-de-skills> \
  --resources scripts,references,assets \
  --interface display_name="<nom lisible>" \
  --interface short_description="<description UI de 25 à 64 caractères>" \
  --interface default_prompt="Utilise $<nom> pour <exemple concret>."
```

Omettre de `--resources` les dossiers inutiles. Ne jamais utiliser `--examples`
pour le résultat final : les placeholders ne sont pas du contenu métier.

Pour une mise à jour, ne pas relancer le générateur. Inspecter et modifier le dossier
existant.

## Écrire le skill

- Garder `SKILL.md` obligatoire et inférieur à 500 lignes.
- Limiter son frontmatter à `name` et `description`.
- Décrire dans `description` ce que fait le skill et les demandes qui le déclenchent.
- Écrire le corps à l'impératif avec la procédure essentielle seulement.
- Placer le code déterministe et réutilisable dans `scripts/`.
- Placer la documentation chargée à la demande dans `references/`.
- Placer les modèles ou fichiers destinés aux sorties dans `assets/`.
- Référencer chaque ressource utile directement depuis `SKILL.md` et préciser quand
  la lire ou l'exécuter.
- Ne pas créer de README, changelog, guide d'installation ou autre documentation
  auxiliaire dans le dossier du skill.

Lire `references/skill-format.md` pour le contrat exact de TIA. Lire
`references/openai_yaml.md` uniquement pour personnaliser `agents/openai.yaml`.

## Tester le code embarqué

Exécuter réellement chaque script ajouté avec un cas représentatif. Vérifier son
code de sortie et ses effets. Ne pas considérer un script comme valide uniquement
parce qu'il compile.

## Valider

Exécuter le validateur embarqué :

```bash
python scripts/quick_validate.py <chemin-du-dossier-du-skill>
```

Corriger toutes les erreurs, relancer le validateur, puis vérifier que TIA détecte
le skill au prochain tour. Tester enfin une activation explicite avec `$<nom>`.

## Restituer

Indiquer le chemin créé ou modifié, les ressources embarquées, la commande de
validation et le résultat du test réel.
