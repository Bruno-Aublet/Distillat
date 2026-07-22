---
name: bump-version
description: Procédure pour bumper le numéro de version de Distillat (app/__version__.py, version_info.txt, CHANGELOG.md, README.md, index.html). À utiliser uniquement sur demande explicite et précise du numéro cible (voir règle 1 de CLAUDE.md, ne jamais bumper de sa propre initiative).
---

# Bumper le numéro de version de Distillat

Uniquement sur demande explicite et précise du numéro cible (règle 1 de
`CLAUDE.md`). Une fois la demande reçue, mettre à jour dans cet ordre :

1. **`app/__version__.py`** : `VERSION = "x.y.z"`. Source unique de vérité,
   affichée dans le titre de la fenêtre (`main_window.tr("main_window.window_title", version=VERSION)`).
2. **`version_info.txt`** : `filevers`/`prodvers` (tuple `(x, y, z, 0)`) et les
   deux `StringStruct` `FileVersion`/`ProductVersion` (chaîne `"x.y.z.0"`) -
   les trois doivent rester synchronisés entre eux et avec `app/__version__.py`
   (désynchronisation déjà constatée par le passé, à vérifier systématiquement
   plutôt qu'à supposer à jour).
3. **`CHANGELOG.md`** : la marche à suivre dépend de l'état de publication de
   la section `## [ancien] - <date>` actuellement en tête de fichier -
   **vérifier avant d'agir**, ne jamais le supposer : `git tag -l "v<ancien>"`
   (correspondance = version déjà publiée et taguée sur GitHub, voir
   `RELEASE.txt`).
   - **Si cette version n'est pas encore taguée** (les entrées en tête de
     fichier documentent un travail en cours, pas encore publié) : renommer
     cette même section en `## [x.y.z] - <date du jour>`, en ajustant le
     titre pour qu'il reflète fidèlement le contenu réel de cette version (ne
     pas se contenter de changer le numéro). Ne jamais créer de section
     distincte dans ce cas : les entrées déjà rédigées restent sous la
     section renommée.
   - **Si cette version est déjà taguée** (déjà publiée : la modifier
     altèrerait l'historique d'une release existante, incident vécu le
     2026-07-20 en confondant les deux cas) : ne surtout pas toucher à cette
     section. Ajouter une toute nouvelle section `## [x.y.z] - <date du
     jour>` au-dessus, avec uniquement les entrées propres à ce nouveau bump.
   - Rappel format CHANGELOG (règle 5 de `CLAUDE.md`) : chaque item de liste
     reste sur une seule ligne physique dans le fichier source, aussi longue
     soit-elle.
4. **`README.md`** : `**Version x.y.z**` (près du haut du fichier).
5. **`index.html`** (landing page du projet, hébergée via GitHub Pages) :
   chaîne `version: 'vx.y.z · ...'`, présente en double dans le bundle - une
   fois dans le dictionnaire `fr`, une fois dans le dictionnaire `en` - à
   mettre à jour dans les deux.
6. Vérifier qu'aucune autre mention de l'ancien numéro ne subsiste ailleurs
   dans le projet avant de considérer le bump terminé.
