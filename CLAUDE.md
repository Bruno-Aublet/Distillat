# Règles impératives pour travailler sur ce projet

## 1. N'entreprendre AUCUNE action non explicitement demandée

- Ne fais que ce qui est explicitement demandé. Rien de plus.
- Ne bump JAMAIS un numéro de version (`app/__version__.py`, `version_info.txt`,
  README, CHANGELOG) sauf demande explicite et précise du numéro cible.
- Ne fais AUCUNE action destructive ou irréversible (git commit, git push,
  suppression de fichiers, écrasement de fichiers existants) sans validation
  explicite préalable.
- En cas de doute sur l'interprétation d'une demande : **demander avant
  d'agir**, ne jamais supposer.
- Une confirmation donnée pour une action précise ne vaut pas autorisation
  générale pour des actions similaires ou connexes.

## 2. Signaler, ne jamais corriger silencieusement un problème hors scope

- Si un bug, une incohérence ou un problème est découvert en dehors du
  périmètre exact de la demande en cours : le signaler explicitement à
  l'utilisateur et demander comment procéder.
- Ne jamais élargir le scope d'une tâche de sa propre initiative, même pour
  « bien faire » ou « en profiter ».

## 3. En cas d'erreur

- Si une action non autorisée a été faite par erreur, l'annuler immédiatement
  et proprement, puis attendre les instructions de la suite.

## 4. Typographie

- Ne JAMAIS utiliser de tiret cadratin (—) ni de tiret demi-cadratin (–), dans
  aucun texte produit pour ce projet : code, commentaires, UI, README,
  CHANGELOG, messages. Utiliser un tiret simple (-) ou reformuler la phrase.

## 5. CHANGELOG.md : une entrée = une seule ligne physique

- Chaque item de liste (`- ...`) du CHANGELOG doit rester sur une seule ligne
  physique dans le fichier source, aussi longue soit-elle : jamais de retour à
  la ligne dur au milieu d'un item, même pour la lisibilité du fichier brut.
- Raison : le champ de description d'une release GitHub interprète les
  retours à la ligne durs comme de vrais sauts de ligne (contrairement au
  rendu Markdown habituel, qui les ignore). Un item réparti sur plusieurs
  lignes dans le fichier produit donc des sauts de ligne parasites une fois
  collé dans la description de la release.
- S'applique à toute nouvelle entrée ajoutée au CHANGELOG, y compris par
  Claude.

## 6. Ne jamais dédoubler l'emplacement de stockage d'un fichier persistant

- Un fichier de données persistantes de l'application (état de quota, config,
  dernier dossier utilisé, etc.) doit toujours être lu et écrit au même
  emplacement, quel que soit le mode de lancement (développement ou
  compilé/exe) ou le contexte d'exécution. Ne jamais introduire un chemin qui
  varie selon le mode pour ce type de fichier.
- Raison : deux emplacements distincts pour le même fichier logique créent
  deux copies indépendantes qui divergent silencieusement à l'usage. Exemple
  vécu le 2026-07-19 : `get_settings_dir()` renvoyait `%APPDATA%\Distillat`
  en mode compilé mais le dossier du projet en développement, si bien que
  `.quota_state.json` et `last_dirs.json` existaient en double, chacun avec
  un état différent. Le compteur de quota quotidien (RPD) affiché semblait
  alors varier de façon incohérente d'un lancement à l'autre, alors qu'il ne
  reflétait que la moitié des appels réellement effectués sur la clé API
  partagée - un bug de fond, difficile à repérer, qui aurait pu faire
  dépasser le quota réel sans avertissement.
- S'applique à tout nouveau fichier de persistance ajouté par la suite : un
  seul chemin de résolution, indépendant du mode de lancement.

## 7. Internationalisation (i18n) : logique de détection de langue au premier démarrage

- Au premier démarrage (aucune langue encore enregistrée dans `settings.json`),
  la langue est déterminée par la langue du système Windows selon une logique
  en 3 cas, à respecter strictement et ne jamais simplifier :
  1. Langue système détectée = français -> l'application démarre en français.
  2. Langue système détectée = anglais -> l'application démarre en anglais.
  3. Toute autre langue système détectée (ni français ni anglais) -> repli sur
     l'anglais.
- Raison : cette formulation en 3 cas (plutôt qu'un simple `if français then
  français else anglais`) est volontaire et pensée pour l'ajout ultérieur
  d'une 3e langue (ou plus). Le jour où une nouvelle langue est ajoutée, il
  suffit d'insérer un nouveau cas spécifique pour elle (avant le cas de repli
  générique) sans toucher à la structure existante ni au comportement des
  langues déjà gérées.
- L'anglais est donc la langue de repli universelle de l'application, pas
  seulement la langue des systèmes anglophones.
- Ce réglage est ensuite modifiable manuellement par l'utilisateur (sélecteur
  de langue dans la fenêtre principale) et persiste indépendamment de la
  langue système une fois choisi explicitement.

### Marche à suivre pour ajouter une nouvelle langue

Voir le skill `add-language` (`.claude/skills/add-language/SKILL.md`).

# Carte de l'application

Documentation détaillée déplacée dans `ARCHITECTURE.md` (module par module,
comportements et bugs passés à connaître avant modification). Toujours la
consulter avant de toucher à un module listé ci-dessous - ce résumé n'est
qu'un index.

## Ce que fait Distillat

Application de bureau Windows (PyQt5) qui génère une fiche de lecture complète
à partir d'un livre `.epub` ou `.pdf`, via l'API Gemini (palier gratuit) :
résumé court, résumé détaillé, personnages/entités principaux et analyse
littéraire, toujours dans la langue actuellement choisie pour l'interface
(français ou anglais, voir `app/i18n.py`) quelle que soit la langue source. La
fiche est éditable dans l'UI, sauvegardable dans un format JSON autonome
(`.distillat.json`) et exportable en PDF avec mise en page éditoriale.

Flux utilisateur type : glisser-déposer un EPUB/PDF -> génération (thread
séparé, avec suivi de quota) -> fiche affichée dans 5 onglets éditables ->
sauvegarde JSON et/ou export PDF.

## Index des modules (détails dans `ARCHITECTURE.md`)

- **`main.py`** : point d'entrée, migration, init langue, lance `MainWindow`.
- **`app/worker.py`** (`SummarizeWorker`) : extraction + appel Gemini hors
  thread UI.
- **`app/config.py`** : emplacements de stockage persistants, clé API
  (keyring), `settings.json` (langue, prompts, derniers dossiers), migration
  des anciens fichiers.
- **`app/i18n.py`** : chargement des traductions, détection langue système,
  changement de langue à chaud.
- **`app/epub_parser.py`** : extraction texte/TOC/couverture EPUB.
- **`app/pdf_parser.py`** : extraction texte/couverture PDF, découpage par
  blocs de pages.
- **`app/cover_image.py`** : redimensionnement/recompression des couvertures.
- **`app/gemini_client.py`** (coeur de la génération) : appel unique ou
  génération par lots + consolidation, gestion des erreurs API, parsing et
  réparation du JSON de sortie, prompts par défaut par langue.
- **`app/prompts_store.py`** : persistance des prompts personnalisés par
  langue.
- **`app/generation_resume.py`** (`ResumeState`) : reprise après échec
  partiel d'une génération par lots.
- **`app/quota_tracker.py`** (`QuotaTracker`) : suivi local RPM/TPM/RPD,
  calculé en heure du Pacifique.
- **`app/update_checker.py`** : vérification de nouvelle version au démarrage
  via GitHub Releases.
- **`app/book_report.py`** (`BookReport`, `Character`) : structure de données
  de la fiche + sérialisation JSON.
- **`app/pdf_export.py`** : export PDF éditorial via ReportLab.
- **`app/main_window.py`** (~1700 lignes) : toute l'UI - fenêtre principale,
  dialogues, édition/round-trip du texte (point sensible, voir
  `ARCHITECTURE.md`), protection contre perte de données non sauvegardées.

## Build et distribution

- **`build.py`** : nettoie `build/`/`dist/`, lance PyInstaller sur
  **`distillat.spec`**, produit `dist/Distillat/Distillat.exe` (dossier
  complet à distribuer, pas juste l'exe).
- **`version_info.txt`** : métadonnées Windows de l'exécutable.
- **`app/__version__.py`** : numéro de version affiché dans le titre de la
  fenêtre - ne jamais le modifier sans demande explicite et précise du numéro
  cible (règle 1 ci-dessus).
- **Dépôt GitHub** : https://github.com/Bruno-Aublet/Distillat. Les releases
  (exécutable compilé zippé, une par version taguée `vx.x.x`) sont publiées
  sur https://github.com/Bruno-Aublet/Distillat/releases. Procédure de
  publication détaillée dans `RELEASE.txt` (checklist personnelle de
  l'utilisateur, pas un fichier de documentation du projet).

### Procédure pour bumper le numéro de version

Uniquement sur demande explicite et précise du numéro cible (règle 1). Voir
le skill `bump-version` (`.claude/skills/bump-version/SKILL.md`).

## Documentation à tenir à jour

Après toute modification fonctionnelle : `CHANGELOG.md`, `README.md`
(comportement utilisateur), `requirements.txt` (si dépendance ajoutée/retirée),
`ARCHITECTURE.md` (si un module documenté y est modifié : signature,
comportement, structure de données, fichier de persistance...), uniquement ce
qui est réellement concerné par le changement - suivre l'instruction
"changelog, readme, requirements, spec" au cas par cas plutôt que de tout
mettre à jour mécaniquement.
