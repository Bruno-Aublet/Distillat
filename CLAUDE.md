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

## 2. Question à choix multiples (AskUserQuestion) : autorisée en mode plan
   uniquement

- L'outil de question à choix multiples (AskUserQuestion) est autorisé
  uniquement pendant le mode plan (assoupli le 2026-07-22, sur demande
  explicite de l'utilisateur).
- En dehors du mode plan, interdiction ferme d'utiliser cet outil : en cas de
  besoin de clarification, poser la question en texte libre, normal, dans la
  conversation - jamais sous forme de choix structurés/boutons.

## 3. Jamais de bouton Qt standard non traduit

- Interdiction ferme et permanente d'utiliser un bouton standard PyQt5 dont le
  texte n'est pas piloté par `app/i18n.py` (`tr(...)`). Concrètement,
  n'utilise JAMAIS les rôles standard de `QDialogButtonBox`
  (`QDialogButtonBox.Ok`, `.Cancel`, `.Yes`, `.No`, `.Close`, `.Save`,
  `.Discard`, `.Apply`, `.Reset`, `.RestoreDefaults`, `.Help`, `.Abort`,
  `.Retry`, `.Ignore`, etc.) : leur texte vient des traductions intégrées de
  Qt, pas de `locales/fr.json`/`locales/en.json`, et reste donc figé dans une
  langue indépendante de celle choisie par l'utilisateur dans l'application
  (ex. bouton "Cancel" resté en anglais alors que l'interface est en
  français).
- À la place, toujours construire les boutons d'un `QDialogButtonBox` via
  `addButton(tr("clé.appropriée"), QDialogButtonBox.AcceptRole)` (ou
  `.RejectRole`, `.ActionRole`, etc. selon le rôle voulu), avec une clé de
  traduction dédiée ajoutée dans les deux fichiers `locales/*.json`.
- Raison : bug découvert le 2026-07-22 sur `PromptsDialog`
  (`app/main_window.py`) - le bouton "Sauvegarder" avait bien été traduit via
  un `QPushButton` custom, mais le bouton "Annuler" avait été laissé en
  `QDialogButtonBox.Cancel` standard, affichant "Cancel" même en interface
  française.
- S'applique à toute nouvelle fenêtre ou tout nouveau dialogue ajouté par la
  suite.

## 4. Signaler, ne jamais corriger silencieusement un problème hors scope

- Si un bug, une incohérence ou un problème est découvert en dehors du
  périmètre exact de la demande en cours : le signaler explicitement à
  l'utilisateur et demander comment procéder.
- Ne jamais élargir le scope d'une tâche de sa propre initiative, même pour
  « bien faire » ou « en profiter ».

## 5. En cas d'erreur

- Si une action non autorisée a été faite par erreur, l'annuler immédiatement
  et proprement, puis attendre les instructions de la suite.

## 6. Typographie

- Ne JAMAIS utiliser de tiret cadratin (—) ni de tiret demi-cadratin (–), dans
  aucun texte produit pour ce projet : code, commentaires, UI, README,
  CHANGELOG, messages. Utiliser un tiret simple (-) ou reformuler la phrase.

## 7. CHANGELOG.md : une entrée = une seule ligne physique

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

## 8. Ne jamais dédoubler l'emplacement de stockage d'un fichier persistant

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

## 9. Internationalisation (i18n) : logique de détection de langue au premier démarrage

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

## 10. Toujours consulter ARCHITECTURE.md avant de chercher ou modifier un module

- Avant de chercher des informations sur un module (comportement, signature,
  structure de données, fichier de persistance...) ou de le modifier,
  commencer par lire la section correspondante d'`ARCHITECTURE.md`, plutôt
  que de lire directement le code source ou de supposer son fonctionnement.
- Raison : `ARCHITECTURE.md` documente aussi les comportements non évidents à
  la simple lecture du code (bugs passés corrigés, choix délibérés,
  contraintes découvertes en production) ; les ignorer risque de réintroduire
  un bug déjà corrigé ou de défaire un choix volontaire sans le savoir.
- Pour le détail d'implémentation (code exact, cas limites non documentés
  dans `ARCHITECTURE.md`), aller ensuite consulter le module lui-même : la
  lecture d'`ARCHITECTURE.md` vient en complément du code source, jamais en
  remplacement.
- `CLAUDE.md` ne garde qu'un résumé d'une ligne par module (voir l'index
  ci-dessous) : ce n'est qu'un point d'entrée, jamais une source suffisante en
  soi.

## 11. INTERDICTION ABSOLUE de faire tourner du code de test/diagnostic sur
    l'état réel de l'utilisateur (settings.json, keyring, %APPDATA%\Distillat)

- Tout script Python exécuté via Bash/PowerShell pour tester, vérifier ou
  déboguer un comportement (ex : `python -c "..."`, script jetable dans le
  scratchpad) doit **systématiquement** utiliser un état entièrement isolé
  et jetable : un dossier de config temporaire dédié (jamais
  `config.get_settings_dir()`/`config.get_app_dir()` réels), et jamais
  l'entrée keyring réelle du service `Distillat` (jamais
  `keyring.get_password`/`set_password`/`delete_password` avec le vrai
  `KEYRING_SERVICE_NAME` sans un nom de compte/profil fictif garanti bidon).
- Interdiction stricte d'appeler `config.list_profiles()`,
  `config.save_profiles(...)`, `config.delete_profile_api_key(...)`,
  `config.load_api_key()`/`save_api_key()`/`load_settings()`/
  `save_settings()` (ou tout équivalent qui lit/écrit l'état réel de
  l'application) dans un script de test "pour nettoyer avant de commencer",
  même avec l'intention de tout restaurer après : si l'utilisateur a de
  vraies données à cet emplacement (profils, clés API, réglages), les vider
  au nom d'un "nettoyage préalable" de test les détruit réellement, sans
  recours - **et une clé API supprimée du Gestionnaire d'identification
  Windows ne peut PAS être retrouvée depuis l'application ensuite**, même si
  elle reste valide côté Google (il faut alors que l'utilisateur aille la
  regénérer/retrouver lui-même sur https://aistudio.google.com/apikey).
- Raison : incident vécu **deux fois** le 2026-07-22 dans la même
  conversation. La première fois, un script de test de migration a exécuté
  `keyring.delete_password(config.KEYRING_SERVICE_NAME,
  config.KEYRING_USERNAME)` en pensant "nettoyer une clé de test", sans
  jamais vérifier qu'il y avait une vraie clé de l'utilisateur à cet
  emplacement avant de commencer - elle y était, et a été perdue. La seconde
  fois, quelques échanges plus tard dans la même session, un script de test
  de `find_profile_by_name()`/`find_profile_by_api_key()` a exécuté
  `for p in config.list_profiles(): config.delete_profile_api_key(p['id'])`
  puis `config.save_profiles([])` comme "nettoyage préalable" avant de créer
  des profils fictifs de test - or `config.list_profiles()` a retourné le
  VRAI profil de l'utilisateur ("Bruno", avec sa vraie clé API), qui a donc
  été supprimé une seconde fois, avec le même mécanisme de perte
  irrécupérable (l'identifiant du profil, seul moyen de retrouver son entrée
  keyring, n'existait plus que dans `settings.json`, lui-même vidé au même
  moment).
- Marche à suivre correcte pour tout futur test similaire : soit passer un
  `settings_dir`/chemin explicitement factice à chaque fonction qui
  l'accepte en paramètre, soit monkey-patcher `config.get_settings_dir()`
  pour qu'elle pointe vers un dossier temporaire du scratchpad le temps du
  test, soit (le plus sûr) écrire un vrai test automatisé
  (`pytest`/`unittest`) avec fixtures qui isolent complètement
  l'environnement plutôt qu'un script `python -c` ad hoc qui touche
  l'état réel par défaut. En cas de doute sur l'état réellement présent à un
  emplacement avant d'y toucher (même pour un test) : lire d'abord ce qui
  s'y trouve et le signaler à l'utilisateur, ne jamais supposer qu'il ne
  contient que des données jetables.

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
- **`app/config.py`** : emplacements de stockage persistants, profils de clé
  API nommés (keyring), `settings.json` (langue, prompts par profil, derniers
  dossiers), migration des anciens fichiers.
- **`app/instance_lock.py`** : verrous inter-instances (fichiers PID) par
  profil de clé API et par livre en cours de génération, pour l'usage à
  plusieurs instances de Distillat en parallèle.
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
  profil de clé API puis par langue.
- **`app/generation_resume.py`** (`ResumeState`) : reprise après échec
  partiel d'une génération par lots.
- **`app/quota_tracker.py`** (`QuotaTracker`) : suivi local RPM/TPM/RPD par
  clé API, calculé en heure du Pacifique.
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
