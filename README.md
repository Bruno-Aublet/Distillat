**Français** | [English](#english)

# Distillat

**Le tome 8 de votre saga préférée vient de sortir, et vous mourez d'envie de
le lire. Sauf que le tome 7, c'était il y a 4 ans, et vos souvenirs sont un
peu flous : qui étaient déjà ces personnages secondaires ? Et il se passait
quoi, à la fin ?**

Si cette situation vous parle, Distillat est fait pour vous. Déposez
simplement votre fichier EPUB ou PDF dans la zone prévue à cet effet : vous
obtenez un résumé, des fiches de personnages et une analyse de l'oeuvre,
prêts à consulter ou à exporter en PDF.

L'application s'appuie sur le palier gratuit de Gemini 3.5 Flash.

**Téléchargement** : dernière version sur la page
[Releases](https://github.com/Bruno-Aublet/Distillat/releases) (le fichier
`.zip` à télécharger se trouve tout en bas de la page de la release).

**Version 1.2.3**

Application Windows avec interface PyQt5 pour générer une fiche de lecture
complète (résumés, personnages, analyse) à partir d'un livre EPUB ou PDF, via
l'API Gemini (gratuite). L'interface et la langue des fiches générées sont
disponibles en français et en anglais (voir
[Langue de l'interface et des fiches](#langue-de-linterface-et-des-fiches)).

Le numéro de version s'affiche dans le titre de la fenêtre et dans les
propriétés Windows de l'exécutable (clic droit sur `Distillat.exe` >
Propriétés > Détails). Source unique de vérité : `app/__version__.py`
(synchronisé manuellement avec `version_info.txt` pour les métadonnées .exe).

À chaque lancement, l'application vérifie silencieusement en arrière-plan si
une version plus récente est disponible sur la page
[Releases](https://github.com/Bruno-Aublet/Distillat/releases) : en cas
d'erreur réseau ou si l'application est déjà à jour, rien ne s'affiche ; si
une mise à jour existe, un bandeau apparaît sous l'en-tête avec un lien direct
vers la page de téléchargement.

## Fiche d'exemple

Le dossier `Fiches/` contient un fichier nommé
`FICHE TEST - Les Terres Oubliées - LIVRE FICTIF.distillat.json`, à ouvrir via
**Charger une fiche…** (ou en le glissant-déposant dans l'application) pour
voir à quoi ressemble un résultat complet (couverture, résumé court, résumé
détaillé, personnages, analyse) sans avoir à traiter un vrai livre ni à
consommer de quota Gemini.

**⚠️ ATTENTION : « Les Terres Oubliées » est un livre entièrement FICTIF, qui
N'EXISTE PAS.** Le titre, l'autrice (« Camille Vasseur »), la couverture et
tout le contenu de cette fiche ont été inventés de toutes pièces uniquement
pour démontrer le rendu de l'application - il ne s'agit ni d'un vrai livre, ni
d'un vrai résumé, ni d'une vraie autrice. Ne cherchez pas ce livre en librairie
ou en ligne : ne le trouverez pas, il n'a jamais existé.

## Fonctionnement

1. Glissez-déposez un fichier `.epub`, `.pdf` **ou une fiche déjà générée**
   (`.distillat.json`) dans la zone prévue, ou cliquez pour parcourir (même
   choix). Déposer un EPUB/PDF prépare un nouveau résumé ; déposer une fiche
   l'ouvre directement, comme avec **Charger une fiche…**. Le format EPUB
   donne généralement un meilleur résultat pour un livre à résumer : le PDF
   n'a pas de structure de chapitres exploitable (découpage par blocs de
   pages arbitraires pour les livres volumineux). La couverture d'un PDF est
   obtenue en rendant sa première page en image, telle qu'elle s'affiche à
   l'écran.
2. Cliquez sur **Résumer**. La zone de dépôt est désactivée pendant toute la
   durée du traitement, pour éviter de sélectionner un autre fichier ou
   d'ouvrir une autre fiche par-dessus le résumé en cours.
3. L'application compte les tokens du texte extrait via l'API Gemini :
   - si le texte tient sous la limite de débit du palier gratuit (tokens par
     minute), les deux résumés, les personnages principaux et l'analyse
     littéraire sont générés en un seul appel ;
   - sinon (livre volumineux), le texte est découpé en lots de plusieurs
     chapitres consécutifs (table des matières de l'EPUB, ou blocs de pages
     pour un PDF ; le plus de chapitres possible par lot sans dépasser cette
     même limite de débit, pour limiter le nombre de requêtes), chaque lot
     est résumé en un appel, puis un dernier appel reçoit l'ensemble de ces
     résumés et produit en une seule fois les deux résumés finaux, les
     personnages principaux et l'analyse littéraire. Si un échec survient en
     cours de route (quota atteint, réponse Gemini illisible...), les lots
     déjà résumés avec succès sont conservés : redéposer le même fichier et
     cliquer de nouveau sur **Résumer** reprend directement exactement là où
     le traitement s'était arrêté, sans reformuler ce qui a déjà été obtenu.
     Un ou plusieurs livres peuvent rester ainsi en attente de reprise ; au
     démarrage de l'application, si c'est le cas, une fenêtre les liste tous
     et permet d'en charger un directement (bouton **Reprendre la
     sélection**), sans avoir à le retrouver et le redéposer soi-même - il
     suffit ensuite de cliquer sur **Résumer** pour reprendre sa génération.
     Le bouton **Supprimer** de cette fenêtre efface l'état de reprise d'un
     livre (pour repartir de zéro à la place) ; le fichier correspondant est
     envoyé à la corbeille Windows plutôt que supprimé sans recours.
4. Le résultat (toujours dans la langue actuellement choisie pour
   l'interface, quelle que soit la langue du livre - voir
   [Langue de l'interface et des fiches](#langue-de-linterface-et-des-fiches))
   s'affiche dans 5 onglets : **Couverture** (image, titre, auteur),
   **Résumé court** (deux à trois paragraphes maximum), **Résumé
   détaillé** (au moins 1500 mots, structuré par partie, bien davantage pour
   un roman long), **Personnages** (fiches des personnages principaux et des
   groupes ou organisations centraux à l'intrigue - faction, conseil, armée...
   - typiquement 3 à 20 entrées selon la richesse du roman) et **Analyse**
   littéraire (au moins 600 à 900 mots, structurée par thème, style et
   portée de l'œuvre).
   Ces cibles sont indiquées à Gemini, pas des garanties strictes. Les titres
   de section que Gemini structure en Markdown (`#`, `##`, `###`...) sont
   affichés mis en forme (gras, taille) sans les balises visibles. Le contenu
   de tous les onglets est directement éditable : cliquez dans un champ et
   modifiez le texte au clavier (résumés, analyse, nom et description de
   chaque personnage, et titre du livre comme nom de l'auteur sur l'onglet
   Couverture). Toute édition est reprise automatiquement lors d'une
   sauvegarde (fiche `.distillat.json` ou export `.pdf`), balises de titre
   comprises.
5. Cliquez sur **Sauvegarder en .pdf** pour exporter l'ensemble en document
   PDF mis en forme, ou sur **Sauvegarder la fiche…** pour l'enregistrer sous
   forme de fichier JSON autonome (`.distillat.json`) rechargeable plus tard
   via **Charger une fiche…** (voir
   [Emplacement des fichiers](#emplacement-des-fichiers) pour savoir où). Si la
   fiche affichée n'a pas été sauvegardée, l'application demande confirmation
   avant de la remplacer (nouveau fichier, nouveau résumé, fiche déposée ou
   chargée, fermeture de la fiche ou de l'application). Une confirmation est
   également demandée si l'application est fermée pendant qu'une génération
   est en cours (la fiche en cours de génération serait perdue).

La clé API Gemini est demandée au premier lancement et stockée de façon
chiffrée via le Gestionnaire d'identification Windows (voir
[Sécurité de la clé API](#sécurité-de-la-clé-api)).

Le bouton **Prompts** ouvre une fenêtre permettant de consulter et, si
souhaité, de modifier les prompts envoyés à Gemini (un par cas de figure
décrit ci-dessus), chacun réinitialisable indépendamment des autres. Ils
fonctionnent tels quels : les modifier reste possible mais à vos risques et
périls, comme le rappelle un avertissement dans la fenêtre. Un prompt
personnalisé est enregistré séparément pour chaque langue de l'interface :
le modifier en français n'affecte jamais sa version anglaise, et inversement.

## Langue de l'interface et des fiches

Un sélecteur dans l'en-tête permet de basculer l'interface entre français et
anglais, sans redémarrage. Au tout premier démarrage, la langue est déterminée
par la langue du système Windows : français si le système est en français,
anglais dans tous les autres cas (y compris pour une langue système ni
française ni anglaise). Ce choix peut ensuite être modifié à tout moment via
le sélecteur ; il persiste au redémarrage de l'application
(`%APPDATA%\Distillat\settings.json`, voir
[Emplacement des fichiers](#emplacement-des-fichiers)).

La langue choisie détermine aussi la langue dans laquelle Gemini rédige la
fiche (résumés, personnages, analyse) : un rappel discret apparaît sous le
sélecteur pour l'indiquer clairement. Une fiche déjà générée reste affichée
dans sa langue d'origine, indépendamment d'un changement ultérieur de la
langue de l'interface : ce n'est pas un bug, une fiche n'est jamais retraduite
automatiquement (ce qui nécessiterait un nouvel appel à l'API Gemini, donc du
quota supplémentaire).

## Suivi des quotas

Distillat utilise le modèle `gemini-3.5-flash`, dont le palier gratuit est
limité à :

| Limite | Valeur |
| --- | --- |
| Requêtes par minute (RPM) | 5 |
| Tokens par minute (TPM) | 250 000 |
| Requêtes par jour (RPD) | 20 |

Ces chiffres proviennent du dashboard AI Studio du compte utilisé pour
développer l'application (relevés le 18/07/2026) - Google ne les expose pas
via l'API, ils **varient d'un compte à l'autre et peuvent changer dans le
temps**. Vérifiez les vôtres sur
[aistudio.google.com/rate-limit](https://aistudio.google.com/rate-limit) et
ajustez-les si besoin via le bouton **Limites de quota** de l'application ;
elles sont alors enregistrées dans `%APPDATA%\Distillat\quota_limits.json`.

Avec seulement 20 requêtes par jour, le quota quotidien est le facteur le
plus limitant : chaque livre consomme au minimum 1 requête (génération),
davantage si le livre est volumineux et doit être découpé par chapitres -
comptez large. Ce quota quotidien se réinitialise à minuit heure du
Pacifique (Californie), soit en général en matinée en France (le décalage
exact varie légèrement selon les changements d'heure respectifs de la
France et de la Californie).

L'application affiche en temps réel une estimation de la consommation
(tokens entrée/sortie, requêtes et tokens par minute, requêtes par jour), avec
un avertissement dès 80 % d'une limite atteinte. En cas de quota effectivement
dépassé, la génération échoue immédiatement avec un message clair (aucune
nouvelle tentative automatique) ; il suffit de recliquer sur **Résumer** une
fois le quota libéré. Ce compteur tient compte de chaque requête envoyée à
Gemini, qu'elle réussisse ou échoue, pour rester fidèle au quota réellement
consommé ; le temps qu'une requête reçoive sa réponse (jusqu'à plusieurs
minutes pour un gros livre), un indicateur « (+1 en attente) » apparaît à
côté du compteur de requêtes du jour pour signaler qu'elle est bien partie.
Ce suivi est **local à l'application** : il ne reflète pas l'usage
réel si la même clé API est utilisée ailleurs en parallèle (un autre outil,
un test manuel via AI Studio...), auquel cas les compteurs affichés ne
seront plus fiables. Il est en revanche propre à chaque clé API : passer
d'une clé à une autre (bouton **Clé API**) affiche aussitôt le compteur de
cette clé, sans jamais le mélanger avec celui d'une autre.

Une explication simplifiée de ce fonctionnement, sans jargon technique, est
également accessible directement dans l'application via le bouton **?** situé
à côté du statut de génération.

## Installation (développement)

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

## Obtenir une clé API Gemini gratuite

Rendez-vous sur [Google AI Studio](https://aistudio.google.com/apikey) pour
générer une clé API gratuite. N'activez jamais la facturation sur ce compte
Google si vous souhaitez conserver le palier gratuit.

## Compilation en .exe (PyInstaller, mode one-dir)

```bash
python build.py
```

Ce script invoque PyInstaller avec `distillat.spec`. L'exécutable est généré
dans `dist/Distillat/Distillat.exe`, accompagné de ses dépendances dans le
même dossier (mode one-dir : démarrage plus rapide qu'un exécutable unique).
L'icône de l'exécutable et de toutes les fenêtres provient de
`icons/open-book_4681875.png` ; `icons/distillat.ico` (généré à partir de ce
PNG en plusieurs résolutions) est utilisé pour l'icône de `Distillat.exe`
elle-même - régénérez-le si le PNG source change.

Pour distribuer l'application, copiez l'intégralité du dossier
`dist/Distillat/`.

## Sécurité de la clé API

La clé API Gemini n'est **jamais stockée en clair sur disque**. Elle est
enregistrée via le module [keyring](https://pypi.org/project/keyring/), qui
délègue au Gestionnaire d'identification Windows (chiffrement DPAPI lié à
votre compte Windows sur cette machine) - la clé reste illisible si le 
dossier de l'application est copié ailleurs ou consulté par un autre compte utilisateur.

Cette protection a une limite inhérente à tout stockage local automatique :
elle ne protège pas contre un accès complet à votre session Windows ouverte
(l'application elle-même doit pouvoir relire la clé pour fonctionner sans
redemander de mot de passe à chaque lancement).

## Emplacement des fichiers

Une fois compilé, Distillat stocke ses données **indépendamment du dossier de
l'exécutable** (pour ne rien perdre si ce dossier est supprimé ou remplacé
lors d'une mise à jour) :

- **Clé API** : Gestionnaire d'identification Windows (voir
  [Sécurité de la clé API](#sécurité-de-la-clé-api)).
- **`.quota_state_<hash>.json`** (compteur de requêtes du jour, un fichier par clé API pour ne jamais mélanger deux comptes), **`quota_limits.json`**
  (limites RPM/TPM/RPD personnalisées, si modifiées via le bouton **Limites de
  quota**), **`settings.json`** (regroupe la langue de l'interface choisie, voir
  [Langue de l'interface et des fiches](#langue-de-linterface-et-des-fiches) ;
  les prompts personnalisés par langue, si modifiés via le bouton **Prompts** ;
  et les derniers dossiers utilisés pour une fiche et pour un export PDF, voir
  ci-dessous), **`.generation_resume_<hash>.json`** (un fichier par livre,
  lots de chapitres déjà résumés pour une génération interrompue par un
  échec ; absent en l'absence d'échec, supprimé dès que la génération de ce
  livre se termine avec succès) et **`debug_logs\`**
  (fichiers de diagnostic : réponses Gemini brutes sauvegardées
  automatiquement quand une réponse reste illisible même après tentative de
  réparation automatique (les 5 fichiers les plus récents sont conservés,
  les plus anciens étant supprimés automatiquement), et journal
  `api_requests.log` consignant chaque appel envoyé à Gemini - horodatage,
  type d'appel, tokens, durée, résultat, jamais le contenu du livre - pour
  pouvoir comparer la consommation réelle avec le dashboard Google AI Studio
  (ne conserve que les 5 dernières générations de fiche) :
  `%APPDATA%\Distillat\`.
- **Fiches sauvegardées** (`.distillat.json`) et **exports PDF** :
  `Documents\Distillat\Fiches\` au tout premier usage, puis le dernier dossier
  utilisé pour ce type de fichier (fiche ou PDF, mémorisés séparément) est
  proposé par défaut par **Sauvegarder la fiche…**, **Sauvegarder en .pdf** et
  **Charger une fiche…**, y compris après redémarrage de l'application (un
  autre emplacement peut toujours être choisi ; les sous-dossiers y sont sans
  problème). Si la fiche affichée a été chargée depuis un fichier, c'est son
  dossier d'origine qui est proposé en priorité, avec son nom de fichier ; la
  réenregistrer sur son fichier d'origine ne demande aucune confirmation de
  remplacement (un message confirme simplement la modification). La
  couverture est automatiquement redimensionnée et recompressée avant d'être
  stockée dans la fiche ou le document PDF, pour éviter qu'une image haute
  résolution alourdisse inutilement le fichier ; une fiche existante
  contenant encore une couverture surdimensionnée (créée par une version
  antérieure) est allégée automatiquement dès son prochain chargement.
- **`LICENSE`**, **`CHANGELOG.md`**, **icône de l'application**
  (`icons/open-book_4681875.png`) et **fichiers de traduction**
  (`locales/fr.json`, `locales/en.json`) : embarqués à la compilation (dans
  `_internal/`). Le `LICENSE` et le `CHANGELOG.md` sont accessibles depuis le
  footer de l'application (« Copyright ... - Licence GNU GPL v3 » à gauche ;
  liens « Code source », « Téléchargement » et « Changelog » à droite).

En développement (`python main.py`), tous ces emplacements sont identiques au
mode compilé, y compris les fichiers techniques (`.quota_state_<hash>.json`,
`quota_limits.json`, `settings.json`, `.generation_resume_<hash>.json`), désormais
toujours dans `%APPDATA%\Distillat\` quel que soit le mode de lancement (pour
que le suivi de quota reflète la même consommation réelle, peu importe la
façon de lancer l'application). Le
dossier `Fiches/` à la racine du projet ne sert qu'à héberger la fiche
d'exemple fournie avec le code.

Si vous mettez à jour depuis une version antérieure qui stockait ces fichiers
à côté de l'exécutable, l'application les déplace automatiquement vers les
nouveaux emplacements au premier lancement (sans jamais écraser un fichier
déjà présent à la destination). De même, si vous mettez à jour depuis une
version antérieure où `settings.json`, les prompts personnalisés et les
derniers dossiers utilisés étaient chacun dans leur propre fichier
(`last_dirs.json`, `prompts.json`), leur contenu est fusionné automatiquement
dans `settings.json` au premier lancement, sans perte de données.

## Licence

Ce logiciel est distribué sous licence [GNU GPL v3](LICENSE).

---

[Français](#distillat) | **English**

<a name="english"></a>
# Distillat

**Book 8 of your favorite saga just came out, and you're dying to read it.
Except book 7 was 4 years ago, and your memories are a bit fuzzy: who were
those secondary characters again? And what happened at the end?**

If this sounds familiar, Distillat is for you. Simply drop your EPUB or PDF
file into the designated area: you get a summary, character sheets, and an
analysis of the work, ready to view or export to PDF.

The application relies on Gemini 3.5 Flash's free tier.

**Download**: latest version on the
[Releases](https://github.com/Bruno-Aublet/Distillat/releases) page (the
`.zip` file to download is at the bottom of the release page).

**Version 1.2.3**

Windows application with a PyQt5 interface to generate a complete reading
report (summaries, characters, analysis) from an EPUB or PDF book, via the
Gemini API (free). The interface and the language of generated reports are
available in French and English (see
[Interface and report language](#interface-and-report-language)).

The version number is displayed in the window title and in the executable's
Windows properties (right-click `Distillat.exe` > Properties > Details).
Single source of truth: `app/__version__.py` (manually synchronized with
`version_info.txt` for the .exe metadata).

On each launch, the application silently checks in the background whether a
newer version is available on the
[Releases](https://github.com/Bruno-Aublet/Distillat/releases) page: on a
network error or if the application is already up to date, nothing is
shown; if an update exists, a banner appears under the header with a direct
link to the download page.

## Sample report

The `Fiches/` folder contains a file named
`FICHE TEST - Les Terres Oubliées - LIVRE FICTIF.distillat.json`, to open via
**Load a report…** (or by dragging and dropping it into the application) to
see what a complete result looks like (cover, short summary, detailed
summary, characters, analysis) without having to process a real book or
consume any Gemini quota.

**⚠️ WARNING: "Les Terres Oubliées" ("The Forgotten Lands") is an entirely
FICTIONAL book that DOES NOT EXIST.** The title, the author ("Camille
Vasseur"), the cover, and all the content of this report were made up purely
to demonstrate what the application produces - it is not a real book, a real
summary, or a real author. Don't look for this book in a bookstore or
online: you won't find it, it never existed.

## How it works

1. Drag and drop an `.epub`, `.pdf` file **or an already generated report**
   (`.distillat.json`) into the designated area, or click to browse (same
   choice). Dropping an EPUB/PDF prepares a new summary; dropping a report
   opens it directly, same as **Load a report…**. The EPUB format generally
   gives a better result for a book to summarize: PDF has no usable chapter
   structure (arbitrary page-block splitting for large books). A PDF's cover
   is obtained by rendering its first page as an image, exactly as it
   appears on screen.
2. Click **Summarize**. The drop zone is disabled for the entire duration of
   processing, to avoid selecting another file or opening another report on
   top of the summary currently in progress.
3. The application counts the tokens of the extracted text via the Gemini
   API:
   - if the text fits under the free tier's rate limit (tokens per minute),
     both summaries, the main characters, and the literary analysis are
     generated in a single call;
   - otherwise (large book), the text is split into batches of several
     consecutive chapters (EPUB table of contents, or page blocks for a
     PDF; as many chapters as possible per batch without exceeding this same
     rate limit, to limit the number of requests), each batch is summarized
     in one call, then a final call receives all these summaries and
     produces, in one go, both final summaries, the main characters, and the
     literary analysis. If a failure occurs along the way (quota reached,
     unreadable Gemini response...), batches already summarized
     successfully are kept: dropping the same file again and clicking
     **Summarize** again offers to resume exactly where processing had
     stopped, without redoing what was already obtained. One or more books
     can remain waiting to be resumed this way; on startup, if that is the
     case, a window lists all of them and lets you resume one directly,
     without having to find and drop the file again yourself.
4. The result (always in the language currently chosen for the interface,
   regardless of the book's language - see
   [Interface and report language](#interface-and-report-language)) is
   displayed in 5 tabs: **Cover** (image, title, author), **Short
   summary** (two to three paragraphs maximum), **Detailed
   summary** (at least 1500 words, structured by part, considerably more for
   a long novel), **Characters** (sheets for the main characters and for
   groups or organizations central to the plot - faction, council, army...
   - typically 3 to 20 entries depending on how rich the novel is), and
   **Literary analysis** (at least 600 to 900 words, structured by theme,
   style, and the work's significance).
   These are targets given to Gemini, not strict guarantees. Section
   headings that Gemini structures in Markdown (`#`, `##`, `###`...) are
   displayed formatted (bold, size) without the visible tags. The content of
   every tab is directly editable: click into a field and edit the text with
   the keyboard (summaries, analysis, name and description of each
   character, and the book's title as well as the author's name on the
   Cover tab). Any edit is automatically picked up when saving (`.distillat.json`
   report or `.pdf` export), heading tags included.
5. Click **Export to .pdf** to export everything as a formatted PDF
   document, or **Save the report…** to save it as a standalone JSON file
   (`.distillat.json`) reloadable later via **Load a report…** (see
   [File locations](#file-locations) to know where). If the displayed
   report hasn't been saved, the application asks for confirmation before
   replacing it (new file, new summary, dropped or loaded report, closing
   the report or the application). Confirmation is also asked if the
   application is closed while a generation is in progress (the report
   being generated would be lost).

The Gemini API key is requested on first launch and stored encrypted via the
Windows Credential Manager (see
[API key security](#api-key-security)).

The **Prompts** button opens a window to view and, if desired, edit the
prompts sent to Gemini (one per case described above), each independently
resettable. They work as-is: editing them is possible but at your own risk,
as a warning in the window reminds you. A custom prompt is stored separately
for each interface language: editing it in French never affects its English
version, and vice versa.

## Interface and report language

A selector in the header lets you switch the interface between French and
English, without restarting. On the very first launch, the language is
determined by the Windows system language: French if the system is in
French, English in all other cases (including for a system language that is
neither French nor English). This choice can then be changed at any time via
the selector; it persists across application restarts
(`%APPDATA%\Distillat\settings.json`, see
[File locations](#file-locations)).

The chosen language also determines the language in which Gemini writes the
report (summaries, characters, analysis): a discreet reminder appears under
the selector to make this clear. An already generated report keeps
displaying in its original language, regardless of a later change to the
interface language: this is not a bug, a report is never automatically
retranslated (which would require a new call to the Gemini API, and
therefore additional quota).

## Quota tracking

Distillat uses the `gemini-3.5-flash` model, whose free tier is limited to:

| Limit | Value |
| --- | --- |
| Requests per minute (RPM) | 5 |
| Tokens per minute (TPM) | 250,000 |
| Requests per day (RPD) | 20 |

These figures come from the AI Studio dashboard of the account used to
develop the application (recorded on 2026-07-18) - Google does not expose
them via the API, they **vary from one account to another and may change
over time**. Check your own at
[aistudio.google.com/rate-limit](https://aistudio.google.com/rate-limit) and
adjust them if needed via the **Quota limits** button in the application;
they are then stored in `%APPDATA%\Distillat\quota_limits.json`.

With only 20 requests per day, the daily quota is the most limiting factor:
each book consumes at least 1 request (generation), more if the book is
large and needs to be split by chapters - plan generously. This daily quota
resets at midnight Pacific Time (California), not at midnight in your own
local time zone.

The application shows a real-time estimate of consumption (input/output
tokens, requests and tokens per minute, requests per day), with a warning as
soon as 80% of a limit is reached. If a quota is actually exceeded,
generation fails immediately with a clear message (no automatic retry);
simply click **Summarize** again once the quota is freed. This counter
accounts for every request sent to Gemini, whether it succeeds or fails, to
stay true to the quota actually consumed; while a request is awaiting its
response (up to several minutes for a large book), a "(+1 pending)"
indicator appears next to the daily request counter to show it was indeed
sent. This tracking is **local to the application**: it does not reflect
actual usage if the same API key is used elsewhere in parallel (another
tool, a manual test via AI Studio...), in which case the displayed counters
will no longer be accurate. It is however specific to each API key:
switching from one key to another (**API key** button) immediately shows
that key's own counter, never mixed with another one's.

A simplified, jargon-free explanation of how this works is also available
directly in the application via the **?** button next to the generation
status.

## Installation (development)

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python main.py
```

## Getting a free Gemini API key

Go to [Google AI Studio](https://aistudio.google.com/apikey) to generate a
free API key. Never enable billing on this Google account if you want to
keep the free tier.

## Building the .exe (PyInstaller, one-dir mode)

```bash
python build.py
```

This script invokes PyInstaller with `distillat.spec`. The executable is
generated in `dist/Distillat/Distillat.exe`, along with its dependencies in
the same folder (one-dir mode: faster startup than a single-file
executable). The icon of the executable and of all windows comes from
`icons/open-book_4681875.png`; `icons/distillat.ico` (generated from this
PNG at several resolutions) is used for `Distillat.exe`'s own icon -
regenerate it if the source PNG changes.

To distribute the application, copy the entire `dist/Distillat/` folder.

## API key security

The Gemini API key is **never stored in plain text on disk**. It is saved
via the [keyring](https://pypi.org/project/keyring/) module, which delegates
to the Windows Credential Manager (DPAPI encryption tied to your Windows
account on this machine) - the key remains unreadable if the application
folder is copied elsewhere or accessed by another user account.

This protection has a limitation inherent to any automatic local storage: it
does not protect against full access to your open Windows session (the
application itself must be able to read the key back to work without asking
for a password on every launch).

## File locations

Once compiled, Distillat stores its data **independently of the executable's
folder** (so nothing is lost if that folder is deleted or replaced during an
update):

- **API key**: Windows Credential Manager (see
  [API key security](#api-key-security)).
- **`.quota_state_<hash>.json`** (today's request counter, one file per API key so two accounts are never mixed), **`quota_limits.json`**
  (custom RPM/TPM/RPD limits, if changed via the **Quota limits** button),
  **`settings.json`** (groups together the chosen interface language, see
  [Interface and report language](#interface-and-report-language); custom
  prompts per language, if changed via the **Prompts** button; and the last
  folders used for a report and for a PDF export, see below),
  **`.generation_resume_<hash>.json`** (one file per book, chapter batches
  already summarized for a generation interrupted by a failure; absent if
  there was no failure, removed as soon as that book's generation finishes
  successfully) and
  **`debug_logs\`** (diagnostic files: raw Gemini responses saved
  automatically when a response stays unreadable even after an automatic
  repair attempt (only the 5 most recent files are kept, older ones being
  deleted automatically), and an `api_requests.log` journal recording every
  call sent to Gemini - timestamp, call type, tokens, duration, outcome,
  never the book content itself - so actual consumption can be compared with
  the Google AI Studio dashboard (keeps only the last 5 report generations)):
  `%APPDATA%\Distillat\`.
- **Saved reports** (`.distillat.json`) and **PDF exports**:
  `Documents\Distillat\Fiches\` on first use, then the last folder used for
  that type of file (report or PDF, remembered separately) is offered by
  default by **Save the report…**, **Export to .pdf**, and **Load a
  report…**, including after restarting the application (another location
  can always be chosen; subfolders work fine there too). If the displayed
  report was loaded from a file, its original folder is offered first,
  along with its file name; saving it back to its original file doesn't ask
  for overwrite confirmation (a message simply confirms the update was
  saved). The cover is automatically resized and recompressed before being
  stored in the report or the PDF document, to avoid a high-resolution image
  needlessly bloating the file; an existing report still containing an
  oversized cover (created by an earlier version) is lightened automatically
  the next time it is loaded.
- **`LICENSE`**, **`CHANGELOG.md`**, **application icon**
  (`icons/open-book_4681875.png`), and **translation files**
  (`locales/fr.json`, `locales/en.json`): bundled at compile time (in
  `_internal/`). The `LICENSE` and `CHANGELOG.md` are accessible from the
  application's footer ("Copyright ... - GNU GPL v3 license" on the left;
  "Source code", "Download", and "Changelog" links on the right).

In development (`python main.py`), all these locations are identical to the
compiled mode, including the technical files (`.quota_state_<hash>.json`,
`quota_limits.json`, `settings.json`, `.generation_resume_<hash>.json`), now always
in `%APPDATA%\Distillat\` regardless of the launch mode (so that quota
tracking reflects the same actual consumption, no matter how the application
is launched). The `Fiches/` folder at the project root only hosts the
sample report shipped with the code.

If you're updating from an earlier version that stored these files next to
the executable, the application automatically moves them to the new
locations on first launch (never overwriting a file already present at the
destination). Likewise, if you're updating from an earlier version where
`settings.json`, custom prompts, and the last folders used were each in
their own file (`last_dirs.json`, `prompts.json`), their content is
automatically merged into `settings.json` on first launch, without any data
loss.

## License

This software is distributed under the [GNU GPL v3](LICENSE) license.
