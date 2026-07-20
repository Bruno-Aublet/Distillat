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
  un état différent. Le compteur de quota journalier (RPD) affiché semblait
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

Étapes à suivre dans cet ordre, sans en sauter aucune :

1. **`app/i18n.py`** : ajouter le code de la langue (ex. `"es"`) à
   `SUPPORTED_LANGUAGES`. Insérer un nouveau cas spécifique dans
   `detect_system_language()` **avant** le repli générique sur l'anglais
   (voir logique en 3 cas ci-dessus, qui devient alors 4 cas), sans toucher
   au comportement des langues déjà gérées.
2. **`locales/<code>.json`** (nouveau fichier) : dupliquer `en.json` (ou
   `fr.json`) comme point de départ, puis traduire **toutes** les clés, dans
   le **même ordre** que les autres fichiers de langue (aucun mécanisme de
   repli par clé manquante : une clé absente lève une `KeyError` au premier
   `tr()` qui la cherche). Vérifier ensuite que les 3 fichiers ont
   exactement le même jeu de clés, par exemple :
   ```python
   import json
   def flatten(d, prefix=""):
       keys = []
       for k, v in d.items():
           full = f"{prefix}.{k}" if prefix else k
           keys.extend(flatten(v, full) if isinstance(v, dict) else [full])
       return keys
   fr = flatten(json.load(open("locales/fr.json", encoding="utf-8")))
   new = flatten(json.load(open("locales/<code>.json", encoding="utf-8")))
   assert fr == new
   ```
3. **`app/main_window.py`**, dropdown de langue (`_build_ui()`) : ajouter
   `self.language_selector.addItem(tr("language_selector.<code_name>"), "<code>")`
   (une clé `language_selector.<code_name>` à ajouter dans **tous** les
   `locales/*.json`, y compris ceux des langues déjà existantes - le nom de
   chaque langue doit être traduit dans toutes les langues). Dans
   `retranslate_ui()`, ajouter l'appel `setItemText` correspondant au nouvel
   index.
4. **`app/gemini_client.py`**, prompts par défaut : rédiger un jeu de 3
   prompts **natif** dans la nouvelle langue (jamais une traduction mot à
   mot des prompts français/anglais existants - la qualité de cette
   traduction/adaptation est déterminante pour le résultat produit par
   Gemini). Verrouiller la sortie dans cette langue (ex. "TOUJOURS EN
   FRANÇAIS" / "ALWAYS IN ENGLISH" → équivalent natif). Vérifier que les 3
   prompts de la nouvelle langue ont exactement les mêmes placeholders
   `.format()` que les prompts existants (`{book_title}`, `{full_text}`,
   `{chapter_summaries}`...), sous peine de `KeyError` selon la langue
   active. Ajouter la nouvelle langue à
   `_DEFAULT_PROMPT_TEMPLATES_BY_LANGUAGE`. Ajouter aussi son marqueur de
   titre de chapitre à `_CHAPTER_TITLE_MARKER_BY_LANGUAGE` (ex. `"TITRE"`/
   `"TITLE"` → équivalent natif), qui doit rester cohérent avec celui annoncé
   dans le prompt de résumé de lot de cette langue.
5. **`app/prompts_store.py`** : aucune modification nécessaire (le stockage
   par langue sous la clé `"prompts"` de `settings.json` est déjà générique,
   indexé par le code de langue).
6. **`distillat.spec`** : aucune modification nécessaire (`locales` est déjà
   embarqué comme dossier entier, tout nouveau fichier à l'intérieur suit
   automatiquement).
7. **`README.md`** : mettre à jour la section
   "Langue de l'interface et des fiches" si la version bilingue du README a
   déjà été mise en place (dernière étape du chantier i18n initial), pour
   mentionner la langue supplémentaire.

# Carte de l'application

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

## Point d'entrée et flux général

- **`main.py`** : point d'entrée. Migre les anciens fichiers de données
  (`config.migrate_legacy_files()`), initialise la langue de l'UI
  (`i18n.init_language()`, avant toute construction de fenêtre puisque tous
  les textes affichés en dépendent), configure l'icône, lance `MainWindow`.
- **`app/worker.py`** (`SummarizeWorker`, `QThread`) : exécute extraction du
  livre + appel Gemini hors du thread UI, pour ne pas geler l'interface.
  Émet des signaux Qt (`progress`, `quota_updated`, `finished_ok`, `failed`)
  consommés par `MainWindow`.

## Modules `app/`

- **`app/config.py`** : emplacements de stockage persistants, indépendants du
  dossier de l'exécutable (survivent à une réinstallation) :
  - `%APPDATA%\Distillat` (mode compilé comme en développement) pour les
    données techniques (état de quota, limites personnalisées, langue de
    l'UI, prompts personnalisés, derniers dossiers utilisés).
  - `Documents\Distillat\Fiches` (toujours) comme dossier de repli initial
    pour les fiches `.distillat.json` et les exports PDF, tant qu'aucun
    dossier n'a encore été mémorisé (voir ci-dessous).
  - Clé API Gemini stockée chiffrée via `keyring` (Gestionnaire
    d'identification Windows), jamais en clair sur disque.
    `load_api_key()`/`save_api_key()` absorbent `keyring.errors.KeyringError`
    (service indisponible) plutôt que de laisser planter l'application.
  - `load_settings()`/`save_settings(update)` : fonctions génériques centrales
    pour `settings.json` (dossier de config), qui regroupe tous les réglages
    peu fréquemment modifiés (langue de l'UI, prompts personnalisés par
    langue, derniers dossiers utilisés) en un seul fichier - à la différence
    du compteur de quota (`.quota_state.json`) ou des limites RPM/TPM/RPD
    (`quota_limits.json`), réécrits bien plus souvent (à chaque appel Gemini
    pour le premier) et donc gardés dans des fichiers séparés pour limiter la
    fenêtre d'exposition à une corruption et éviter de réécrire inutilement
    des données volumineuses (les prompts personnalisés) à chaque appel API.
    `save_settings()` fait un cycle lecture-fusion-écriture complet (une clé
    de premier niveau, ex. `"prompts"` ou `"last_dirs"`, n'écrase jamais les
    autres). Toute nouvelle fonction de persistance ajoutée doit passer par
    ces deux fonctions plutôt que de créer un nouveau fichier, sauf besoin
    similaire à celui du quota (écritures très fréquentes ou volume important
    de données peu liées aux autres réglages).
  - `load_language_setting()`/`save_language_setting()` : langue de l'UI
    choisie par l'utilisateur (code `fr`/`en`), sous la clé `"language"` de
    `settings.json`. `None` si aucune langue n'a encore été enregistrée
    (premier démarrage), consommé par `app/i18n.py` pour déclencher la
    détection depuis la langue système dans ce cas précis.
  - `load_last_report_dir()`/`save_last_report_dir()` et
    `load_last_pdf_dir()`/`save_last_pdf_dir()` : dernier dossier utilisé
    respectivement pour une fiche et pour un export PDF (mémorisés
    séparément), sous la clé `"last_dirs"` de `settings.json`. Un dossier
    mémorisé qui n'existe plus (supprimé, périphérique amovible
    débranché...) est traité comme absent (`None`), sans jamais lever
    d'erreur. Consommé par `main_window._default_save_dir()` (fiche) et
    `_default_pdf_dir()` (PDF), qui gardent la priorité au dossier de la
    fiche actuellement ouverte si elle en a un ; le dossier est mémorisé à
    chaque sauvegarde/chargement réussi, pas seulement au premier usage.
  - `_merge_legacy_settings_files()` (appelée par `migrate_legacy_files()`,
    y compris en développement contrairement au reste de cette fonction,
    ces anciens fichiers vivant déjà dans `%APPDATA%\Distillat` dans les deux
    modes) : fusionne dans `settings.json` les anciens `last_dirs.json` et
    `prompts.json` (chacun dans son propre fichier avant leur regroupement
    en un seul `settings.json`, le 2026-07-20), s'ils existent encore, sans
    jamais écraser une clé déjà présente dans `settings.json` ; supprime
    l'ancien fichier une fois sa fusion effectuée avec succès.

- **`app/i18n.py`** : internationalisation (français/anglais). Traductions
  chargées depuis `locales/fr.json`/`locales/en.json` (clés imbriquées par
  fenêtre/module, `.format()` pour les portions dynamiques - même mécanisme
  que les prompts Gemini), embarqués à la compilation comme `LICENSE` (voir
  `distillat.spec`). `detect_system_language()` implémente la logique en 3 cas
  de la règle 7 ci-dessus. `init_language()` (appelé une seule fois par
  `main.py`, avant toute construction de fenêtre) charge la langue déjà
  enregistrée (`config.load_language_setting()`) ou la détecte depuis le
  système au premier démarrage. `set_language()` bascule l'état global
  (`_current_language`/`_current_translations`) et permet un changement à
  chaud sans redémarrage : `main_window.MainWindow.retranslate_ui()` réapplique
  alors tous les textes statiques déjà construits. `tr(key, **kwargs)` résout
  une clé (ex. `"main_window.save_button"`) dans la langue actuellement
  chargée ; toutes les clés existent dans les 2 langues dès leur création,
  donc pas de mécanisme de repli par clé manquante.

- **`app/epub_parser.py`** : extrait texte + table des matières d'un EPUB via
  `ebooklib`/`BeautifulSoup`. Découpe par chapitre (titre pris dans la TOC, ou
  un titre de repli traduit à défaut) ; extrait la couverture (plusieurs
  stratégies de repli, beaucoup d'EPUB ne la taguent pas proprement). Retourne
  un `BookContent` (titre, auteur, texte intégral, liste de `Chapter`,
  couverture).

- **`app/pdf_parser.py`** : extrait le texte d'un PDF via `pypdf`, page par
  page ; les PDF n'ayant pas de structure de chapitres fiable, le texte est
  découpé arbitrairement par blocs de 20 pages (`PAGES_PER_CHAPTER`) pour
  réutiliser le même traitement par lots qu'un gros EPUB. La couverture est
  la première page du PDF rendue en image (via `pypdfium2`), quel que soit son
  contenu réel. Retourne aussi un `BookContent`.

- **`app/cover_image.py`** : redimensionne (max 600px de large) et recompresse
  en JPEG (qualité 85) les images de couverture avant stockage, pour éviter des
  fiches JSON inutilement lourdes. `shrink_cover_image()` absorbe
  `UnidentifiedImageError`/`OSError`/`Image.DecompressionBombError` (renvoie
  les bytes d'origine) : une couverture illisible ou piégée ne doit jamais
  faire échouer tout le parsing du livre.

- **`app/gemini_client.py`** (coeur de la génération) :
  - `MODEL_NAME = "gemini-3.5-flash"`. `_get_model()` (texte libre, utilisé
    uniquement pour `count_tokens()`) et `_get_json_model()` (force
    `response_mime_type="application/json"` côté API pour tous les appels de
    génération, pour fiabiliser le JSON en sortie).
  - `generate_book_report()` est le point d'entrée : compte les tokens du
    texte intégral, puis deux cas stricts, sans autre cas intermédiaire :
    - **Texte tient dans `MAX_TOKENS_PER_REQUEST` (200k)** : un seul appel
      combiné demandant résumé court + détaillé + personnages + analyse en
      JSON (`_full_report_prompt`, prompt par défaut `full_report`).
    - **Texte trop long** : `_split_chapters_into_batches()` répartit les
      chapitres en lots consécutifs tenant chacun sous `MAX_TOKENS_PER_REQUEST`
      (le plus de chapitres possible par lot, pour limiter le nombre de
      requêtes - le quota RPD du palier gratuit est très serré). Chaque lot
      est résumé en un appel JSON (`_chapter_summary_prompt`, prompt par
      défaut `chapter_summary`, un résumé par chapitre du lot dans la
      réponse ; un résumé vide est toléré sans erreur - chapitre sans contenu
      narratif, ex. "Du même auteur" - et simplement absent de la
      consolidation). Une seule requête finale (`_consolidation_prompt`,
      prompt par défaut `consolidation`) reçoit ensuite tous les résumés de
      chapitre et produit en une fois résumé court + détaillé + personnages +
      analyse - jamais le texte intégral, déjà couvert par ces résumés. Il
      n'y a pas de troisième cas ni de découpage séparé pour
      personnages/analyse : cette simplification est volontaire, voir
      l'historique de conversation du 2026-07-19 si une réintroduction de
      complexité est envisagée.
    - **`MAX_TOKENS_PER_REQUEST` (200k) vs `MAX_INPUT_TOKENS` (900k)** : ne
      pas confondre les deux (bug corrigé le 2026-07-19). `MAX_INPUT_TOKENS`
      documente la fenêtre de contexte du modèle (limite haute, rarement
      atteinte) ; `MAX_TOKENS_PER_REQUEST` est la vraie contrainte
      opérationnelle, calée avec marge sous la limite de débit par minute
      (TPM) du palier gratuit constatée sur le dashboard AI Studio - c'est
      elle qui dimensionne un appel unique, sous peine de saturer le TPM à
      elle seule dès le premier appel même sans historique de requêtes.
    - **Reprise après échec partiel** : si un lot échoue (quota, réponse
      illisible...) après qu'au moins un lot a déjà été résumé avec succès,
      `generate_book_report()` lève `PartialGenerationError` (sous-classe de
      `GeminiError` portant `chapter_summaries`/`batches_done`/
      `batches_total`) au lieu de laisser filer l'erreur brute et perdre ce
      travail. Les paramètres `resume_chapter_summaries`/`resume_batches_done`
      permettent de reprendre à partir d'un lot donné sans reformuler les
      précédents. Voir `app/generation_resume.py` (persistance de cet état
      dans `.generation_resume.json`, dossier de config, lié au livre par
      hash SHA-256 du texte extrait) et `app/worker.py`/`app/main_window.py`
      (sauvegarde sur échec, proposition de reprise au clic sur "Résumer" si
      le fichier sélectionné correspond).
  - `_call_gemini()` effectue un seul appel, sans retry automatique (voir
    historique de conversation du 2026-07-19 : c'est un choix délibéré,
    l'utilisateur doit recliquer lui-même sur "Résumer") : toute erreur API
    (quota RPM/TPM, quota RPD journalier, `ServiceUnavailable`/
    `InternalServerError`/`DeadlineExceeded` (503/500/504), clé invalide
    `PermissionDenied`/`Unauthenticated`) échoue immédiatement. L'accès à
    `response.text` est protégé contre le `ValueError` d'une réponse bloquée
    par les filtres de sécurité de Gemini (message dédié). `_friendly_error_message()`
    traduit chaque cas dans la langue actuellement choisie pour l'UI (via
    `tr()`, voir `app/i18n.py`) avec le code d'erreur d'origine entre
    parenthèses, en invitant à recliquer sur "Résumer" quand pertinent.
    Retourne aussi `error_kind` (`"daily_quota"`/`"rate_quota"`/`None`),
    indépendant de la langue du message : `main_window._on_failed()` s'appuie
    dessus (via le signal `SummarizeWorker.failed`, qui transmet désormais
    `(message, error_kind)`) pour adapter son comportement (ex : proposer une
    reprise), jamais en cherchant un mot-clé dans le message traduit, ce qui
    casserait selon la langue active.
  - `_parse_json_object()` utilise `json.JSONDecoder().raw_decode()` (pas
    `json.loads()`) pour tolérer du contenu superflu après le premier objet
    JSON valide ; ce surplus est renvoyé séparément (`leftover`), jamais jeté
    silencieusement, et remonté jusqu'à `BookReport.extra_generated_text`.
  - `_normalize_dashes()` : remplace systématiquement les tirets cadratin/
    demi-cadratin produits par Gemini par un tiret simple.
  - `default_prompt_templates()` (3 clés : `full_report`, `chapter_summary`,
    `consolidation`) : renvoie le jeu de prompts par défaut de la langue
    actuellement choisie pour l'UI (`DEFAULT_PROMPT_TEMPLATES_FR`/`_EN`,
    sélectionné via `i18n.current_language()`). Le prompt anglais est rédigé
    nativement (pas une traduction mot à mot du français) et verrouille la
    sortie en anglais ("ALWAYS IN ENGLISH"), symétrique au "TOUJOURS EN
    FRANÇAIS" du prompt français : la langue de sortie demandée à Gemini suit
    toujours la langue de l'UI au moment de la génération. Les deux jeux de
    prompts ont exactement les mêmes placeholders `.format()` (ex.
    `{book_title}`, `{full_text}`, `{chapter_summaries}`) : à vérifier à
    chaque modification d'un des deux prompts, sous peine de `KeyError` selon
    la langue active. `_get_prompt_template()` renvoie le prompt personnalisé
    par l'utilisateur pour cette langue (via `app/prompts_store.py`) s'il
    existe, sinon le défaut de cette langue. `_format_prompt_template()`
    centralise l'appel à `.format()` pour les 3 prompts et traduit un
    `KeyError` (repère mal orthographié dans un prompt personnalisé, ex.
    `{full_textt}`) en `GeminiError` nommant le prompt et le repère en cause.
    Le marqueur de titre de chapitre inséré par `_chapters_batch_text()`
    (`[[[TITRE: ...]]]`/`[[[TITLE: ...]]]`) suit aussi la langue active, pour
    rester cohérent avec celui annoncé dans le prompt de résumé de lot.

- **`app/prompts_store.py`** : persistance des prompts personnalisés sous la
  clé `"prompts"` de `settings.json` (via `config.load_settings()`/
  `save_settings()`), imbriqués par langue (`{"fr": {...}, "en": {...}}`) :
  personnaliser un prompt dans une langue ne doit jamais affecter l'autre,
  sous peine de mélanger un texte français et une consigne de sortie
  anglaise (ou l'inverse) dès que l'utilisateur change la langue de l'UI
  (bug vécu le 2026-07-20, avant cette séparation par langue). Un ancien
  stockage à plat (format antérieur à l'introduction du bilinguisme, avant
  même l'existence de `settings.json`) est migré silencieusement vers le
  français (`_LEGACY_FORMAT_LANGUAGE`, seule langue de l'application à cette
  époque) à la première lecture - ce cas peut se présenter après la fusion
  d'un ancien `prompts.json` à plat par `config._merge_legacy_settings_files()`,
  qui ne connaît pas la structure interne des prompts et le recopie tel quel.
  Une clé absente pour une langue donnée signifie "utiliser le prompt par
  défaut de cette langue". `PROMPT_KEYS` doit rester synchronisé avec les
  clés de `default_prompt_templates()`. `load_custom_prompts(language)`/
  `save_custom_prompts(language, prompts)` ne prennent plus de paramètre
  `settings_dir` (retiré le 2026-07-20 en même temps que la fusion : devenu
  un paramètre mort, ces fonctions résolvent leur propre chemin via
  `app.config`) - à conserver au fil des futures modifications, ne pas le
  réintroduire par réflexe de compatibilité avec une ancienne signature.

- **`app/generation_resume.py`** (`ResumeState`) : persistance de l'état
  intermédiaire d'une génération en lots interrompue par un échec partiel
  (voir `gemini_client.PartialGenerationError`) dans
  `.generation_resume.json` (dossier de config, comme `quota_limits.json`).
  Fichier unique : une seule génération en lots peut être interrompue à la
  fois. `compute_book_hash()` (SHA-256 du texte extrait) permet à l'appelant
  de vérifier qu'un état sauvegardé correspond bien au livre actuellement
  chargé avant de proposer une reprise, pas seulement au chemin de fichier
  (qui pourrait avoir changé de contenu entre-temps).

- **`app/quota_tracker.py`** (`QuotaTracker`) : suivi *local* et *estimatif*
  des quotas Gemini (RPM/TPM sur fenêtre glissante de 60s, RPD persisté par
  date dans `.quota_state.json`). Les limites par défaut
  (`DEFAULT_RPM/TPM/RPD_LIMIT`) sont ajustables par l'utilisateur via l'UI et
  stockées dans `quota_limits.json`. Ne reflète que ce que *cette*
  application a envoyé (faussé si la même clé est utilisée ailleurs).
  `record_call()` (thread worker) et `snapshot()`/`reload_limits()` (thread
  UI, dont un timer périodique) accèdent au même état : `_lock`
  (`threading.Lock`, non réentrant) protège toute méthode qui le touche -
  ajouter une méthode publique en tenant compte de ça.

- **`app/update_checker.py`** : vérification de la disponibilité d'une
  nouvelle version de Distillat via l'API GitHub Releases
  (`Bruno-Aublet/Distillat`), au démarrage uniquement (pas de menu "À propos"
  dans cette application, contrairement à d'autres projets de l'auteur).
  `check_for_updates_on_startup()` lance la requête réseau dans un
  `threading.Thread` daemon (ne retarde jamais l'affichage de la fenêtre) et
  compare les versions via `packaging.version.Version` ; silencieux en cas
  d'erreur réseau, d'absence de mise à jour, ou de tag GitHub malformé
  (`_is_newer()` retourne `False` sans lever). Seule une mise à jour trouvée
  a un effet visible : appel de `main_window.show_update_banner()`.

- **`app/book_report.py`** (`BookReport`, `Character`) : structure de données
  centrale de la fiche + sérialisation JSON (`to_json`/`from_json`/`save`/`load`,
  `FILE_FORMAT_VERSION = 2`). `extra_generated_text` existe uniquement en
  mémoire (jamais persisté, ni JSON ni PDF) : contenu superflu généré par
  Gemini, à la disposition de l'utilisateur via l'UI mais pas de la fiche.
  `from_json()`/`load()` ne réécrivent jamais le fichier source (même si la
  couverture est recompressée au passage, en mémoire uniquement) : une
  simple lecture ne doit jamais modifier le fichier lu.
  `sanitize_filename()` nettoie un titre de livre pour en faire un nom de
  fichier Windows valide, en gardant la ponctuation courante des titres ;
  retombe sur `fallback` (résolu via `tr("book_report.fallback_filename")` si
  non fourni explicitement, jamais figé en français : le paramètre par défaut
  ne peut pas appeler `tr()` au chargement du module, avant que la langue soit
  initialisée) pour un nom réservé Windows (`CON`, `NUL`, `COM1`...) ou se
  terminant par un point/espace après nettoyage.

- **`app/pdf_export.py`** : export de la fiche en PDF via ReportLab (pas de
  dépendance système, contrairement à WeasyPrint). Style éditorial
  navy/gold : bandeaux de titre de section (`_TagHeading`), lettrine
  dessinée à la main sur le premier paragraphe de chaque section
  (`_DropCapBlock`/`_dropcap_flowables`, justification calculée mot à mot).
  `_body_flowables()` interprète le texte stocké ligne par ligne : une ligne
  `### `/`## `/`# ` devient un titre stylé, tout le reste est un paragraphe de
  corps (le tout premier reçoit la lettrine). Couverture = première page du
  PDF source si présente. Un chapitre par `PageBreak`.

- **`app/main_window.py`** (le plus gros fichier, ~1700 lignes) : toute l'UI.
  - `MainWindow` : fenêtre principale, zone de glisser-déposer (`DropZone`,
    accepte EPUB/PDF à résumer ou `.distillat.json` à recharger), sélecteur de
    langue (`QComboBox` dans l'en-tête, voir `app/i18n.py`) et rappel discret
    sous l'en-tête indiquant la langue de sortie des fiches générées
    (`output_language_hint_label`), boutons de gestion de fiche
    (Charger/Sauvegarder/Fermer/Exporter PDF), 5 onglets de résultat
    (Couverture, Résumé court, Résumé détaillé, Personnages, Analyse),
    affichage de quota en temps réel, chrono écoulé pendant la génération,
    bandeau de mise à jour disponible (`update_banner_label`, masqué par
    défaut, voir `app/update_checker.py`).
  - `_on_language_changed()` (connecté à `currentIndexChanged` du sélecteur,
    APRÈS l'initialisation de l'index courant pour ne pas se déclencher à la
    construction) appelle `i18n.set_language()` + `config.save_language_setting()`
    puis `retranslate_ui()`, qui réapplique tous les textes **statiques**
    (titres, labels fixes, placeholders, titres d'onglets, boutons) sur les
    widgets déjà construits, sans reconstruire l'UI ni toucher à l'état
    dynamique actuellement affiché (fiche en cours, statut de génération...)
    qui se retraduit de lui-même à sa prochaine mise à jour naturelle : un
    changement de langue à chaud, sans redémarrage, mais aussi sans le risque
    de régression d'un rebuild complet de `_build_ui()` en cours de session.
  - Dialogues : `ApiKeyDialog`, `QuotaLimitsDialog`, `PromptsDialog` (un
    onglet par clé de `default_prompt_templates()`, un bouton de
    réinitialisation par onglet, n'affecte que cet onglet - la police
    Courier New de chaque zone de saisie est fixée via
    `document().setDefaultFont()` + repaint forcé du viewport, pas seulement
    `setFont()`/`setFontFamily()` : sur certaines machines, l'affichage ne se
    synchronisait pas avec l'état logique du widget sans ce repaint explicite,
    bug constaté le 2026-07-20), `ExtraTextDialog`
    (non modale, pour le contenu superflu de Gemini), `LicenseDialog`. Le
    dialogue de reprise de génération (dans `_on_summarize_clicked()`, avant
    de démarrer `SummarizeWorker`) utilise un `QMessageBox` avec des boutons
    construits à la main (`addButton(tr(...), ...)`) plutôt que les boutons
    standards `QMessageBox.Yes/No` : ces derniers restent parfois non traduits
    selon la configuration Qt du système (bug constaté le 2026-07-19),
    contrairement à un texte de bouton explicite passé par `tr()`.
  - **Édition et round-trip du texte** (point sensible, voir bug corrigé le
    2026-07-19) : le texte stocké utilise une ligne = un paragraphe ou un
    titre `#`/`##`/`###`. Pour l'affichage, `_to_display_markdown()` insère
    des lignes vides entre blocs puis `QTextEdit.setMarkdown()` rend le
    Markdown (titres stylés, plus de `#` visibles). Pour resynchroniser une
    édition utilisateur vers `last_result`
    (`_sync_edits_to_last_result()`, appelée avant toute sauvegarde/export) :
    **ne jamais utiliser `toMarkdown()`** (Qt y recoupe artificiellement les
    lignes trop longues autour de 80 colonnes, un wrap purement visuel sans
    rapport avec la vraie structure - a causé un bug de titre coupé en deux
    avec lettrine parasite). Utiliser `toPlainText()` (un vrai retour à la
    ligne par paragraphe/titre réel, jamais de wrap artificiel) et
    `_from_display_plain_text()`, qui réassocie chaque bloc affiché au
    préfixe `#`/`##`/`###` du bloc correspondant dans le texte source
    d'origine (par position), pour ne reporter que les vraies éditions.
    Limite connue et acceptée (évaluée le 2026-07-19, correction jugée trop
    risquée pour le bénéfice) : ajouter/supprimer un paragraphe entier décale
    ce mappage positionnel et peut faire hériter un paragraphe du préfixe
    d'un titre voisin, ou l'inverse.
  - `_report_dirty` + `_confirm_discard_unsaved_report()` : protège contre la
    perte d'une fiche modifiée non sauvegardée (nouveau fichier, fermeture de
    fiche, fermeture de l'application). `_confirm_abort_running_generation()`
    protège séparément contre la fermeture de l'application pendant qu'un
    `SummarizeWorker` tourne encore (sinon : QThread détruit actif).
    `_on_summarize_clicked()` doit remettre `last_result`/`_report_dirty` à
    `None`/`False` AVANT `_clear_result_tabs()` (dont le `clear()` des
    QTextEdit émet `textChanged`), sous peine de marquer à tort la fiche
    précédente comme modifiée.

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

Uniquement sur demande explicite et précise du numéro cible (règle 1). Une
fois la demande reçue, mettre à jour dans cet ordre :

1. **`app/__version__.py`** : `VERSION = "x.y.z"`. Source unique de vérité,
   affichée dans le titre de la fenêtre (`main_window.tr("main_window.window_title", version=VERSION)`).
2. **`version_info.txt`** : `filevers`/`prodvers` (tuple `(x, y, z, 0)`) et les
   deux `StringStruct` `FileVersion`/`ProductVersion` (chaîne `"x.y.z.0"`) -
   les trois doivent rester synchronisés entre eux et avec `app/__version__.py`
   (désynchronisation déjà constatée par le passé, à vérifier systématiquement
   plutôt qu'à supposer à jour).
3. **`CHANGELOG.md`** : renommer la section `## [ancien] - <date>` déjà en
   tête de fichier en `## [x.y.z] - <date du jour>`, en ajustant le titre de
   section pour qu'il reflète fidèlement le contenu réel de cette version (ne
   pas se contenter de changer le numéro). Ne jamais créer une nouvelle
   section distincte pour un simple bump : les entrées déjà rédigées pour la
   prochaine version restent sous la section renommée.
4. **`README.md`** : `**Version x.y.z**` (près du haut du fichier).
5. Vérifier qu'aucune autre mention de l'ancien numéro ne subsiste ailleurs
   dans le projet avant de considérer le bump terminé.

## Documentation à tenir à jour

Après toute modification fonctionnelle : `CHANGELOG.md`, `README.md`
(comportement utilisateur), `requirements.txt` (si dépendance ajoutée/retirée),
uniquement ce qui est réellement concerné par le changement - suivre
l'instruction "changelog, readme, requirements, spec" au cas par cas plutôt
que de tout mettre à jour mécaniquement.
