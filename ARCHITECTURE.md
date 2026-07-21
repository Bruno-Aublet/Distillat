# Carte de l'application

Documentation détaillée de l'architecture de Distillat. Référencée depuis
`CLAUDE.md`, qui ne garde qu'un résumé d'une ligne par module. Se reporter
ici pour le détail d'implémentation avant de modifier un module.

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
    l'UI, prompts personnalisés, derniers dossiers utilisés), et son
    sous-dossier `debug_logs\` (`get_debug_logs_dir()`) pour les réponses
    Gemini brutes journalisées en cas d'échec de parsing JSON non réparable
    (voir `gemini_client._log_unparsable_response()` ci-dessous) et le
    journal d'appels API `api_requests.log` (voir
    `gemini_client._log_api_call()` ci-dessous).
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
    l'ancien fichier une fois sa fusion effectuée avec succès, via
    `send2trash()` (bibliothèque `send2trash`) plutôt qu'un `unlink()`
    définitif, pour qu'un fichier supprimé par erreur reste récupérable
    depuis la corbeille Windows (changement du 2026-07-21, qui s'applique
    à toute suppression de fichier faite par l'application ; voir aussi
    `generation_resume.clear_resume_state()`).

- **`app/i18n.py`** : internationalisation (français/anglais). Traductions
  chargées depuis `locales/fr.json`/`locales/en.json` (clés imbriquées par
  fenêtre/module, `.format()` pour les portions dynamiques - même mécanisme
  que les prompts Gemini), embarqués à la compilation comme `LICENSE` (voir
  `distillat.spec`). `detect_system_language()` implémente la logique en 3 cas
  de la règle 7 de `CLAUDE.md`. `init_language()` (appelé une seule fois par
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
      dans `.generation_resume_<hash>.json`, un fichier par livre, dossier de
      config, lié au livre par hash SHA-256 du texte extrait) et
      `app/worker.py`/`app/main_window.py` (sauvegarde sur échec, proposition
      de reprise au clic sur "Résumer" si le fichier sélectionné correspond,
      et au démarrage de l'application via `PendingResumesDialog` si un ou
      plusieurs livres sont en attente).
  - `_call_gemini()` effectue un seul appel, sans retry automatique (voir
    historique de conversation du 2026-07-19 : c'est un choix délibéré,
    l'utilisateur doit recliquer lui-même sur "Résumer") : toute erreur API
    (quota RPM/TPM, quota RPD quotidien, `ServiceUnavailable`/
    `InternalServerError`/`DeadlineExceeded` (503/500/504), clé invalide
    `PermissionDenied`/`Unauthenticated`) échoue immédiatement. "Sans retry"
    vaut aussi au niveau de la bibliothèque : `generate_content` est appelé
    avec `request_options={"retry": None}`, car la couche transport de
    `google-generativeai` retente sinon d'elle-même sur 503
    (`ServiceUnavailable`), avec backoff de 1 à 10 s pendant jusqu'à 10 min
    (voir `generative_service/transports/base.py` du paquet installé) -
    chaque tentative supplémentaire était une vraie requête comptée par
    Google (RPM/RPD) mais invisible pour l'application et son suivi de quota
    (découvert le 2026-07-21 en cherchant un écart entre compteur local et
    dashboard AI Studio). Ne pas retirer ce paramètre, et l'ajouter à tout
    nouvel appel de génération. `count_tokens()` garde en revanche le retry
    par défaut de la bibliothèque : appel gratuit hors quota, le laisser
    retenter un 503 est sans conséquence et évite de faire échouer une
    génération pour un simple comptage.
    `quota_tracker.record_call()` est appelé aussi bien en cas de succès
    qu'en cas d'échec de cet appel (bug corrigé le 2026-07-21) : Google
    comptabilise la requête côté serveur (RPM/RPD) dès qu'elle est reçue,
    qu'elle réussisse ou échoue ensuite, donc le suivi local doit faire de
    même sous peine de diverger du dashboard AI Studio - c'est précisément ce
    qui se produisait avant ce correctif à chaque échec en cours de
    génération d'un livre en plusieurs lots. En cas d'échec, il n'y a pas de
    `response.usage_metadata` exploitable : les tokens d'entrée réellement
    envoyés sont alors estimés via `count_tokens()` sur le prompt
    (`estimated_input_tokens`, transmis par l'appelant : `token_count` déjà
    calculé pour le cas single-request, tokens du lot déjà calculés par
    `_split_chapters_into_batches()` pour un lot, ou un appel `count_tokens()`
    dédié pour la requête de consolidation, dont le prompt n'a pas de
    comptage préexistant) ; les tokens de sortie restent à 0, Gemini n'ayant
    rien généré. Voir aussi `app/quota_tracker.py` ci-dessous : `count_tokens()`
    lui-même est un appel gratuit sur un quota séparé, jamais compté dans le
    RPD/RPM suivi ici (vérifié empiriquement le 2026-07-21 : le RPD affiché
    par le dashboard AI Studio ne bouge pas après un appel `count_tokens()`),
    donc l'utiliser pour cette estimation ne coûte jamais de quota
    supplémentaire. `record_call()` n'étant crédité qu'au retour de l'appel
    réseau (succès ou échec), le compteur RPD/RPM affiché restait figé
    pendant toute la durée de cet appel (jusqu'à plusieurs minutes pour un
    gros livre), au point de sembler ne pas bouger du tout à l'envoi d'une
    requête sur un livre tenant en une seule requête (constaté par
    l'utilisateur le 2026-07-21). `quota_tracker.begin_request()` (juste
    avant `model.generate_content()`) et `end_request()` (dans un `finally`
    couvrant tout l'appel, y compris la validation de `response.text`) encadrent
    donc l'appel pour incrémenter/décrémenter `QuotaSnapshot.requests_in_flight`,
    un compteur strictement affiché (indicateur « (+N en attente) » à côté du
    compteur de requêtes du jour dans `main_window._update_quota_display()`),
    qui n'influence jamais `requests_today`/`_recent_calls` : seul
    `record_call()` fait foi pour le quota réel. L'accès à
    `response.text` est protégé contre le
    `ValueError` d'une réponse bloquée par les filtres de sécurité de Gemini
    (message dédié). `_friendly_error_message()`
    traduit chaque cas dans la langue actuellement choisie pour l'UI (via
    `tr()`, voir `app/i18n.py`) avec le code d'erreur d'origine entre
    parenthèses, en invitant à recliquer sur "Résumer" quand pertinent.
    Retourne aussi `error_kind` (`"daily_quota"`/`"rate_quota"`/`None`),
    indépendant de la langue du message : `main_window._on_failed()` s'appuie
    dessus (via le signal `SummarizeWorker.failed`, qui transmet désormais
    `(message, error_kind)`) pour adapter son comportement (ex : proposer une
    reprise), jamais en cherchant un mot-clé dans le message traduit, ce qui
    casserait selon la langue active.
  - **Journal d'appels API** (`_log_api_call()`, ajouté le 2026-07-21 pour
    diagnostiquer un écart inexpliqué entre le compteur local de requêtes
    quotidiennes et celui du dashboard AI Studio) : chaque appel réseau à
    Gemini écrit une ligne horodatée (ISO, en append, jamais écrasé) dans
    `debug_logs/api_requests.log`. Événements consignés : paires
    `ENVOI`/`OK` (ou `ECHEC` avec le type d'exception) de chaque
    `generate_content` avec contexte (`full_report`,
    `chapter_summary_batch_i/N`, `consolidation`), tokens, durée et compteur
    `requetes_jour` après enregistrement ; chaque `count_tokens` (contexte
    `texte_integral`, `decoupage_chapitre_i/N` ou `consolidation`) ;
    `generation DEBUT`/`MODE`/`FIN`/`ECHEC` (marqueurs écrits par
    `generate_book_report()`, devenu une enveloppe de journalisation autour
    de `_generate_book_report_impl()` qui porte la logique) avec l'info de
    reprise, le mode retenu (`une_seule_requete` ou `decoupage_en_lots` avec
    `requetes_generation_attendues`) et les totaux `_api_call_totals`
    (appels de génération et de comptage séparés, plus le cumul
    `tokens_soumis_au_comptage`, remis à zéro à chaque DEBUT - état module
    sans verrou, valide car une seule génération à la fois) ;
    `reponse_illisible` (écrit par `_log_unparsable_response()`, avec le nom
    du fichier de réponse brute correspondant) ; et `application DEMARRAGE`
    (écrit par `main_window` via le point d'entrée public `log_api_event()`,
    avec version, pid, nom de machine (`platform.node()`, pour attribuer son
    origine à un log recueilli sur un autre PC) et compteur quotidien
    rechargé - deux DEMARRAGE à pid différents sans fermeture entre eux
    signalent deux instances simultanées). Jamais le contenu des prompts ni des réponses (volumineux
    et dérivé du livre traité, donc plus sensible que des métadonnées).
    Écriture best-effort (`except OSError: pass`) : ne doit jamais faire
    échouer un appel ni une génération. Pas de rotation ni de purge (choix
    assumé pendant la phase de diagnostic, cohérent avec les fichiers
    `gemini_unparsable_*`).
  - `_parse_json_object()` utilise `json.JSONDecoder().raw_decode()` (pas
    `json.loads()`) pour tolérer du contenu superflu après le premier objet
    JSON valide ; ce surplus est renvoyé séparément (`leftover`), jamais jeté
    silencieusement, et remonté jusqu'à `BookReport.extra_generated_text`.
    Si `raw_decode()` échoue, `_try_repair_stuttered_json()` tente une
    réparation ciblée d'un cas distinct et repéré le 2026-07-20 sur la requête
    de consolidation (`_consolidation_prompt`, la plus longue en sortie) :
    Gemini termine correctement la dernière valeur JSON puis, avant de placer
    l'accolade de fermeture, répète parfois quelques fragments de la toute
    fin du texte déjà produit (`finish_reason` reste `STOP`, confirmé par
    appel API réel : ce n'est pas une troncature par `max_output_tokens`,
    jamais fixé explicitement dans `_get_json_model()`). La réparation
    recoupe le texte ligne par ligne en partant de la fin (au plus
    `_STUTTER_REPAIR_MAX_LINES_DROPPED` lignes retirées), referme l'objet
    avec `}`, et ne retient une coupe que si `_looks_like_stutter()` reconnaît
    le texte retiré comme un bégaiement (court, et entièrement composé de
    fragments déjà présents dans la fin du texte accepté) ; sinon aucune
    réparation n'est tentée et l'erreur `GeminiError` normale remonte, pour ne
    jamais masquer un cas différent (ex. une vraie troncature en plein milieu
    d'une valeur). Si aucune coupe par la fin ne donne de JSON valide,
    `_try_repair_internal_stutter()` tente une seconde variante, repérée le
    2026-07-21 sur un lot de résumés de chapitres
    (`gemini_unparsable_chapter_summary_batch_20260721_164455_360147.txt`) :
    Gemini avait répété un fragment de la fin de la dernière valeur sur une
    ligne parasite puis avait quand même refermé correctement le JSON derrière
    (`}` + `]` + `}`), plaçant la ligne fautive au milieu du texte, hors de
    portée d'une coupe par la fin (qui emporterait aussi les fermetures
    légitimes ; et la refermeture par un unique `}` ne conviendrait de toute
    façon qu'à un bégaiement à la racine, pas à la structure imbriquée des
    lots de chapitres). Cette variante cherche un petit bloc de lignes contigu
    dans les `_STUTTER_REPAIR_MAX_LINES_DROPPED` dernières lignes dont la
    suppression seule (sans rien ajouter) rend le JSON valide, blocs les plus
    petits puis les plus proches de la fin d'abord, et ne retient un bloc que
    si `_looks_like_stutter()` le reconnaît comme un bégaiement du texte qui
    le précède ; contrairement à la coupe par la fin, un bloc refusé
    n'interrompt pas la recherche (il signifie seulement que ce n'était pas le
    bon emplacement). Dans les deux variantes, le texte de bégaiement retiré
    est renvoyé comme `leftover` (donc conservé dans
    `BookReport.extra_generated_text`, jamais jeté silencieusement). Si même
    ces réparations échouent, `_log_unparsable_response()`
    sauvegarde la réponse brute complète (avec le contexte d'appel -
    `"consolidation"`, `"chapter_summary_batch"` ou `"full_report"`, passé en
    paramètre `context_label` à travers `_parse_full_report_json()`/
    `_parse_chapter_summaries_batch_json()` - et l'erreur JSON rencontrée) dans
    un fichier distinct sous `config.get_debug_logs_dir()`
    (`%APPDATA%\Distillat\debug_logs\`, un fichier par échec, jamais écrasé),
    ajouté le 2026-07-21 après un premier cas de bégaiement non couvert par la
    réparation ci-dessus où la réponse brute avait été perdue dès l'affichage
    de l'erreur, empêchant tout diagnostic a posteriori. Écriture best-effort
    (`except OSError: pass`) : ne doit jamais empêcher la `GeminiError`
    normale de remonter à l'utilisateur.
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
  `.generation_resume_<hash>.json` (dossier de config, comme
  `quota_limits.json`). Un fichier par livre interrompu (nommé d'après
  `compute_book_hash()`, SHA-256 du texte extrait) : plusieurs livres peuvent
  donc être en attente de reprise simultanément, chacun dans son propre
  fichier. `load_all_resume_states()` liste tous les états en attente (utilisé
  au démarrage par `PendingResumesDialog`) ; `load_resume_state()` en cible un
  seul par son hash. Le hash sert aussi, comme avant, à vérifier qu'un état
  sauvegardé correspond bien au livre actuellement chargé avant de proposer
  une reprise, pas seulement au chemin de fichier (qui pourrait avoir changé
  de contenu entre-temps). L'ancien format à fichier unique
  (`.generation_resume.json`, sans hash dans le nom) est migré
  automatiquement vers le nouveau format au premier chargement suivant la
  mise à jour, pour ne pas perdre silencieusement une reprise déjà en attente
  chez un utilisateur (`_migrate_legacy_resume_state()`). `clear_resume_state()`
  (appelée par le bouton "Supprimer" de `PendingResumesDialog`) envoie le
  fichier à la corbeille Windows via `send2trash()` plutôt que de le supprimer
  définitivement (`unlink()`), pour laisser une chance de récupération en cas
  de clic accidentel (changement du 2026-07-21) ; `send2trash()` lève
  `FileNotFoundError` sur un fichier absent (contrairement à
  `unlink(missing_ok=True)`), d'où la vérification `exists()` préalable.

- **`app/quota_tracker.py`** (`QuotaTracker`) : suivi *local* et *estimatif*
  des quotas Gemini (RPM/TPM sur fenêtre glissante de 60s, RPD persisté par
  date dans `.quota_state.json`). Les limites par défaut
  (`DEFAULT_RPM/TPM/RPD_LIMIT`) sont ajustables par l'utilisateur via l'UI et
  stockées dans `quota_limits.json`. Ne reflète que ce que *cette*
  application a envoyé (faussé si la même clé est utilisée ailleurs).
  `record_call()` (thread worker) et `snapshot()`/`reload_limits()` (thread
  UI, dont un timer périodique) accèdent au même état : `_lock`
  (`threading.Lock`, non réentrant) protège toute méthode qui le touche -
  ajouter une méthode publique en tenant compte de ça. Le "jour" utilisé pour
  le suivi du RPD (`_pacific_today()`) est calculé en heure du Pacifique
  (`zoneinfo.ZoneInfo("America/Los_Angeles")`, dépendance `tzdata` nécessaire
  sur Windows), jamais en heure locale de l'utilisateur : c'est à minuit
  heure du Pacifique que Google réinitialise réellement ce quota côté
  serveur, pas à minuit heure locale (bug corrigé le 2026-07-21, le suivi se
  basait auparavant sur `date.today()`, l'heure système Windows).
  `record_call()` doit être appelé dès le retour de l'appel réseau à Gemini,
  succès ou échec (voir `app/gemini_client.py::_call_gemini()` ci-dessus) :
  avant le correctif du 2026-07-21, seul un appel réussi incrémentait
  `_requests_today`/`_recent_calls`, désynchronisant le compteur RPD/RPM
  local de celui du dashboard AI Studio à chaque échec en cours de route
  (constaté par l'utilisateur : compteur local à 3 quand le dashboard
  affichait 6, après plusieurs échecs sur un livre en plusieurs lots) - ne
  jamais réintroduire un appel à `record_call()` conditionné à la réussite de
  `generate_content()`. À distinguer de `count_tokens()`
  (`GenerativeModel.count_tokens()`, utilisé par `count_tokens()` du même
  module et par `_split_chapters_into_batches()`) : cet appel est gratuit et
  compté sur un quota séparé (3000 requêtes/minute, propre à `countTokens`),
  jamais sur le RPD/RPM/TPM suivi ici - vérifié empiriquement le 2026-07-21
  (appel réel `count_tokens()` sur le compte de développement : le RPD du
  dashboard AI Studio n'a pas bougé), confirmant la documentation officielle
  ([Firebase AI Logic - Count Tokens](https://firebase.google.com/docs/ai-logic/count-tokens)).
  Il ne faut donc jamais faire remonter les appels `count_tokens()` dans
  `record_call()`/`QuotaSnapshot`, et il est acceptable d'en faire un
  supplémentaire (ex. pour estimer les tokens d'un prompt avant un échec
  potentiel) sans crainte de consommer le quota journalier serré à 20
  requêtes/jour par défaut.
  `begin_request()`/`end_request()` (ajoutés le 2026-07-21) maintiennent un
  compteur séparé `_requests_in_flight`, exposé en lecture seule via
  `QuotaSnapshot.requests_in_flight` : uniquement un indicateur d'affichage
  (« (+N en attente) » à côté du compteur de requêtes du jour, voir
  `main_window._update_quota_display()`) signalant qu'une requête a été
  envoyée à Gemini et n'a pas encore reçu de réponse, pendant que
  `record_call()` (qui, lui, fait foi pour le quota réel RPD/RPM) n'a pas
  encore pu être crédité faute de réponse. Ne jamais laisser ce compteur
  influencer `requests_today`/`_recent_calls` ni les limites affichées.

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
  `releases_page_url()` (lien « Téléchargement » du bandeau de mise à jour et
  du footer) et `repo_page_url()` (lien « Code source » du footer) exposent
  chacun une simple constante d'URL, sans appel réseau.

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
    (non modale, pour le contenu superflu de Gemini), `LicenseDialog`,
    `ChangelogDialog` (affiche `CHANGELOG.md`, même mécanisme de résolution de
    chemin que `LicenseDialog` via `config.get_resource_dir()`, bundlé à la
    compilation via `distillat.spec`), `QuotaHelpDialog` (texte statique fixe,
    sans jargon technique, expliquant le fonctionnement des quotas/requêtes
    Gemini au public non technique : ouverte via le bouton `?` placé à gauche
    de `status_label`, ajouté le 2026-07-21). Ces quatre dialogues utilisent un
    `QPushButton` construit à la main pour leur bouton Fermer/OK, traduit via
    `tr()`, plutôt que `QDialogButtonBox.Close`/`.Ok` : ces boutons standards
    Qt restent affichés en anglais même en français faute de `QTranslator` Qt
    installé pour cette locale (bug constaté le 2026-07-21). `status_label`
    affiche désormais un texte de repos (`main_window.idle_status`) plutôt que
    de rester vide en l'absence de traitement en cours (auparavant vide, ce
    qui isolait visuellement ce bouton `?` sans texte à côté). Le footer de
    `MainWindow` propose aussi, à droite du copyright, les liens « Code
    source » (`update_checker.repo_page_url()`) et « Téléchargement »
    (`update_checker.releases_page_url()`, déjà utilisé par le bandeau de mise
    à jour), ouverts via `webbrowser.open()`.
    `PendingResumesDialog` (appelé par `_offer_pending_resumes()`, invoqué
    depuis `main.py` juste après `window.show()` - et non depuis
    `MainWindow.__init__()` - pour que la fenêtre principale soit déjà visible
    avant l'apparition de ce dialogue modal, bug corrigé le 2026-07-21) liste
    dans un `QListWidget` tous les livres ayant un état de reprise en attente
    (`generation_resume.load_all_resume_states()`) ; une entrée dont le
    fichier livre n'existe plus (déplacé/supprimé) est affichée en rouge avec
    un suffixe traduit et son bouton "Reprendre la sélection" reste désactivé
    (seul "Supprimer" reste possible pour elle). Le focus par défaut est
    explicitement placé sur "Fermer" (`setFocus()` + `setAutoDefault(False)`
    sur les trois boutons) plutôt que de laisser Qt le donner par défaut au
    premier bouton focusable de la ligne, qui est "Supprimer" : un appui
    malencontreux sur la barre espace supprimerait sinon un état de reprise
    (bug corrigé le 2026-07-21). Le livre choisi est seulement chargé via
    `_on_file_selected()` ; la génération n'est pas relancée automatiquement
    (bug corrigé le 2026-07-21 : appeler aussi `_on_summarize_clicked()` ici
    provoquait un doublon, cf. ci-dessous). L'utilisateur reclique donc
    lui-même sur "Résumer", qui reprend alors silencieusement la génération
    interrompue si `_find_resume_state_for()` trouve un état correspondant
    (voir `app/generation_resume.py`). Une boîte de dialogue "Reprendre la
    génération interrompue ?" (Reprendre/Repartir de zéro) existait ici pour
    demander confirmation avant de reprendre, mais fermer cette boîte avec la
    croix de la fenêtre relançait quand même la génération en mode reprise
    (comme si "Reprendre" avait été cliqué) au lieu de n'engager aucune
    génération : plutôt que corriger ce cas particulier, la boîte elle-même a
    été supprimée le 2026-07-21 ("Résumer" reprend désormais toujours
    directement quand un état de reprise existe, sans plus proposer de
    repartir de zéro depuis ce point ; le seul moyen de repartir de zéro est
    désormais de supprimer l'état de reprise via le bouton "Supprimer" de
    `PendingResumesDialog` avant de relancer "Résumer").
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

## Outils de développement (`Tools/`)

Dossier non versionné (`.gitignore`), pour des scripts jetables/utilitaires
réutilisables d'une session à l'autre, sans rapport avec le build ou la
distribution de l'application (voir `build.py`/`distillat.spec` pour ça).

- **`Tools/estimate_tokens.py`** : estime, pour un livre donné (EPUB/PDF), le
  nombre de tokens et le découpage en lots qu'obtiendrait
  `generate_book_report()`, sans lancer de génération complète (donc sans
  consommer le quota RPD réel). Réutilise `worker.parse_book()` et
  `gemini_client.count_tokens()`/`_split_chapters_into_batches()` - à tenir
  synchronisé si la logique de découpage change. Fait un vrai appel réseau
  `count_tokens()` (gratuit, hors quota, voir plus haut) avec la clé API déjà
  enregistrée dans l'application. Usage :
  `python Tools/estimate_tokens.py "chemin/vers/livre.epub"`.
