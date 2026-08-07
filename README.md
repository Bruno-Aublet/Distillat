**Français** | [English](#english)

# Distillat

**Le tome 8 de votre saga préférée vient de sortir, et vous mourez d'envie de
le lire. Sauf que le tome 7, c'était il y a 4 ans, et vos souvenirs sont un
peu flous : qui étaient ces personnages secondaires ? Et il se passait
quoi, à la fin ?**

Si cette situation vous parle, Distillat est fait pour vous. Déposez
simplement votre fichier EPUB ou PDF dans la zone prévue à cet effet : vous
obtenez un résumé, des fiches de personnages et une analyse de l'oeuvre,
prêts à consulter ou à exporter en PDF.

L'application s'appuie sur le palier gratuit de Gemini (Flash) (voir
[Suivi des quotas](#suivi-des-quotas)).

**Confidentialité** : le texte du livre est envoyé à l'API Gemini pour générer
la fiche, et Google peut utiliser les contenus soumis via le palier gratuit
pour améliorer ses services - évitez d'y soumettre des documents sensibles ou
confidentiels.

**Téléchargement** : dernière version sur la page
[Releases](https://github.com/Bruno-Aublet/Distillat/releases) (le fichier
`.zip` à télécharger se trouve tout en bas de la page de la release).

**Version 1.4.2**

Application Windows avec interface PyQt5 pour générer une fiche de lecture
complète (résumés, personnages, analyse) à partir d'un livre EPUB ou PDF, via
l'API Gemini (gratuite). L'interface et la langue des fiches générées sont
disponibles en français et en anglais (voir
[Langue de l'interface et des fiches](#langue-de-linterface-et-des-fiches)).

À chaque lancement, l'application vérifie silencieusement en arrière-plan si
une version plus récente est disponible sur la page
[Releases](https://github.com/Bruno-Aublet/Distillat/releases) : en cas
d'erreur réseau ou si l'application est déjà à jour, rien ne s'affiche ; si
une mise à jour existe, un bandeau apparaît sous l'en-tête avec un lien direct
vers la page de téléchargement.

## Fiche d'exemple

Deux fiches d'exemple, `FICHE TEST - Les Terres Oubliées - LIVRE FICTIF.dstl`
et sa version anglaise `TEST SHEET - The Forgotten Lands - FICTITIOUS BOOK
(EN).dstl`, sont copiées automatiquement au premier démarrage dans
le dossier normal des fiches (`Documents\Distillat\Fiches`) : ouvrez-les via
**Charger une fiche…** (ou en les glissant-déposant dans l'application) pour
voir à quoi ressemble un résultat complet (couverture, résumé court, résumé
détaillé, personnages, analyse) sans avoir à traiter un vrai livre ni à
consommer de quota Gemini.

**⚠️ ATTENTION : « Les Terres Oubliées » est un livre entièrement FICTIF, qui
N'EXISTE PAS.** Le titre, l'autrice (« Camille Vasseur »), la couverture et
tout le contenu de cette fiche ont été inventés de toutes pièces uniquement
pour démontrer le rendu de l'application - il ne s'agit ni d'un vrai livre, ni
d'un vrai résumé, ni d'une vraie autrice. Ne cherchez pas ce livre en librairie
ou en ligne : ne le trouverez pas, il n'a jamais existé.

Le contenu narratif de cette fiche (intrigue, personnages, thèmes) a été
généré par IA : attendez-vous à des poncifs et clichés à la tonne, l'objectif
étant de démontrer la mise en forme de l'application, pas d'atteindre la
qualité littéraire d'une intrigue originale.

## Fonctionnement

1. Glissez-déposez un fichier `.epub`, `.pdf` **ou une fiche déjà générée**
   (`.dstl`, ou l'ancien format `.distillat.json` encore reconnu) dans la
   zone prévue, ou cliquez pour parcourir (même choix). Déposer un EPUB/PDF
   prépare un nouveau résumé ; déposer une fiche
   l'ouvre directement, comme avec **Charger une fiche…**. Le format EPUB
   donne généralement un meilleur résultat pour un livre à résumer : le PDF
   n'a pas de structure de chapitres exploitable.
2. Cliquez sur **Résumer**. Selon la taille du livre, la génération se fait
   en un seul appel à l'API Gemini ou, pour un livre volumineux, en
   plusieurs appels successifs (résumé par lots de chapitres puis fusion
   finale). Si un échec survient en cours de route (quota atteint, réponse
   Gemini illisible...), le travail déjà accompli est conservé : redéposer
   le même fichier et recliquer sur **Résumer** reprend directement là où le
   traitement s'était arrêté. Un ou plusieurs livres peuvent ainsi rester en
   attente de reprise ; le bouton **Reprises en attente** à côté de
   **Résumer** permet de les retrouver et d'en reprendre un à tout moment.
   Si plusieurs fenêtres de Distillat sont ouvertes en parallèle, un même
   livre ne peut être généré ou repris que dans une seule fenêtre à la fois.
3. Le résultat (toujours dans la langue actuellement choisie pour
   l'interface, quelle que soit la langue du livre - voir
   [Langue de l'interface et des fiches](#langue-de-linterface-et-des-fiches))
   s'affiche dans 5 onglets : **Couverture** (image, titre, auteur),
   **Résumé court**, **Résumé détaillé** (structuré par partie),
   **Personnages** (fiches des personnages principaux et des groupes ou
   organisations centraux à l'intrigue) et **Analyse** littéraire
   (structurée par thème, style et portée de l'œuvre). Le contenu de tous
   les onglets est directement éditable : cliquez dans un champ et modifiez
   le texte au clavier. Toute édition est reprise automatiquement lors d'une
   sauvegarde (fiche `.dstl` ou export `.pdf`). Un clic droit sur l'image de
   l'onglet Couverture permet de choisir manuellement une image sur le
   disque pour la définir ou la remplacer, sans avoir à régénérer la fiche.
4. Cliquez sur **Exporter en .pdf** pour exporter l'ensemble en document
   PDF mis en forme, ou sur **Sauvegarder la fiche…** pour l'enregistrer sous
   forme de fichier JSON autonome (`.dstl`) rechargeable plus tard via
   **Charger une fiche…**, en la glissant-déposant dans l'application, ou en
   double-cliquant dessus directement dans l'Explorateur Windows. Si la
   fiche affichée n'a pas été sauvegardée, l'application demande confirmation
   avant de la remplacer ou de se fermer.

La clé API Gemini est demandée au premier lancement et stockée de façon
chiffrée via le Gestionnaire d'identification Windows (voir
[Sécurité de la clé API](#sécurité-de-la-clé-api)). Plusieurs profils de clé
API (un nom + une clé chacun) peuvent être enregistrés via le bouton
**Profils**, pour lancer plusieurs instances de Distillat en parallèle sur
des comptes différents : chaque instance se voit attribuer automatiquement,
à son démarrage, le premier profil non déjà utilisé par une autre instance
ouverte sur le même ordinateur. Le modèle Gemini utilisé, les prompts
personnalisés et les limites de quota (voir
[Suivi des quotas](#suivi-des-quotas)) sont propres à chaque profil. Un
bouton dédié, à droite du titre en haut de la fenêtre,
permet d'ouvrir directement une nouvelle instance de Distillat (4 instances
simultanées maximum sur le même ordinateur).

Le bouton **Prompts** ouvre une fenêtre permettant de consulter et de
modifier les prompts envoyés à Gemini, chacun réinitialisable
indépendamment des autres. Ils fonctionnent tels quels : les modifier reste
possible mais à vos risques et périls, comme le rappelle un avertissement
dans la fenêtre. Un prompt personnalisé est enregistré séparément pour
chaque langue de l'interface.

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

Chaque profil de clé API permet de choisir le modèle Gemini utilisé parmi une
liste (`gemini-3.5-flash` ou `gemini-3.6-flash` à ce jour), via le bouton
**Profils**. Le palier gratuit de ces deux modèles est actuellement identique :

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
ajustez-les si besoin via le bouton **Limites de quota** de l'application.
Chaque profil et chaque modèle a ses propres compteurs et limites, sans
jamais mélanger leur consommation.

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
d'un profil à un autre (bouton **Profils**) affiche aussitôt le compteur de
la clé de ce profil, sans jamais le mélanger avec celui d'un autre.

Une explication simplifiée de ce fonctionnement est
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
- **Fichiers techniques** (compteurs et limites de quota, réglages,
  profils, prompts personnalisés, état de reprise, journaux de diagnostic) :
  `%APPDATA%\Distillat\`. Le détail de chaque fichier est documenté dans
  `ARCHITECTURE.md` pour qui souhaite l'implémentation exacte.
- **Fiches sauvegardées** (`.dstl`) et **exports PDF** : `Documents\Distillat\Fiches\`
  au tout premier usage, puis le dernier dossier utilisé pour ce type de
  fichier (fiche ou PDF, mémorisés séparément) est proposé par défaut par
  **Sauvegarder la fiche…**, **Sauvegarder en .pdf** et **Charger une
  fiche…**, y compris après redémarrage de l'application (un autre
  emplacement peut toujours être choisi). Si la fiche affichée a été chargée
  depuis un fichier, c'est son dossier d'origine qui est proposé en priorité.
- **`LICENSE`**, **`CHANGELOG.md`**, icône, son et fichiers de traduction :
  embarqués à la compilation. Le `LICENSE` et le `CHANGELOG.md` sont
  accessibles depuis le footer de l'application (liens « Code source »,
  « Téléchargement », « Changelog » et « Page web »).

En développement (`python main.py`), tous ces emplacements sont identiques au
mode compilé (`%APPDATA%\Distillat\`), pour que le suivi de quota reflète la
même consommation réelle quelle que soit la façon de lancer l'application.

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

The application relies on Gemini's free tier (Flash) (see
[Quota tracking](#quota-tracking)).

**Privacy**: the book's text is sent to the Gemini API to generate the
report, and Google may use content submitted through the free tier to improve
its services - avoid submitting sensitive or confidential documents.

**Download**: latest version on the
[Releases](https://github.com/Bruno-Aublet/Distillat/releases) page (the
`.zip` file to download is at the bottom of the release page).

**Version 1.4.2**

Windows application with a PyQt5 interface to generate a complete reading
report (summaries, characters, analysis) from an EPUB or PDF book, via the
Gemini API (free). The interface and the language of generated reports are
available in French and English (see
[Interface and report language](#interface-and-report-language)).

On each launch, the application silently checks in the background whether a
newer version is available on the
[Releases](https://github.com/Bruno-Aublet/Distillat/releases) page: on a
network error or if the application is already up to date, nothing is
shown; if an update exists, a banner appears under the header with a direct
link to the download page.

## Sample report

Two sample reports, `FICHE TEST - Les Terres Oubliées - LIVRE FICTIF.dstl`
and its English version `TEST SHEET - The Forgotten Lands - FICTITIOUS BOOK
(EN).dstl`, are automatically copied on first launch into the
normal reports folder (`Documents\Distillat\Fiches`): open them via
**Load a report…** (or by dragging and dropping them into the application) to
see what a complete result looks like (cover, short summary, detailed
summary, characters, analysis) without having to process a real book or
consume any Gemini quota.

**⚠️ WARNING: "Les Terres Oubliées" ("The Forgotten Lands") is an entirely
FICTIONAL book that DOES NOT EXIST.** The title, the author ("Camille
Vasseur"), the cover, and all the content of this report were made up purely
to demonstrate what the application produces - it is not a real book, a real
summary, or a real author. Don't look for this book in a bookstore or
online: you won't find it, it never existed.

This report's narrative content (plot, characters, themes) was itself
generated by AI: expect a truckload of tropes and clichés, the goal being to
demonstrate the application's formatting, not to achieve the literary
quality of an original plot.

## How it works

1. Drag and drop an `.epub`, `.pdf` file **or an already generated report**
   (`.dstl`, or the old `.distillat.json` format, still recognized) into the
   designated area, or click to browse (same choice). Dropping an EPUB/PDF
   prepares a new summary; dropping a report opens it directly, same as
   **Load a report…**. The EPUB format generally gives a better result for a
   book to summarize: PDF has no usable chapter structure.
2. Click **Summarize**. Depending on the book's size, generation happens in
   a single call to the Gemini API or, for a large book, in several
   successive calls (chapter-batch summaries followed by a final merge). If
   a failure occurs along the way (quota reached, unreadable Gemini
   response...), the work already done is kept: dropping the same file
   again and clicking **Summarize** again resumes exactly where processing
   had stopped. One or more books can remain waiting to be resumed this
   way; the **Pending resumes** button next to **Summarize** lets you find
   and resume one at any time. If several Distillat windows are open in
   parallel, a given book can only be generated or resumed in one window at
   a time.
3. The result (always in the language currently chosen for
   the interface, regardless of the book's language - see
   [Interface and report language](#interface-and-report-language)) is
   displayed in 5 tabs: **Cover** (image, title, author), **Short
   summary**, **Detailed summary** (structured by part), **Characters**
   (sheets for the main characters and for groups or organizations central
   to the plot), and **Literary analysis** (structured by theme, style, and
   the work's significance). The content of every tab is directly editable:
   click into a field and edit the text with the keyboard. Any edit is
   automatically picked up when saving (`.dstl` report or `.pdf` export).
   Right-clicking the image on the Cover tab lets you manually pick an
   image from disk to set or replace the cover, without having to
   regenerate the report.
4. Click **Export to .pdf** to export everything as a formatted PDF
   document, or **Save the report…** to save it as a standalone JSON file
   (`.dstl`) reloadable later via **Load a report…**, by dragging and
   dropping it into the application, or by double-clicking it directly in
   Windows Explorer. If the displayed report hasn't been saved, the
   application asks for confirmation before replacing it or closing.

The Gemini API key is requested on first launch and stored encrypted via the
Windows Credential Manager (see
[API key security](#api-key-security)). Several API key profiles (a name
plus a key each) can be registered via the **Profiles** button, to run
several Distillat instances in parallel with different accounts: each
instance is automatically assigned, on startup, the first profile not
already in use by another instance open on the same computer. The Gemini
model used, custom prompts, and quota limits (see
[Quota tracking](#quota-tracking)) are specific to each profile. A
dedicated button, to the right of the title at the top of the window, opens
a new Distillat instance directly (up to 4 simultaneous instances on the
same computer).

The **Prompts** button opens a window to view and edit the
prompts sent to Gemini, each independently resettable. They work as-is:
editing them is possible but at your own risk, as a warning in the window
reminds you. A custom prompt is stored separately for each interface
language.

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

Each API key profile lets you choose the Gemini model used from a list
(`gemini-3.5-flash` or `gemini-3.6-flash` as of now), via the **Profiles**
button. The free tier of both models is currently identical:

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
adjust them if needed via the **Quota limits** button in the application.
Each profile and each model has its own counters and limits, never mixing
their consumption.

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
switching from one profile to another (**Profiles** button) immediately
shows that profile's own key counter, never mixed with another one's.

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
- **Technical files** (quota counters and limits, settings, profiles, custom
  prompts, resume state, diagnostic logs): `%APPDATA%\Distillat\`. Each
  file's exact purpose is documented in `ARCHITECTURE.md` for anyone who
  needs the implementation detail.
- **Saved reports** (`.dstl`) and **PDF exports**: `Documents\Distillat\Fiches\`
  on first use, then the last folder used for that type of file (report or
  PDF, remembered separately) is offered by default by **Save the report…**,
  **Export to .pdf**, and **Load a report…**, including after restarting the
  application (another location can always be chosen). If the displayed
  report was loaded from a file, its original folder is offered first.
- **`LICENSE`**, **`CHANGELOG.md`**, application icon, sound, and
  translation files: bundled at compile time. The `LICENSE` and
  `CHANGELOG.md` are accessible from the application's footer ("Source
  code", "Download", "Changelog", and "Website" links).

In development (`python main.py`), all these locations are identical to the
compiled mode (`%APPDATA%\Distillat\`), so that quota tracking reflects the
same actual consumption no matter how the application is launched.

## License

This software is distributed under the [GNU GPL v3](LICENSE) license.
