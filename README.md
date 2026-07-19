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

**Version 1.1.0**

Application Windows avec interface PyQt5 pour générer une fiche de lecture
complète (résumés, personnages, analyse) à partir d'un livre EPUB ou PDF, en
français, via l'API Gemini (gratuite).

Le numéro de version s'affiche dans le titre de la fenêtre et dans les
propriétés Windows de l'exécutable (clic droit sur `Distillat.exe` >
Propriétés > Détails). Source unique de vérité : `app/__version__.py`
(synchronisé manuellement avec `version_info.txt` pour les métadonnées .exe).

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
   - si le livre tient dans une seule requête, les deux résumés, les
     personnages principaux et l'analyse littéraire sont générés en un seul
     appel ;
   - sinon (livre volumineux), le texte est découpé en lots de plusieurs
     chapitres consécutifs (table des matières de l'EPUB, ou blocs de pages
     pour un PDF ; le plus de chapitres possible par lot selon la taille du
     livre, pour limiter le nombre de requêtes), chaque lot est résumé en un
     appel, puis un dernier appel reçoit l'ensemble de ces résumés et produit
     en une seule fois les deux résumés finaux, les personnages principaux et
     l'analyse littéraire.
4. Le résultat (toujours en français, quelle que soit la langue du livre)
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
périls, comme le rappelle un avertissement dans la fenêtre.

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

Avec seulement 20 requêtes par jour, le quota journalier est le facteur le
plus limitant : chaque livre consomme au minimum 2 requêtes (comptage des
tokens + génération), davantage si le livre est volumineux et doit être
découpé par chapitres - comptez large.

L'application affiche en temps réel une estimation de la consommation
(tokens entrée/sortie, requêtes et tokens par minute, requêtes par jour), avec
un avertissement dès 80 % d'une limite atteinte, et une alerte avec compte à
rebours en cas de quota effectivement dépassé. Ce suivi est **local à
l'application** : il ne reflète pas l'usage réel si la même clé API est
utilisée ailleurs en parallèle (un autre outil, un test manuel via AI
Studio...), auquel cas les compteurs affichés ne seront plus fiables.

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
- **`.quota_state.json`** (compteur de requêtes du jour), **`quota_limits.json`**
  (limites RPM/TPM/RPD personnalisées, si modifiées via le bouton **Limites de
  quota**) et **`prompts.json`** (prompts personnalisés, si modifiés via le
  bouton **Prompts** ; absent tant qu'aucun prompt n'a été modifié) :
  `%APPDATA%\Distillat\`.
- **Fiches sauvegardées** (`.distillat.json`) et **exports PDF** :
  `Documents\Distillat\Fiches\`, proposé par défaut par
  **Sauvegarder la fiche…**, **Sauvegarder en .pdf** et
  **Charger une fiche…** (un autre emplacement peut toujours être choisi ; les
  sous-dossiers y sont sans problème). Si la fiche affichée a été chargée
  depuis un fichier, c'est son dossier d'origine qui est proposé à la place,
  avec son nom de fichier ; la réenregistrer sur son fichier d'origine ne
  demande aucune confirmation de remplacement (un message confirme simplement
  la modification). La couverture est automatiquement
  redimensionnée et recompressée avant d'être stockée dans la fiche ou le
  document PDF, pour éviter qu'une image haute résolution alourdisse
  inutilement le fichier ; une fiche existante contenant encore une
  couverture surdimensionnée (créée par une version antérieure) est allégée
  automatiquement dès son prochain chargement.
- **`LICENSE`** et **icône de l'application** (`icons/open-book_4681875.png`) :
  embarqués à la compilation (dans `_internal/`). Le `LICENSE` est accessible
  depuis le footer de l'application (« Copyright ... - Licence GNU GPL v3 »).

En développement (`python main.py`), la clé API (keyring) et le dossier des
fiches (`Documents\Distillat\Fiches\`) sont les mêmes qu'en mode compilé ;
seuls les fichiers techniques (`.quota_state.json`, `quota_limits.json`,
`prompts.json`) restent à la racine du projet au lieu de `%APPDATA%\Distillat\`. Le dossier
`Fiches/` à la racine du projet ne sert qu'à héberger la fiche d'exemple
fournie avec le code.

Si vous mettez à jour depuis une version antérieure qui stockait ces fichiers
à côté de l'exécutable, l'application les déplace automatiquement vers les
nouveaux emplacements au premier lancement (sans jamais écraser un fichier
déjà présent à la destination).

## Licence

Ce logiciel est distribué sous licence [GNU GPL v3](LICENSE).
