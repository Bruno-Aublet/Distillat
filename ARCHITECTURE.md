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
  consommés par `MainWindow`. Juste après le calcul du hash du livre (seul
  moment où il est connu pour une première génération, l'extraction ayant
  lieu ici) et avant tout appel à Gemini, `run()` prend le verrou de livre
  (`instance_lock.acquire_book_lock()`, ajouté le 2026-07-22) : si une autre
  instance de Distillat génère déjà ce même livre, la génération échoue
  immédiatement (`failed` avec `error_kind` `"book_locked"`, aucun appel API)
  au lieu de consommer du quota en double pour la même fiche et d'écraser
  tour à tour le même fichier de reprise. Le verrou est libéré dans le
  `finally` de `run()` (succès comme échec, après la sauvegarde éventuelle de
  l'état de reprise), et `locked_book_hash` (champ public, `None` hors
  détention) permet à `main_window._confirm_abort_running_generation()` de le
  libérer après un `terminate()`, qui court-circuite ce `finally`.

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
  - `get_resource_dir()`/`get_app_icon_path()`/`get_success_sound_path()` :
    résolution des ressources embarquées à la compilation (icône, son de fin
    de génération `assets/success.wav`, `LICENSE`, `CHANGELOG.md`) - même
    logique `sys._MEIPASS` en mode gelé que pour le reste de `get_resource_dir()`,
    pour ne jamais dupliquer leur emplacement selon le mode de lancement.
  - Clés API Gemini stockées chiffrées via `keyring` (Gestionnaire
    d'identification Windows), jamais en clair sur disque. Depuis l'ajout du
    support multi-instances (2026-07-22, voir `app/instance_lock.py`
    ci-dessous), plusieurs **profils** nommés peuvent coexister : chaque
    profil (`{"id": <uuid4>, "name": <str>, "model": <str> optionnel}`, sous
    la clé `"api_profiles"` de `settings.json`, `list_profiles()`/
    `save_profiles()`) a sa propre entrée keyring (`gemini_api_key_<id>`,
    `load_profile_api_key()`/
    `save_profile_api_key()`/`delete_profile_api_key()`), distincte pour
    chaque profil. Toutes ces fonctions absorbent `keyring.errors.KeyringError`
    (service indisponible) plutôt que de laisser planter l'application.
    L'ancienne entrée keyring unique (`KEYRING_USERNAME =
    "gemini_api_key"`, `load_api_key()`/`save_api_key()`, conservées
    inchangées) est reprise automatiquement dans un premier profil "Défaut"
    par `_migrate_legacy_api_key_to_profile()` (appelée depuis
    `migrate_legacy_files()`) si aucun profil n'existe encore ; cette
    migration ne fait elle-même qu'une copie (jamais de déplacement), mais
    `_cleanup_legacy_api_key_entry()` (appelée juste après par
    `migrate_legacy_files()`, ajoutée à l'audit de sécurité du 2026-07-22)
    supprime ensuite l'ancienne entrée keyring, uniquement après avoir
    vérifié (relecture réelle via `find_profile_by_api_key()`) qu'une copie
    identique de sa valeur existe bien dans l'entrée d'un profil - sans cette
    purge, une copie du secret restait indéfiniment dans le Gestionnaire
    d'identification Windows, survivant même à une rotation de clé faite
    depuis l'UI (qui ne modifie que l'entrée du profil) ; une ancienne entrée
    dont la valeur ne correspond à aucun profil (seule copie restante d'une
    clé) est laissée intacte, aucune suppression sur un simple doute. Le
    contrôle "aucun profil encore créé" et l'écriture de
    cette migration se font sous le verrou inter-processus de settings.json
    (audit du 2026-07-22) : deux instances lancées simultanément au premier
    démarrage suivant la mise à jour créaient sinon chacune son propre
    profil "Défaut" (UUID différents), la dernière écriture écrasant l'autre
    et laissant son entrée keyring orpheline. `find_profile_by_name(name, exclude_profile_id=None)`/
    `find_profile_by_api_key(api_key, exclude_profile_id=None)` (ajoutées le
    2026-07-22) cherchent un profil existant de même nom, ou de même clé réelle
    (relue via keyring, pas un hash) qu'une valeur candidate ; utilisées par
    `main_window.ProfilesDialog._on_add_clicked()`/`_on_edit_clicked()` pour
    interdire deux profils de même nom ou de même clé (message d'erreur
    nommant le profil déjà concerné), avant tout `save_profile_api_key()`/
    `save_profiles()`. `exclude_profile_id` ignore le profil en cours de
    modification, pour qu'un nom ou une clé laissés inchangés à l'édition ne
    se signalent jamais comme leur propre doublon. `update_profile_model(profile_id, model)`
    (ajoutée le 2026-07-22, choix de modèle Gemini par profil, voir
    `gemini_client.AVAILABLE_MODELS` ci-dessous) change le champ `"model"` d'un
    profil enregistré, même squelette que `rename_profile()` (relecture sous
    verrou inter-processus, tolérant si le profil a disparu). Un profil sans
    ce champ (créé avant cette fonctionnalité) est traité comme utilisant
    `gemini_client.MODEL_NAME` par défaut, résolu via `.get("model", MODEL_NAME)`
    par chaque appelant - aucune migration forcée, le champ s'ajoute
    naturellement à la prochaine édition du profil via l'UI.
  - `load_settings()`/`save_settings(update)` : fonctions génériques centrales
    pour `settings.json` (dossier de config), qui regroupe tous les réglages
    peu fréquemment modifiés (langue de l'UI, prompts personnalisés par
    profil de clé API puis par langue, derniers dossiers utilisés) en un seul
    fichier - à la différence du compteur de quota
    (`.quota_state_<hash>_<modele>.json`, un fichier par clé API depuis le
    2026-07-21, et par modèle depuis le 2026-07-22, voir `quota_tracker.py`
    ci-dessous) ou des limites RPM/TPM/RPD (`quota_limits_<hash>_<modele>.json`,
    même granularité), réécrits bien plus souvent (à
    chaque appel Gemini pour le premier) et donc gardés dans des fichiers
    séparés pour limiter la
    fenêtre d'exposition à une corruption et éviter de réécrire inutilement
    des données volumineuses (les prompts personnalisés) à chaque appel API.
    `save_settings()` fait un cycle lecture-fusion-écriture complet (une clé
    de premier niveau, ex. `"prompts"` ou `"last_dirs"`, n'écrase jamais les
    autres). Depuis l'audit multi-instances du 2026-07-22, ce cycle est
    sérialisé entre processus par `_settings_lock()` (verrou d'un octet du
    fichier dédié `.settings.lock` via `msvcrt.locking`, best-effort : après
    ~10 s d'attente infructueuse, on continue sans verrou plutôt que de
    faire échouer la sauvegarde) et l'écriture est atomique
    (`_write_settings_file()` : fichier temporaire suffixé du PID puis
    `os.replace()`) - sans quoi deux instances parallèles se perdaient
    mutuellement des mises à jour (ex. un profil ajouté par l'une effacé par
    la sauvegarde d'un dernier dossier utilisé dans l'autre, son entrée
    keyring devenant orpheline, irrécupérable depuis l'application), et un
    crash en pleine écriture pouvait laisser un settings.json tronqué que
    `load_settings()` lisait ensuite comme `{}` (perte silencieuse de tous
    les réglages et de la liste des profils). `update_settings(mutate)`
    (ajoutée au même moment) : cycle lecture-modification-écriture complet
    sous ce même verrou, pour toute modification qui dépend du contenu
    existant (ajout à une liste, mise à jour d'un sous-dictionnaire) ;
    `mutate(data)` modifie en place le dict fraîchement relu et retourne
    True si quelque chose a réellement changé (False : rien n'est réécrit).
    Utilisée par `_save_last_dir()`, par les mutateurs de profils
    `add_profile()`/`rename_profile()`/`remove_profile()` (à préférer à un
    enchaînement `list_profiles()` puis `save_profiles()` dans l'appelant,
    qui recréerait la fenêtre de mise à jour perdue que le verrou ferme) et
    par `app/prompts_store.py`. Toute nouvelle fonction de persistance
    ajoutée doit passer par `load_settings()`/`save_settings()`/
    `update_settings()` plutôt que de créer un nouveau fichier, sauf besoin
    similaire à celui du quota (écritures très fréquentes ou volume important
    de données peu liées aux autres réglages).
  - `load_language_setting()`/`save_language_setting()` : langue de l'UI
    choisie par l'utilisateur (code `fr`/`en`), sous la clé `"language"` de
    `settings.json`. `None` si aucune langue n'a encore été enregistrée
    (premier démarrage), consommé par `app/i18n.py` pour déclencher la
    détection depuis la langue système dans ce cas précis.
  - `load_last_report_dir()`/`save_last_report_dir()`,
    `load_last_pdf_dir()`/`save_last_pdf_dir()`,
    `load_last_cover_dir()`/`save_last_cover_dir()` et
    `load_last_book_dir()`/`save_last_book_dir()` : dernier dossier utilisé
    respectivement pour une fiche, un export PDF, le choix manuel d'une image
    de couverture et le sélecteur de fichier de la zone de dépôt (mémorisés
    séparément), sous la clé `"last_dirs"` de `settings.json`. Un dossier
    mémorisé qui n'existe plus (supprimé, périphérique amovible débranché...)
    est traité comme absent (`None`), sans jamais lever d'erreur. Consommé par
    `main_window._default_save_dir()` (fiche), `_default_pdf_dir()` (PDF),
    `_on_set_cover_manually()` (couverture) et `DropZone.mousePressEvent()`
    (zone de dépôt) ; les deux premiers gardent la priorité au dossier de la
    fiche actuellement ouverte si elle en a un. Le dossier est mémorisé à
    chaque sauvegarde/chargement/choix réussi, pas seulement au premier
    usage.
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

- **`app/instance_lock.py`** (ajouté le 2026-07-22) : verrous
  inter-instances, pour permettre de lancer plusieurs instances de Distillat
  en parallèle (une clé différente chacune, usage régulier prévu par
  l'utilisateur) : verrou par profil de clé API (pour qu'une instance ne
  réutilise pas par erreur un profil déjà actif ailleurs) et verrou par livre
  (voir plus bas). Toute la mécanique est commune (`_acquire_lock()`/
  `_is_locked_elsewhere()`/`_release_lock()`, paramétrées par le chemin du
  fichier de verrou), les fonctions publiques par type de verrou n'étant que
  des façades qui résolvent ce chemin. Un fichier
  `.profile_lock_<id_profil>.json` (dossier `config.get_settings_dir()`)
  contient le PID du processus qui détient le
  verrou, sa date de création (`psutil.Process.create_time()`) et le nom de
  la machine (contenu commun avec les marqueurs d'instance, voir
  `_owner_content()`). `acquire_profile_lock(profile_id)` réussit si le
  fichier est absent, ou si son propriétaire n'est plus vivant
  (`_owner_is_alive()`, basé sur psutil - un simple `os.kill(pid, 0)` n'est
  pas fiable sous Windows contrairement à Unix - détecte ainsi un verrou
  orphelin laissé par une instance qui aurait planté sans le libérer
  proprement, avec deux précautions ajoutées à l'audit du 2026-07-22 : un
  PID réutilisé par Windows pour un processus étranger est démasqué par la
  comparaison des dates de création, plutôt que de laisser le profil marqué
  "utilisé" indéfiniment ; et un fichier venant d'une autre machine -
  hostname différent, cas d'un %APPDATA% itinérant partagé - est considéré
  comme détenu, faute de pouvoir vérifier un processus distant) ; échoue
  sans écraser le verrou si un autre processus vivant le détient déjà. La
  création du fichier de verrou est atomique (`os.O_CREAT | os.O_EXCL`,
  avec boucle de relecture si une autre instance gagne la course - audit du
  2026-07-22) : l'ancien enchaînement lire-vérifier-écrire laissait deux
  instances lancées simultanément lire toutes deux "verrou absent" puis
  s'attribuer toutes deux le même profil, donc la même clé API et le même
  fichier de quota. `acquire_profile_lock()` échoue aussi (False) si le
  fichier de verrou ne peut pas être réellement écrit sur disque, plutôt
  que de retourner une fausse possession invisible des autres instances.
  `release_profile_lock(profile_id)` ne
  supprime le verrou que s'il appartient bien au processus courant, pour ne
  jamais effacer par erreur celui d'une autre instance qui l'aurait
  entre-temps repris après un crash de la nôtre. `is_profile_locked_elsewhere(profile_id)`
  (ajoutée à l'audit du 2026-07-22) est une simple lecture, sans jamais
  prendre ni relâcher de verrou par effet de bord : utilisée par
  `ProfilesDialog._reload_list()` pour le seul affichage du suffixe "utilisé
  par une autre fenêtre" dans la liste, qui appelait auparavant
  `acquire_profile_lock()`/`release_profile_lock()` par commodité - un
  acquire/release réel, même bref, pouvait perturber une autre instance en
  train de résoudre son propre profil au même instant via
  `_resolve_active_profile()` ci-dessous (lui faisant croire à tort que le
  profil venait d'être libéré puis repris). `acquire_profile_lock()` reste
  utilisé par `main_window.MainWindow._resolve_active_profile()` (attribution
  automatique du premier profil libre au démarrage, parcouru dans l'ordre de
  `config.list_profiles()`) et par `ProfilesDialog` pour les actions qui
  agissent réellement sur un profil (Modifier/Supprimer/Utiliser), et libéré
  dans `MainWindow.closeEvent()`.

  Verrou par livre (`acquire_book_lock()`/`is_book_locked_elsewhere()`/
  `release_book_lock()`, ajouté le 2026-07-22, fichier
  `.book_lock_<hash>.json` où `<hash>` est le SHA-256 du texte extrait, voir
  `generation_resume.compute_book_hash()`) : garantit que deux instances ne
  génèrent jamais la fiche du même livre en même temps, qu'il s'agisse de
  deux premières générations lancées en parallèle ou de la reprise du même
  état interrompu depuis plusieurs fenêtres (chacune consommerait sinon du
  quota pour produire une fiche identique et, en cas de nouvel échec partiel,
  réécrirait tour à tour le même fichier `.generation_resume_<hash>.json`
  avec sa propre progression, en "dernier qui écrit gagne"). Pris par
  `SummarizeWorker.run()` juste après le calcul du hash et avant tout appel à
  Gemini, libéré dans son `finally` (ou par
  `main_window._confirm_abort_running_generation()` après un `terminate()`) ;
  `is_book_locked_elsewhere()` (lecture seule, mêmes raisons que
  `is_profile_locked_elsewhere()`) sert au rafraîchissement en direct de
  `PendingResumesDialog` et au refus anticipé de `_on_summarize_clicked()`
  quand un état de reprise fournit déjà le hash. Un crash laisse un verrou
  orphelin, démasqué comme pour les profils par `_owner_is_alive()`.

  Mécanisme distinct (ajouté le 2026-07-22 avec le bouton "nouvelle
  instance" du header de `MainWindow`, à droite du titre) : comptage des
  instances Distillat vivantes, indépendant du profil - une instance sans
  profil actif (aucun profil libre, ou aucun profil encore créé) ne détient
  aucun verrou de profil, donc serait invisible à un comptage basé
  uniquement sur `acquire_profile_lock()`. Un fichier `.instance_<pid>.json`
  par instance (`register_instance()`, appelée dans `MainWindow.__init__()`
  juste après la construction de `quota_tracker` ; `unregister_instance()`,
  appelée dans `closeEvent()`) contient les mêmes informations de
  propriétaire que les verrous de profil (PID, date de création, machine).
  `count_alive_instances()` parcourt tous ces fichiers, ne compte que ceux
  dont le propriétaire est vivant (même `_owner_is_alive()` que ci-dessus,
  donc robuste lui aussi à la réutilisation d'un PID), et supprime
  au passage les marqueurs orphelins trouvés (instance qui a planté sans se
  désinscrire) plutôt que de les laisser s'accumuler indéfiniment - même
  esprit que `gemini_client._trim_api_requests_log()`. `MAX_INSTANCES = 4`
  (plafond fixe, pas un réglage utilisateur) : vérifié par
  `MainWindow._on_new_instance_clicked()` avant de lancer un nouveau
  processus, avec un message si la limite est atteinte ; ce n'est pas une
  contrainte de ressource par compte Google comme pour les profils, juste un
  plafond d'ergonomie pour ne pas se retrouver avec un nombre de fenêtres
  difficilement gérable à l'écran.

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
  un titre de repli traduit à défaut) ; extrait la couverture. Retourne
  un `BookContent` (titre, auteur, texte intégral, liste de `Chapter`,
  couverture). `_check_uncompressed_size()` (appelée en tout premier par
  `parse_epub()`, ajoutée à l'audit de sécurité du 2026-07-22) lit uniquement
  le répertoire central du zip (aucune décompression, coût quasi nul) et
  refuse avec un message traduit tout EPUB dont la somme des tailles
  décompressées déclarées dépasse `_MAX_UNCOMPRESSED_EPUB_BYTES` (500 Mo,
  très au-dessus de tout livre légitime) : sans ce plafond, une "bombe zip"
  (fichier minuscule sur disque, gigantesque décompressé) épuisait la mémoire,
  `ebooklib` chargeant tout le contenu en mémoire. Le contrôle est fiable
  (zipfile, utilisé par `ebooklib`, refuse de lire au-delà de la taille
  déclarée d'une entrée : mentir sur les tailles ne le contourne pas), et un
  fichier illisible ou qui n'est pas un vrai zip est laissé passer tel quel,
  `read_epub()` levant alors sa propre erreur comme avant. `read_epub()` passe explicitement `options={"ignore_ncx":
  False}` pour continuer à s'appuyer sur le NCX (table des matières EPUB2)
  même sur les versions récentes d'`ebooklib` (0.20+) où ce comportement n'est
  plus le défaut ; à ne pas retirer sans vérifier que la table des matières
  reste correctement extraite sur des EPUB2 legacy n'ayant pas de Navigation
  Document EPUB3.
  `_find_cover_image_bytes()` (beaucoup d'EPUB ne taguent pas proprement leur
  couverture) essaie dans l'ordre : type `ITEM_COVER`, métadonnée OPF
  `<meta name="cover">`, nom/id d'image contenant "cover", puis en dernier
  repli nom d'image commençant par "fc" ou contenant "front" (convention
  "front cover"/"rear cover" rencontrée sur certains EPUB, ex. constaté le
  2026-07-22 sur un EPUB nommant ses images `fc_750px.jpg`/`rc_750px.jpg` sans
  aucune des 3 premières stratégies applicable). Si les 4 échouent, la fiche
  reste sans couverture automatique mais l'utilisateur peut en définir une
  manuellement depuis l'UI (voir `main_window.py` ci-dessous).

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

- **`app/gemini_client.py`** (coeur de la génération) : utilise le SDK
  `google-genai` (migré depuis `google-generativeai` le 2026-07-21, dépôt
  d'origine archivé par Google au profit de ce SDK unifié - voir
  `CHANGELOG.md`). Un seul `genai.Client` module-level (`configure()`), créé
  avec la clé API et `_HTTP_OPTIONS_NO_RETRY` ; pas d'équivalent du
  `genai.configure()` global de l'ancien SDK, ni de `GenerativeModel` par
  appel : `model=` est passé explicitement à chaque appel
  (`_client.models.generate_content(...)`/`count_tokens(...)`), résolu depuis
  le paramètre `model` propagé de bout en bout (voir ci-dessous), avec
  `MODEL_NAME` comme valeur par défaut.
  - `MODEL_NAME = "gemini-3.5-flash"` reste la valeur par défaut/de
    rétrocompatibilité, mais depuis le 2026-07-22 (choix de modèle Gemini par
    profil, voir `app/config.py` ci-dessus) le modèle réellement utilisé est
    choisissable par profil et propagé en paramètre explicite `model: str`
    à travers toute la chaîne d'appel (`main_window` -> `worker.SummarizeWorker`
    -> `gemini_client.generate_book_report()`/`_generate_book_report_impl()`
    -> `_call_gemini()`/`count_tokens()`/`_split_chapters_into_batches()`),
    même mécanisme que `profile_id` pour la résolution des prompts
    personnalisés - pas de variable globale mutable, pas de singleton.
    `AVAILABLE_MODELS: list[ModelInfo]` est le registre centralisé des
    modèles proposés au choix (`ModelInfo(name, max_input_tokens,
    max_tokens_per_request)`), résolu par nom via `get_model_info(name)`
    (repli sur le premier élément de la liste si le nom stocké dans un vieux
    profil ne correspond à aucune entrée connue, ex. modèle retiré depuis).
    Ajouter ou retirer un modèle proposé se limite à modifier cette liste :
    `gemini-3.5-flash` et `gemini-3.6-flash` y partagent aujourd'hui les
    mêmes valeurs (`max_input_tokens=900_000`, `max_tokens_per_request=200_000`),
    par choix documenté en commentaire (specs et quotas gratuits identiques
    vérifiés le 2026-07-22), pas par oubli - à revoir si un futur modèle aux
    caractéristiques différentes est ajouté. Le mode JSON forcé
    (`response_mime_type="application/json"`, pour fiabiliser le JSON en
    sortie) est un `genai_types.GenerateContentConfig` passé en paramètre
    `config=` de chaque appel de génération (`_JSON_GENERATION_CONFIG`), pas
    un modèle séparé comme avec l'ancien SDK.
  - `generate_book_report()` est le point d'entrée : compte les tokens du
    texte intégral, puis deux cas selon ce compte, plus un repli particulier
    au sein du second (voir plus bas) :
    - **Texte tient dans `max_tokens_per_request` du `ModelInfo` résolu (200k
      pour les deux modèles actuels)** : un seul appel
      combiné demandant résumé court + détaillé + personnages + analyse en
      JSON (`_full_report_prompt`, prompt par défaut `full_report`).
    - **Texte trop long** : `_split_chapters_into_batches()` répartit les
      chapitres en lots consécutifs tenant chacun sous `max_tokens_per_request`
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
      - **Repli si un seul lot** : `count_tokens(full_text)` (mesuré en bloc,
        décide du cas ci-dessus) et la somme des `count_tokens(chapter.text)`
        (mesurée chapitre par chapitre par `_split_chapters_into_batches()`)
        peuvent diverger légèrement selon le découpage soumis au tokenizer,
        pour un même texte au caractère près (`full_text` n'est que la
        concaténation des `chapter.text`, voir `epub_parser.parse_epub()`).
        Un livre tout juste au-dessus de 200k tokens en comptage bloc peut
        donc voir ses chapitres tenir malgré tout en un seul lot une fois
        comptés séparément puis additionnés. Si `_split_chapters_into_batches()`
        ne produit qu'un seul lot **et** qu'aucune reprise n'est en cours
        (`resume_chapter_summaries` vide - une reprise implique
        `batches_total >= 2`, voir plus bas, donc ce cas ne peut de toute
        façon pas coexister avec un lot unique), `generate_book_report()`
        traite ce lot comme le cas "une seule requête" ci-dessus
        (`_full_report_prompt` sur `full_text`) plutôt que d'enchaîner
        résumé-de-lot puis consolidation séparée : même texte envoyé au
        final, une requête Gemini économisée. Aucun risque de dépassement
        TPM introduit par ce repli : `max_tokens_per_request` (200k) garde
        déjà 50k tokens de marge sous la vraie limite TPM (250k), largement
        suffisante pour couvrir l'écart de comptage et les instructions
        ajoutées par `_full_report_prompt`. Décidé en conversation le
        2026-07-22 suite à un livre affichant "lot de chapitres 1/1" dans
        l'UI - comportement correct mais déroutant sans cette explication.
    - **`max_tokens_per_request` (200k) vs `max_input_tokens` (900k) du `ModelInfo` résolu** : ne
      pas confondre les deux (bug corrigé le 2026-07-19, à l'époque constantes
      globales `MAX_TOKENS_PER_REQUEST`/`MAX_INPUT_TOKENS`, devenues champs de
      `ModelInfo` le 2026-07-22). `max_input_tokens`
      documente la fenêtre de contexte du modèle (limite haute, rarement
      atteinte) ; `max_tokens_per_request` est la vraie contrainte
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
      plusieurs livres sont en attente). Depuis le 2026-07-22 (choix de modèle
      par profil), `ResumeState`/le JSON persisté incluent aussi le modèle
      utilisé pour cette génération (`model`, repli sur `MODEL_NAME` si absent
      d'un ancien fichier) : `worker.SummarizeWorker.run()` fait primer ce
      modèle d'origine sur le modèle actuellement actif du profil dès qu'une
      reprise correspond au livre en cours, pour ne jamais mélanger dans une
      même fiche des résumés de chapitres produits par deux modèles
      différents - le modèle actif du profil ne s'applique qu'à la prochaine
      génération lancée sur un livre neuf, sans reprise en attente.
  - `_call_gemini()` effectue un seul appel, sans retry automatique (voir
    historique de conversation du 2026-07-19 : c'est un choix délibéré,
    l'utilisateur doit recliquer lui-même sur "Résumer") : toute erreur API
    (quota RPM/TPM, quota RPD quotidien, service indisponible/erreur
    serveur/timeout (503/500/504), clé invalide (400 avec
    `error.details[].reason == "API_KEY_INVALID"`, ou 401/403)) échoue
    immédiatement. `google-genai` ne distingue plus ces cas par une classe
    Python dédiée par erreur (contrairement à l'ancien SDK
    `google-generativeai` : `ResourceExhausted`/`ServiceUnavailable`/etc.) :
    seulement deux sous-classes génériques de `genai_errors.APIError`
    (`ClientError` pour 4xx, `ServerError` pour 5xx), toutes deux exposant
    `exc.code` (l'entier HTTP) et `exc.details` (le corps JSON brut de
    l'erreur) ; `_friendly_error_message()` aiguille donc explicitement sur
    `exc.code`, et `_extract_quota_blocked_info()`/`_error_reason()` cherchent
    dans `exc.details["error"]["details"]` les blocs identifiés par leur clé
    `"@type"` (`QuotaFailure` pour le `quotaId` distinguant quota
    journalier/par minute, `RetryInfo` pour le délai de nouvelle tentative,
    `ErrorInfo` pour le `reason` d'une clé invalide) - vérifié empiriquement
    contre de vraies erreurs 429/400 le 2026-07-21 lors de la migration.
    "Sans retry" vaut aussi au niveau du SDK : le `Client` est créé
    (`configure()`) avec `_HTTP_OPTIONS_NO_RETRY`
    (`HttpRetryOptions(attempts=1)`), car `google-genai` retente sinon
    lui-même jusqu'à 5 fois sur 408/429/5xx par défaut (contrairement à
    l'ancien SDK, ce comportement est ici documenté et configurable
    explicitement, plutôt qu'un comportement caché découvert dans le code
    source du paquet installé) - chaque tentative supplémentaire était une
    vraie requête comptée par Google (RPM/RPD) mais invisible pour
    l'application et son suivi de quota (découvert le 2026-07-21 en
    cherchant un écart entre compteur local et dashboard AI Studio avec
    l'ancien SDK ; même risque avec le nouveau si le retry par défaut restait
    actif). Ne pas retirer ce paramètre du `Client`. `count_tokens()` garde
    en revanche le retry par défaut du SDK, via `_COUNT_TOKENS_CONFIG`
    (`CountTokensConfig(http_options=HttpOptions())`, qui écrase le
    `retry_options` hérité du `Client` pour cet appel précis) : appel gratuit
    hors quota, le laisser retenter un 503 est sans conséquence et évite de
    faire échouer une génération pour un simple comptage.
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
    `debug_logs/api_requests.log`, préfixée par `pid=<PID>` juste après le
    timestamp (ajouté le 2026-07-22, support multi-instances) : ce fichier
    est partagé par toutes les instances de Distillat lancées sur la machine
    (`get_debug_logs_dir()` est unique, indépendant du mode de lancement,
    voir règle 8 de `CLAUDE.md`), leurs lignes s'entrelacent donc
    chronologiquement si plusieurs tournent en parallèle (usage prévu, voir
    `app/instance_lock.py`) ; ce préfixe, ajouté une seule fois dans
    `_log_api_call()` (donc sur chaque ligne sans avoir à modifier chacun des
    appelants), permet de filtrer après coup les lignes d'une seule instance
    (`grep "pid=12345"`). Événements consignés : paires
    `ENVOI`/`OK` (ou `ECHEC` avec le type d'exception) de chaque
    `generate_content` avec contexte (`full_report`,
    `chapter_summary_batch_i/N`, `consolidation`), tokens, durée et compteur
    `requetes_jour` après enregistrement ; chaque `count_tokens` (contexte
    `texte_integral`, `decoupage_chapitre_i/N` ou `consolidation`) ;
    `generation DEBUT`/`MODE`/`FIN`/`ECHEC` (marqueurs écrits par
    `generate_book_report()`, devenu une enveloppe de journalisation autour
    de `_generate_book_report_impl()` qui porte la logique) avec le numéro de
    version de l'application (`app.__version__.VERSION`, ajouté le
    2026-07-22 sur la ligne `generation DEBUT` - déjà présent par ailleurs sur
    `application DEMARRAGE`, voir plus bas), l'info de
    reprise, le mode retenu (`une_seule_requete` ou `decoupage_en_lots` avec
    `requetes_generation_attendues`) et les totaux `_api_call_totals`
    (appels de génération et de comptage séparés, plus le cumul
    `tokens_soumis_au_comptage`, remis à zéro à chaque DEBUT - état module
    sans verrou, valide car une seule génération à la fois) ;
    `reponse_illisible` (écrit par `_log_unparsable_response()`, avec le nom
    du fichier de réponse brute correspondant) ; et `application DEMARRAGE`
    (écrit par `main_window` via le point d'entrée public `log_api_event()`,
    avec version, nom de machine (`platform.node()`, pour attribuer son
    origine à un log recueilli sur un autre PC), nom du profil de clé API
    attribué à cette instance par `_resolve_active_profile()` (`"(aucun)"` si
    aucun profil n'a pu être attribué) et compteur quotidien rechargé - le
    pid, qui distingue déjà deux instances via le préfixe systématique décrit
    plus haut, n'a plus besoin d'être répété dans le message lui-même depuis
    le 2026-07-22). Jamais le contenu des prompts ni des réponses (volumineux
    et dérivé du livre traité, donc plus sensible que des métadonnées).
    Écriture best-effort (`except OSError: pass`) : ne doit jamais faire
    échouer un appel ni une génération. Purgé par `_trim_api_requests_log()`
    (ajouté le 2026-07-21) : au démarrage d'une génération (marqueur
    `generation DEBUT`), si le fichier contient déjà
    `API_REQUESTS_LOG_MAX_GENERATIONS` (5) occurrences de ce marqueur, tout ce
    qui précède la 2e occurrence est supprimé (donc le bloc complet du plus
    ancien livre, y compris un éventuel `application DEMARRAGE` initial qui le
    précédait) - le fichier ne contient donc jamais plus de 5 générations
    passées à la fois. Choix délibérément petit (le fichier doit rester
    collable tel quel dans une conversation pour diagnostic), au prix de ne
    couvrir qu'un historique court plutôt que toute la phase de diagnostic.
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
    bon emplacement). `_looks_like_stutter()` exige normalement que chaque
    mot du texte retiré se retrouve tel quel dans la fin du texte accepté,
    sauf en dessous de `_STUTTER_SHORT_FRAGMENT_LENGTH` (25 caractères
    normalisés) où le fragment est accepté sans cette correspondance
    mot-à-mot : ajouté le 2026-07-22 après un cas où Gemini avait coupé un
    mot en plein milieu dans son bégaiement (`"anation culturelle."` pour la
    fin de `"...profanation scientifique."`,
    `gemini_unparsable_consolidation_20260722_100958_322453.txt`), un mot
    tronqué ne pouvant par nature jamais se retrouver tel quel dans le texte
    déjà accepté. Dans les deux variantes, le texte de bégaiement retiré
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
    normale de remonter à l'utilisateur. Purgé juste après l'écriture (même
    2026-07-21) : les fichiers `gemini_unparsable_*.txt` du dossier sont
    triés par date de modification, et seuls les `UNPARSABLE_LOGS_MAX_FILES`
    (5) plus récents sont conservés, les plus anciens étant supprimés
    (`unlink(missing_ok=True)`, best-effort comme le reste).
  - `_call_gemini()` renvoie désormais un tuple `(text, finish_reason)` (et
    non plus seulement `text`), `finish_reason` étant celui du premier
    candidat (`response.candidates[0].finish_reason`, `None` si aucun
    candidat). Il est systématiquement journalisé dans `api_requests.log` sur
    la ligne `generate_content OK`, même quand `text` est exploitable (donc
    même en dehors du cas `if not text` qui gérait déjà le blocage par
    filtres de sécurité) : ajouté le 2026-07-22 après un cas
    (`gemini_unparsable_consolidation_20260722_110125_965039.txt`) où la
    réponse de consolidation s'arrêtait net après la valeur de `"analysis"`
    (aucune accolade fermante, aucun contenu superflu - donc ni un cas géré
    par `_try_repair_stuttered_json()`, ni un simple contenu superflu géré par
    `raw_decode()`), sans qu'aucune trace de `finish_reason` n'ait été
    conservée pour confirmer une troncature par `MAX_TOKENS` : seule l'erreur
    générique de parsing JSON était visible a posteriori. `finish_reason` est
    transmis à `_parse_json_object()` (et par elle à
    `_parse_full_report_json()`/`_parse_chapter_summaries_batch_json()`, qui
    le reçoivent aussi en paramètre) : si le parsing direct ET les deux
    réparations de bégaiement échouent, et que `finish_reason == "MAX_TOKENS"`,
    le message d'erreur dédié `gemini_errors.truncated_response` est levé
    (réponse coupée par la limite de longueur du modèle) au lieu du message
    générique `gemini_errors.unreadable_response` - qui reste utilisé pour
    tout échec de parsing sans cette confirmation.
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
  - **Arrêt prématuré sans erreur (constaté le 2026-07-22 avec gemini-3.6-flash)** :
    `DEFAULT_FULL_REPORT_PROMPT`/`_EN` et `DEFAULT_CONSOLIDATION_PROMPT`/`_EN`
    demandent 4 éléments (`summary`, `detailed_summary`, `characters`,
    `analysis`) dans un seul objet JSON, mais seul `summary` est vérifié comme
    obligatoire par `_parse_full_report_json()` (ligne ~1135, `if not summary:
    raise GeminiError(...)`) - `detailed_summary`/`characters`/`analysis` se
    replient silencieusement sur une valeur vide si absents, sans lever
    d'erreur. Un livre généré avec gemini-3.6-flash a ainsi produit un JSON
    syntaxiquement valide et un `finish_reason` normal (pas de troncature),
    mais avec `characters: []` et `analysis: ""`, après un `detailed_summary`
    deux fois plus long que la normale (comparé au même livre généré avec
    gemini-3.5-flash) : le modèle s'est arrêté de lui-même après le second
    élément plutôt que de produire les quatre, sans que rien dans le code ne
    détecte ni ne signale ce cas. Les 4 templates par défaut ont été renforcés
    en conséquence : une consigne générale ("les quatre sont OBLIGATOIRES...
    ne t'arrête JAMAIS en cours de route", volontairement non ciblée sur un
    champ précis pour ne pas laisser croire qu'un arrêt après un autre champ
    serait acceptable) ajoutée après l'énumération des 4 éléments, et un
    rappel similaire juste avant/dans le bloc JSON attendu. Aucun garde-fou
    côté code (retry automatique si `characters`/`analysis` reviennent vides)
    n'a été ajouté à ce stade, décision explicite de l'utilisateur - à
    reconsidérer si le comportement se reproduit malgré le renforcement du
    prompt. Cette consigne ne s'applique qu'aux prompts par défaut : un
    prompt personnalisé par l'utilisateur (voir `app/prompts_store.py`) qui
    remplacerait un de ces 4 templates n'en bénéficie pas automatiquement.
    Le marqueur de titre de chapitre inséré par `_chapters_batch_text()`
    (`[[[TITRE: ...]]]`/`[[[TITLE: ...]]]`) suit aussi la langue active, pour
    rester cohérent avec celui annoncé dans le prompt de résumé de lot.

- **`app/prompts_store.py`** : persistance des prompts personnalisés sous la
  clé `"prompts_by_profile"` de `settings.json` (lectures via
  `config.load_settings()`, écritures via `config.update_settings()` -
  relecture et fusion sous le verrou inter-processus de settings.json depuis
  l'audit du 2026-07-22, pour ne jamais écraser les personnalisations
  sauvées au même moment par une autre instance pour un autre profil),
  imbriqués par profil de clé API PUIS par langue
  (`{"<id_profil>": {"fr": {...}, "en": {...}}, ...}`, 2026-07-22, support des
  profils multiples) : personnaliser un prompt pour un profil ne doit jamais
  affecter un autre profil, et dans un profil donné, personnaliser un prompt
  dans une langue ne doit jamais affecter l'autre, sous peine de mélanger un
  texte français et une consigne de sortie anglaise (ou l'inverse) dès que
  l'utilisateur change la langue de l'UI (bug vécu le 2026-07-20, avant cette
  séparation par langue - le même risque existerait entre profils sans la
  séparation ajoutée le 2026-07-22). `load_custom_prompts(language,
  profile_id)`/`save_custom_prompts(language, prompts, profile_id)`/
  `reset_custom_prompt(language, key, profile_id)` prennent désormais toutes
  un `profile_id` obligatoire (résolu par l'appelant depuis
  `MainWindow.active_profile`, jamais `None` en pratique puisqu'une fenêtre
  Prompts ne s'ouvre que si un profil est actif, voir `main_window.py`
  ci-dessous) ; `gemini_client._get_prompt_template()` accepte `profile_id:
  str | None` et se rabat sur les prompts par défaut si `None` (ne devrait
  pas arriver en production, une génération exigeant déjà une clé API donc un
  profil résolu). `_migrate_legacy_global_prompts_to_profile(profile_id)`
  reprend l'ancienne personnalisation globale (stockée sous l'ancienne clé
  `"prompts"`, avant l'introduction des profils multiples) dans le PREMIER
  profil qui la consulte ou la modifie (typiquement le profil "Défaut" issu
  de la migration de la clé API unique, voir
  `config._migrate_legacy_api_key_to_profile()`), puis VIDE aussitôt
  l'ancienne clé `"prompts"` globale (`save_settings({"prompts_by_profile":
  ..., "prompts": {}})`) : cette migration ne doit s'appliquer qu'une seule
  fois tous profils confondus, jamais par profil - la vider empêche qu'un
  second profil, consulté après le premier mais lui non plus jamais encore
  présent dans `"prompts_by_profile"`, hérite à tort de la même donnée
  globale que le premier (bug constaté et corrigé le 2026-07-22 pendant les
  tests de ce chantier : sans le vidage, deux profils distincts se
  retrouvaient avec exactement la même personnalisation "héritée"). Un ancien
  stockage à
  plat par langue (format antérieur à l'introduction du bilinguisme, avant
  même l'existence de `settings.json`) reste migré silencieusement vers le
  français (`_LEGACY_FORMAT_LANGUAGE`, seule langue de l'application à cette
  époque) à la première lecture, désormais à l'intérieur des données d'un
  profil plutôt qu'à la racine. Une clé absente pour une langue donnée
  signifie "utiliser le prompt par défaut de cette langue". `PROMPT_KEYS` doit
  rester synchronisé avec les clés de `default_prompt_templates()`.
  `save_custom_prompts()` compare chaque prompt reçu au défaut actuel
  (`gemini_client.default_prompt_templates()`, importé localement dans la
  fonction pour éviter un import circulaire avec `gemini_client.py`, qui
  importe déjà `prompts_store`) et n'écrit sur disque que les valeurs qui en
  diffèrent réellement : bug corrigé le 2026-07-22, valider la fenêtre
  "Prompts" (`PromptsDialog`) sans avoir rien modifié gravait auparavant une
  copie complète et figée des 3 prompts par défaut du moment, qui divergeait
  ensuite silencieusement de tout futur changement de ces prompts par défaut
  dans le code (`load_custom_prompts()` préfère toujours la version sur
  disque). `reset_custom_prompt()` efface immédiatement et définitivement, sur
  disque, la personnalisation d'un seul prompt/langue/profil (sans toucher aux
  deux autres clés, à l'autre langue, ni à un autre profil) : appelée par le
  bouton "Réinitialiser ce prompt" de `PromptsDialog`, indépendamment du
  bouton Sauvegarder/Annuler de la fenêtre - un reset est une action
  immédiate et permanente, pas une simple modification du texte affiché en
  attente de validation.

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
  La reprise du même état (ou la génération du même livre) depuis plusieurs
  instances de Distillat en parallèle est empêchée par le verrou de livre
  (2026-07-22, voir `app/instance_lock.py` et `app/worker.py`) : sans lui,
  chaque instance réécrivait tour à tour ce même fichier avec sa propre
  progression en cas de nouvel échec partiel.

- **`app/quota_tracker.py`** (`QuotaTracker`) : suivi *local* et *estimatif*
  des quotas Gemini (RPM/TPM sur fenêtre glissante de 60s, RPD persisté par
  date dans `.quota_state_<hash>_<modele>.json`, un fichier par (clé API,
  modèle) - voir plus bas). Les limites par défaut (`DEFAULT_RPM/TPM/RPD_LIMIT`)
  sont ajustables par l'utilisateur via l'UI et stockées dans
  `quota_limits_<hash>_<modele>.json`, même granularité
  (`quota_limits_path_for_key()`, même principe que
  `daily_state_path_for_key()`, 2026-07-22, support des profils multiples puis
  du choix de modèle par profil) :
  deux comptes Google avec des paliers différents (gratuit standard,
  payant...), ou deux modèles utilisés avec la même clé, peuvent ainsi avoir
  des limites configurées différemment, plutôt
  que de partager un seul fichier global. `model_slug(model)` normalise le nom
  du modèle pour un nom de fichier sûr (les points de "gemini-3.5-flash" ne
  posent pas problème sur Windows, mais sont remplacés par des tirets par
  cohérence). `QuotaTracker.quota_limits_path`
  (nouveau champ, `None` tant qu'aucun contexte (clé, modèle) n'a encore été
  sélectionné via `switch_context()`) est rebasculé et migré
  (`_migrate_legacy_quota_limits_if_needed()`,
  reprise une seule fois de l'ancien fichier unique `quota_limits.json`, ou du
  fichier par clé sans modèle `quota_limits_<hash>.json` (avant le
  2026-07-22, seulement si le modèle sélectionné est celui par défaut), vers
  le fichier (clé, modèle) qui l'utilise en premier après cette mise à jour,
  même mécanisme que
  `_migrate_legacy_daily_state_if_needed()`) à chaque appel à
  `switch_context()`, en même temps que `daily_state_path`. `load_quota_limits(limits_path)`/
  `save_quota_limits(limits_path, ...)` prennent désormais directement le
  chemin du fichier concerné plutôt qu'un `settings_dir` + résolution
  implicite : à l'appelant (`main_window._on_edit_quota_limits()`) d'utiliser
  `quota_tracker.quota_limits_path`, `None` (donc aucune édition possible,
  message dédié affiché) si aucun profil n'est encore actif dans cette
  instance. Ne reflète que ce que *cette* application a envoyé (faussé si la
  même clé est utilisée ailleurs).
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
  (`_client.models.count_tokens()`, utilisé par `count_tokens()` du même
  module et par `_split_chapters_into_batches()`) : cet appel est gratuit et
  compté sur un quota séparé (3000 requêtes/minute, propre à `countTokens`),
  jamais sur le RPD/RPM/TPM suivi ici - vérifié empiriquement le 2026-07-21
  (appel réel `count_tokens()` sur le compte de développement : le RPD du
  dashboard AI Studio n'a pas bougé), confirmant la documentation officielle
  ([Firebase AI Logic - Count Tokens](https://firebase.google.com/docs/ai-logic/count-tokens),
  qui documente ce produit précis, pas directement l'API Gemini/AI Studio
  utilisée ici par clé API - aucune page officielle équivalente trouvée pour
  cette dernière). Re-vérifié empiriquement après la migration vers
  `google-genai` (2026-07-21) : 15 appels `count_tokens()` en rafale sur le
  compte de développement n'ont provoqué aucune erreur 429, alors que le
  quota `generate_content` de ce même compte est limité à 5 requêtes/minute -
  confirme que `count_tokens()` reste bien hors de ce quota avec le nouveau
  SDK. Il ne faut donc jamais faire remonter les appels `count_tokens()` dans
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
  **Isolation par clé API** (`switch_api_key()`, ajoutée le 2026-07-21,
  renommée `switch_context()` le 2026-07-22 pour aussi isoler par modèle) :
  avant ce fix, `daily_state_path` était un chemin fixe unique
  (`.quota_state.json`), donc changer de clé API en cours de journée
  (bouton **Clé API**, ou en testant plusieurs comptes Google) continuait de
  lire/écrire le même fichier, mélangeant silencieusement la consommation de
  deux comptes distincts dans le même compteur affiché - constaté en testant
  avec un second compte pour contourner un quota journalier atteint sur le
  premier. `api_key_hash()` (SHA-256 tronqué à 8 caractères, jamais la clé en
  clair - même dérivation que le hash déjà journalisé par `main_window` dans
  `api_requests.log`, `cle_api_hash=...`) et `daily_state_path_for_key()`
  dérivent un fichier `.quota_state_<hash>_<modele>.json` par (clé, modèle)
  depuis l'introduction du choix de modèle par profil (2026-07-22) : chaque
  modèle utilisé avec une même clé a ses propres compteurs, même si
  `gemini-3.5-flash` et `gemini-3.6-flash` partagent aujourd'hui les mêmes
  limites.
  `switch_context(api_key, model)` : no-op si ni le hash ni le modèle n'ont
  changé depuis le dernier appel (ou l'initialisation) ; sinon réinitialise
  tout l'état en
  mémoire (tokens cumulés, fenêtre glissante RPM, `_requests_in_flight` -
  aucun sens pour un autre contexte), pointe `daily_state_path` vers le
  fichier du nouveau contexte, migre une seule fois soit l'ancien fichier
  unique `.quota_state.json`, soit le fichier par clé sans modèle
  `.quota_state_<hash>.json` (avant le 2026-07-22, uniquement si le modèle
  sélectionné est celui par défaut `MODEL_NAME`, seul modèle ayant pu produire
  ce fichier) s'ils existent encore et que le nouveau fichier n'existe
  pas (`_migrate_legacy_daily_state_if_needed()`, `Path.replace()`), puis
  recharge le compteur RPD persistant de ce fichier. Appelée par
  `main_window` : à la construction de `MainWindow` si une clé est déjà
  enregistrée (sinon l'affichage resterait sur le tracker construit avec un
  chemin provisoire `.quota_state_pending.json` jusqu'au premier lancement),
  dans `_on_summarize_clicked()` juste après `_ensure_api_key()` (avant de
  créer le `SummarizeWorker`, avec le modèle résolu du profil actif), et dans
  `_prompt_for_api_key()` juste après
  l'enregistrement d'une nouvelle clé (avec rafraîchissement immédiat de
  l'affichage via `_update_quota_display()`, sans attendre la prochaine
  génération).

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
  du footer), `repo_page_url()` (lien « Code source » du footer) et
  `project_site_url()` (lien « Page web » du footer, ajouté le 2026-07-22,
  vers https://bruno-aublet.github.io/Distillat/, la landing page du projet
  hébergée via GitHub Pages) exposent chacun une simple constante d'URL, sans
  appel réseau.

- **`app/book_report.py`** (`BookReport`, `Character`) : structure de données
  centrale de la fiche + sérialisation JSON (`to_json`/`from_json`/`save`/`load`,
  `FILE_FORMAT_VERSION = 2`). `extra_generated_text` existe uniquement en
  mémoire (jamais persisté, ni JSON ni PDF) : contenu superflu généré par
  Gemini, à la disposition de l'utilisateur via l'UI mais pas de la fiche.
  `from_json()`/`load()` ne réécrivent jamais le fichier source (même si la
  couverture est recompressée au passage, en mémoire uniquement) : une
  simple lecture ne doit jamais modifier le fichier lu. La couverture base64
  d'une fiche chargée est plafonnée à `_MAX_COVER_B64_LENGTH` (20 millions de
  caractères, soit ~15 Mo décodés - très au-dessus de toute couverture
  légitime) avant décodage : au-delà, elle est ignorée et la fiche se charge
  sans couverture plutôt que de décoder en mémoire le blob démesuré d'un
  fichier `.distillat.json` piégé ou corrompu (audit de sécurité du
  2026-07-22), même philosophie que `shrink_cover_image()` (une couverture
  inutilisable ne fait jamais échouer le chargement).
  `sanitize_filename()` nettoie un titre de livre pour en faire un nom de
  fichier Windows valide, par exclusion (retire uniquement les caractères
  réellement interdits par Windows `< > : " / \ | ? *` et les caractères de
  contrôle) et non par liste blanche : tout le reste, y compris la
  ponctuation peu courante (`&`, `#`, `@`...) et les accents, est conservé.
  Utilisée à la fois pour le nom de fichier de la fiche JSON (`save()`) et
  pour celui de l'export PDF (`main_window.py`). Retombe sur `fallback`
  (résolu via `tr("book_report.fallback_filename")` si non fourni
  explicitement, jamais figé en français : le paramètre par défaut ne peut
  pas appeler `tr()` au chargement du module, avant que la langue soit
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
    défaut, voir `app/update_checker.py`), label du profil de clé API actif
    (`active_profile_label`, à côté du bouton "Profils") : affiche depuis le
    2026-07-22 le nom du profil et le modèle Gemini actuellement utilisé
    (`profile.get("model", MODEL_NAME)`) sur deux lignes distinctes (`\n`
    dans la clé de traduction `main_window.active_profile_label`, `Qt.AlignCenter`),
    rafraîchi par `_update_profile_label()` à chaque changement de profil
    actif ou de modèle via la fenêtre **Profils**.
  - `setMinimumSize(400, 300)` (appelé dans `__init__`, avant
    `_size_to_available_screen()` qui dimensionne/positionne la fenêtre à
    l'ouverture) : sans cet appel, Qt calcule automatiquement une taille
    minimale à partir du contenu du layout (header, onglets...), assez
    grande pour empêcher Windows Snap de réduire la fenêtre à 1/4 d'écran
    sur certains moniteurs (constaté le 2026-07-22). Cette valeur volontai-
    rement petite ne réorganise jamais le contenu du layout : en dessous de
    sa taille confortable habituelle, le contenu peut se chevaucher ou être
    partiellement coupé (comportement standard d'une fenêtre Qt redimen-
    sionnée sous sa taille naturelle), et retrouve son affichage normal dès
    qu'elle est réagrandie.
  - `_resolve_active_profile()` (appelée dans `__init__`, juste après la
    construction de `self.quota_tracker`) : attribue à cette instance le
    premier profil de `config.list_profiles()` (dans l'ordre
    d'enregistrement) dont `instance_lock.acquire_profile_lock()` réussit ET
    dont la clé API est réellement lisible via keyring, bascule aussitôt
    `quota_tracker.switch_context()` dessus (avec le modèle du profil, voir
    ci-dessus). Un profil dont le verrou est
    acquis mais dont la clé n'est pas lisible (Gestionnaire d'identification
    Windows indisponible pour cette entrée précise) libère aussitôt son
    verrou et cède la place au profil suivant plutôt que de rester
    "actif" sans clé utilisable (corrigé à l'audit du 2026-07-22 : le garder
    ainsi bloquait ce profil pour toute autre instance sans qu'aucune
    génération ne soit possible avec lui depuis celle-ci). Si des profils
    existent mais sont tous verrouillés par d'autres instances ou sans clé
    lisible, `self.active_profile` reste `None` sans qu'aucun avertissement ne
    s'affiche au démarrage (un `QMessageBox` ouvert dans `__init__()`, donc
    avant `window.show()` dans `main.py`, apparaissait avant même la fenêtre
    principale - déroutant, corrigé le 2026-07-22) : l'utilisateur en est
    informé seulement s'il en a réellement besoin, via `ProfilesDialog`
    (ouvert par `_ensure_api_key()` au clic sur "Résumer"), qui affiche déjà
    quels profils sont occupés (`locked_suffix`). `closeEvent()` libère le
    verrou du profil actif (`instance_lock.release_profile_lock()`) à la
    fermeture propre de la fenêtre. L'appel `_ensure_api_key(prompt_if_missing=False)`
    fait auparavant dans `__init__()` quand `self.active_profile` restait
    `None` a été retiré (audit du 2026-07-22) : sans effet dans ce cas précis
    (la fonction retourne immédiatement `None` sans aucun effet de bord),
    c'était un appel mort.
  - `_on_finished_ok()` (connectée à `SummarizeWorker.finished_ok`) joue un
    petit son via `winsound.PlaySound(str(config.get_success_sound_path()),
    winsound.SND_FILENAME | winsound.SND_ASYNC)` juste après l'affichage de la
    fiche (`_display_book_report()`), pour signaler la fin de génération sans
    devoir garder l'oeil sur l'application (ajouté le 2026-07-22). `SND_ASYNC`
    pour ne pas bloquer l'UI le temps de la lecture.
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
  - `_on_new_instance_clicked()` (bouton dans le header, à droite immédiate
    du titre "Distillat" - ajouté le 2026-07-22) : lance une nouvelle
    instance de Distillat via `subprocess.Popen()`, après vérification de
    `instance_lock.count_alive_instances() < instance_lock.MAX_INSTANCES`
    (message dédié si la limite est atteinte, sans lancer de processus).
    Résolution du chemin selon le mode (`getattr(sys, "frozen", False)`,
    même marqueur que partout ailleurs dans `app/config.py`) : en mode
    compilé, `sys.executable` pointe déjà sur `Distillat.exe`
    (`subprocess.Popen([sys.executable])`) ; en développement, il pointe sur
    l'interpréteur Python du venv, à qui il faut passer le chemin de
    `main.py` en argument (`config.get_app_dir() / "main.py"`, qui résout
    déjà correctement la racine du projet en dev). Ne précise jamais `cwd=` :
    `Popen` hérite du répertoire de travail du parent, et aucune fonction de
    résolution de chemin du projet n'en dépend. Ne vérifie PAS au préalable
    qu'un profil de clé API sera disponible pour la nouvelle instance : celle-
    ci affichera elle-même l'avertissement déjà existant
    (`_resolve_active_profile()`) si aucun profil n'est libre, pour ne pas
    dupliquer cette logique ici - seul le nombre d'instances est vérifié en
    amont, puisque ce n'est pas une ressource par compte Google mais un
    simple plafond d'ergonomie.
  - Dialogues : `ProfilesDialog` (gestion des profils de clé API - ajout,
    renommage, modification de la clé, suppression, sélection du profil
    actif de cette instance via le bouton "Utiliser" ; remplace l'ancien
    `ApiKeyDialog` à clé unique depuis le support multi-instances du
    2026-07-22, voir `app/instance_lock.py` et le paragraphe clé API de
    `app/config.py` plus haut ; `ProfileEditDialog`, sous-dialogue
    nom+clé, factorise le champ clé avec bouton oeil via `_ApiKeyInputRow`).
    Utiliser/Modifier/Supprimer sont désactivés tant qu'aucun profil n'est
    sélectionné dans `list_widget` (`_on_selection_changed()`, basé sur
    `selectedItems()`, jamais `currentItem()`/`currentRowChanged` : bug
    corrigé le 2026-07-22, ces derniers restaient positionnés sur le premier
    profil même sans sélection utilisateur réelle - à l'ouverture parce que
    `QListWidget` sélectionne automatiquement la première ligne dès qu'un
    item y est ajouté, et ensuite parce qu'un clic dans une zone vide de
    la liste ne change pas `currentRow`/`currentItem` par défaut - si bien
    que les trois boutons restaient actifs et agissaient sur ce premier
    profil sans que rien ne soit visiblement sélectionné). `_reload_list()`
    force `clearSelection()` + `setCurrentRow(-1)` après chaque
    remplissage ; `list_widget` est une `_DeselectableListWidget`
    (`QListWidget` dont `mousePressEvent()` vide la sélection quand le clic
    ne tombe sur aucun item) pour permettre explicitement à l'utilisateur de
    revenir à "aucune sélection" après en avoir choisi une.
    Quatre garde-fous avant qu'Ajouter/Modifier/Supprimer/Utiliser n'écrive
    quoi que ce soit : (1) `config.find_profile_by_name()`/`find_profile_by_api_key()`
    refusent un nom ou une clé déjà pris par un autre profil (message nommant
    le profil déjà concerné) ; (2) `instance_lock.acquire_profile_lock()`
    refuse d'agir sur un profil actuellement verrouillé par une autre
    instance (sauf s'il s'agit du profil actif de cette instance-ci) - et le
    verrou ainsi pris est conservé pendant toute la durée du sous-dialogue
    d'édition ou de la confirmation de suppression, puis relâché dans un
    `finally` (audit du 2026-07-22 : le relâcher aussitôt après le test
    laissait une autre instance s'attribuer ce profil pendant que le
    dialogue restait ouvert, la validation écrasant ou supprimant alors la
    clé d'un profil devenu actif ailleurs) ; (3) le
    paramètre `generation_in_progress` (calculé dans `_on_edit_api_key()` via
    `self.worker is not None and self.worker.isRunning()`, transmis au
    constructeur) refuse de modifier/supprimer le profil actif de cette
    instance tant qu'une génération tourne avec lui ; (4) le même
    `generation_in_progress` refuse aussi de changer de profil actif (bouton
    "Utiliser") tant qu'une génération tourne (audit du 2026-07-22 : le
    `switch_context()` immédiat aurait crédité les requêtes restantes de la
    génération en cours au compteur du nouveau compte, et le verrou de
    l'ancien profil aurait été libéré alors que sa clé restait activement
    utilisée par le worker). Si la clé du profil choisi via "Utiliser" n'est
    pas lisible (Gestionnaire d'identification Windows indisponible),
    `active_profile` est quand même mis à jour vers ce profil (le choix de
    l'utilisateur est respecté) mais `quota_tracker.switch_context()` n'est
    pas appelé : le suivi de quota affiché reste sur son état actuel plutôt
    que d'être signalé comme basculé sans que le compteur affiché ne
    corresponde réellement au nouveau profil actif. Ajouter/Modifier/Supprimer écrivent la liste
    des profils via `config.add_profile()`/`rename_profile()`/
    `remove_profile()` (relecture et réécriture sous le verrou
    inter-processus de settings.json, voir `app/config.py` ci-dessus),
    jamais via un enchaînement `list_profiles()` + `save_profiles()` dans le
    dialogue. `QuotaLimitsDialog`, `PromptsDialog` (un
    onglet par clé de `default_prompt_templates()`, un bouton de
    réinitialisation par onglet, n'affecte que cet onglet et efface
    immédiatement la personnalisation correspondante sur disque via
    `prompts_store.reset_custom_prompt()`, voir plus haut - la police
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
    de `status_label`, ajouté le 2026-07-21). Tous ces dialogues utilisent un
    `QPushButton`/`QDialogButtonBox.addButton(tr(...), <Role>)` construit à la
    main pour chacun de leurs boutons (Fermer/OK/Annuler/Sauvegarder/Réinitialiser),
    traduit via `tr()`, plutôt que les rôles standard `QDialogButtonBox.Ok`/
    `.Cancel`/`.Close`/etc. : ces boutons standards Qt restent affichés en
    anglais même en français faute de `QTranslator` Qt installé pour cette
    locale (bug constaté le 2026-07-21 sur Fermer/OK, puis à nouveau le
    2026-07-22 sur `PromptsDialog.Cancel`, l'ancien `ApiKeyDialog.Ok/.Cancel` et
    `QuotaLimitsDialog.Ok/.Cancel` : voir règle 3 de `CLAUDE.md`, interdiction
    permanente des rôles standard `QDialogButtonBox` non traduits). Dans
    `PromptsDialog`, le bouton "Sauvegarder" (`AcceptRole`) reste désactivé
    tant qu'aucun onglet n'a un texte différent de son état initial (chargé à
    l'ouverture, personnalisé ou par défaut) ; chaque bouton "Réinitialiser ce
    prompt" reste désactivé tant que le texte affiché de son onglet égale
    déjà le prompt par défaut actuel - suivi via `textChanged` sur chaque
    `QTextEdit` (`_on_text_changed()`), et `_save_button`/les `QTextEdit` sont
    construits avant la boucle sur les onglets pour que
    `_on_text_changed()` (appelé dès la construction de chaque onglet) puisse
    déjà s'appuyer dessus. `status_label`
    affiche désormais un texte de repos (`main_window.idle_status`) plutôt que
    de rester vide en l'absence de traitement en cours (auparavant vide, ce
    qui isolait visuellement ce bouton `?` sans texte à côté). Le footer de
    `MainWindow` propose aussi, à droite du copyright, les liens « Code
    source » (`update_checker.repo_page_url()`), « Téléchargement »
    (`update_checker.releases_page_url()`, déjà utilisé par le bandeau de mise
    à jour), « Changelog » et, ajouté le 2026-07-22, « Page web »
    (`update_checker.project_site_url()`, vers la landing page du projet),
    ouverts via `webbrowser.open()`. Comme tout texte affiché,
    `project_site_link_button` est réappliqué dans `_retranslate_ui()` au
    changement de langue à chaud : un bouton ajouté au footer sans y être
    ajouté resterait figé dans la langue de sa création (bug corrigé le
    2026-07-22 lors de l'ajout de ce lien, avant d'être remarqué par
    l'utilisateur).
    `PendingResumesDialog` (appelé par `_offer_pending_resumes()`, invoqué
    depuis `main.py` juste après `window.show()` - et non depuis
    `MainWindow.__init__()` - pour que la fenêtre principale soit déjà visible
    avant l'apparition de ce dialogue modal, bug corrigé le 2026-07-21) liste
    dans un `QListWidget` tous les livres ayant un état de reprise en attente
    (`generation_resume.load_all_resume_states()`) ; une entrée dont le
    fichier livre n'existe plus (déplacé/supprimé) est affichée en rouge avec
    un suffixe traduit et son bouton "Reprendre la sélection" reste désactivé
    (seul "Supprimer" reste possible pour elle). Un `QTimer` d'une seconde
    (`_refresh_locked_states()`, ajouté le 2026-07-22) surveille en direct
    les verrous de livre (`instance_lock.is_book_locked_elsewhere()`, lecture
    seule) : une reprise démarrée entre-temps dans une autre instance est
    affichée en gris avec un suffixe "(en cours dans une autre fenêtre)" et
    ses boutons "Reprendre la sélection" ET "Supprimer" sont désactivés,
    l'inverse (verrou libéré, instance qui a planté) la rendant à nouveau
    sélectionnable sans rouvrir le dialogue ; `_on_resume_clicked()`/
    `_on_delete_clicked()` revérifient de plus le verrou de façon synchrone
    au clic (le timer peut ne pas avoir encore relevé un verrou tout juste
    posé). Ce marquage n'est qu'une aide visuelle : la vraie protection est
    le verrou pris dans `SummarizeWorker.run()` (voir `app/instance_lock.py`),
    qui couvre aussi le chemin sans dialogue (livre redéposé à la main puis
    "Résumer") et la fenêtre de temps entre la sélection ici et le clic sur
    "Résumer". Le focus par défaut est
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
  - `cover_label` (onglet Couverture) a un menu contextuel (clic droit,
    `_on_cover_context_menu()`) proposant "Définir la couverture..."
    (`_on_set_cover_manually()`), qu'une couverture ait déjà été trouvée
    automatiquement ou non : ouvre un sélecteur de fichier (dossier initial
    mémorisé séparément, voir `config.load_last_cover_dir()` ci-dessus), passe
    l'image choisie par `cover_image.shrink_cover_image()` (même traitement
    que les couvertures extraites automatiquement) et remplace
    `last_result.cover_image`, marque `_report_dirty = True`. Ajouté le
    2026-07-22 pour les livres dont l'extraction automatique échoue
    (couverture non taguée proprement dans le fichier source, voir
    `epub_parser.py` ci-dessus) sans devoir régénérer toute la fiche.
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

## Page web du projet (`index.html`)

Landing page du projet, hébergée via GitHub Pages à
https://bruno-aublet.github.io/Distillat/ (lien "Page web" du footer de
l'application, voir `update_checker.project_site_url()` ci-dessus). Fichier
généré par l'outil de publication d'artefact ("Claude Design"), jamais écrit
ni édité à la main directement : toute modification de contenu (textes,
mise en page, images) doit repasser par une nouvelle génération/publication
de cet outil, puis un remplacement complet du fichier dans le projet.

**Piège découvert le 2026-07-22, à connaître avant toute modification
ponctuelle de ce fichier (ex : ajout d'une favicon) :** `index.html` n'est
pas une page HTML statique classique. C'est un bundler auto-extractible :
- Le `<head>` visible en clair au tout début du fichier (avant le premier
  `<script>`) n'est qu'une coquille de chargement temporaire, affichée le
  temps que le JavaScript s'exécute. Toute balise ajoutée uniquement là
  (ex. `<link rel="icon">`) est silencieusement perdue - elle n'a aucun
  effet sur la page finale, sans erreur ni avertissement visible.
- Au chargement, ce script parse le contenu JSON d'un
  `<script type="__bundler/template">` (chaîne échappée, à décoder avec
  `json.loads()` pour l'inspecter/modifier), résout les URLs de ressources
  (polices, images) en blobs, puis remplace tout
  `document.documentElement` par le DOM reconstruit depuis ce template
  (`document.documentElement.replaceWith(...)`). C'est ce `<head>`-là,
  encodé dans ce JSON, qui est réellement appliqué au document final - pas
  celui visible en clair au début du fichier.
- Autre piège rencontré dans une génération ultérieure du même outil : une
  balise pourtant présente dans le bon JSON du template peut malgré tout
  rester sans effet si elle est placée dans le `<body>` (ex. dans un
  élément `<helmet>`/`<x-dc>`, artefact probable d'un renderer React type
  `react-helmet`) plutôt que dans le vrai `<head>` du template : rien dans
  ce fichier ne migre le contenu d'un tel `<helmet>` vers le `<head>`, cette
  balise reste donc totalement inerte pour le navigateur.
- Pour vérifier ou corriger une balise du `<head>` réellement actif :
  extraire et parser le JSON de `<script type="__bundler/template">`
  (`json.loads()` sur son contenu), chercher `</head>` dans le texte décodé,
  et s'assurer que la balise voulue est bien juste avant, dans le `<head>`
  du template (jamais dans le head visible en clair du début de fichier, ni
  dans le `<body>`).
