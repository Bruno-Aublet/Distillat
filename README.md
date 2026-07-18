# Distillat

**Version 1.0.0**

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
pour démontrer le rendu de l'application — il ne s'agit ni d'un vrai livre, ni
d'un vrai résumé, ni d'une vraie autrice. Ne cherchez pas ce livre en librairie
ou en ligne : ne le trouverez pas, car il n'a jamais existé.

## Fonctionnement

1. Glissez-déposez un fichier `.epub`, `.pdf` **ou une fiche déjà générée**
   (`.distillat.json`) dans la zone prévue, ou cliquez pour parcourir (même
   choix). Déposer un EPUB/PDF prépare un nouveau résumé ; déposer une fiche
   l'ouvre directement, comme avec **Charger une fiche…**. Le format EPUB
   donne généralement un meilleur résultat pour un livre à résumer : le PDF
   n'a pas de structure de chapitres exploitable (découpage par blocs de
   pages arbitraires pour les livres volumineux) et ne fournit pas de
   couverture.
2. Cliquez sur **Résumer**.
3. L'application compte les tokens du texte extrait via l'API Gemini :
   - si le livre tient dans une seule requête, les deux résumés, les
     personnages principaux et l'analyse littéraire sont générés en un seul
     appel ;
   - sinon (livre volumineux), le texte est découpé par chapitres (table des
     matières de l'EPUB, ou blocs de pages pour un PDF), chaque chapitre est
     résumé séparément (au moins 300 mots chacun) puis consolidé en deux
     versions, et personnages + analyse sont générés en un appel
     supplémentaire.
4. Le résultat (toujours en français, quelle que soit la langue du livre)
   s'affiche dans 5 onglets : **Couverture** (image, titre, auteur),
   **Résumé court** (trois à quatre paragraphes de synthèse), **Résumé
   détaillé** (au moins 1500 mots, structuré par partie, bien davantage pour
   un roman long), **Personnages** (fiches des personnages principaux) et
   **Analyse** littéraire (au moins 600 à 900 mots, structurée par thème,
   style et portée de l'œuvre). Ces longueurs sont des cibles indiquées à
   Gemini, pas des garanties strictes.
5. Cliquez sur **Sauvegarder en .docx** pour exporter l'ensemble en document
   Word mis en forme, ou sur **Sauvegarder la fiche…** pour l'enregistrer sous
   forme de fichier JSON autonome (`.distillat.json`) rechargeable plus tard
   via **Charger une fiche…** (voir
   [Emplacement des fichiers](#emplacement-des-fichiers) pour savoir où). Si la
   fiche affichée n'a pas été sauvegardée, l'application demande confirmation
   avant de la remplacer (nouveau fichier, nouveau résumé, fiche déposée ou
   chargée, fermeture de la fiche ou de l'application).

La clé API Gemini est demandée au premier lancement et stockée de façon
chiffrée via le Gestionnaire d'identification Windows (voir
[Sécurité de la clé API](#sécurité-de-la-clé-api)).

## Suivi des quotas

L'application affiche en temps réel une estimation de la consommation du
palier gratuit Gemini (tokens entrée/sortie, requêtes et tokens par minute,
requêtes par jour), avec un avertissement dès 80 % d'une limite atteinte, et
alerte avec compte à rebours en cas de quota effectivement dépassé. Ce suivi
est local à l'application : il ne reflète pas l'usage réel si la même clé API
est utilisée ailleurs en parallèle, et les limites affichées sont des valeurs
approximatives à vérifier sur
[aistudio.google.com/rate-limit](https://aistudio.google.com/rate-limit) en
cas de doute (elles varient selon le compte).

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
elle-même — régénérez-le si le PNG source change.

Pour distribuer l'application, copiez l'intégralité du dossier
`dist/Distillat/`.

## Sécurité de la clé API

La clé API Gemini n'est **jamais stockée en clair sur disque**. Elle est
enregistrée via le module [keyring](https://pypi.org/project/keyring/), qui
délègue au Gestionnaire d'identification Windows (chiffrement DPAPI lié à
votre compte Windows sur cette machine) — pas de fichier `.env` à protéger, et
la clé reste illisible si le dossier de l'application est copié ailleurs ou
consulté par un autre compte utilisateur.

Si une version antérieure de Distillat a laissé une clé en clair dans un
fichier `.env`, elle est reprise automatiquement, migrée vers le stockage
chiffré, puis le fichier `.env` est supprimé — sans action de votre part.

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
- **`.quota_state.json`** (compteur de requêtes du jour) : `%APPDATA%\Distillat\`.
- **Fiches sauvegardées** (`.distillat.json`) et **exports Word** :
  `Documents\Distillat\Fiches\`, proposé par défaut par
  **Sauvegarder la fiche…**, **Sauvegarder en .docx** et
  **Charger une fiche…** (un autre emplacement peut toujours être choisi ; les
  sous-dossiers y sont sans problème). La couverture est automatiquement
  redimensionnée et recompressée avant d'être stockée dans la fiche ou le
  document Word, pour éviter qu'une image haute résolution alourdisse
  inutilement le fichier ; une fiche existante contenant encore une
  couverture surdimensionnée (créée par une version antérieure) est allégée
  automatiquement dès son prochain chargement.
- **`LICENSE`** et **icône de l'application** (`icons/open-book_4681875.png`) :
  embarqués à la compilation (dans `_internal/`). Le `LICENSE` est accessible
  depuis le footer de l'application (« Copyright ... - Licence GNU GPL v3 »).

En développement (`python main.py`), la clé API est stockée via keyring comme
en mode compilé ; seul le dossier des fiches change (`Fiches/` à la racine du
projet, plus pratique pour les tests).

Si vous mettez à jour depuis une version antérieure qui stockait ces fichiers
à côté de l'exécutable, l'application les déplace automatiquement vers les
nouveaux emplacements au premier lancement (sans jamais écraser un fichier
déjà présent à la destination).

## Licence

Ce logiciel est distribué sous licence [GNU GPL v3](LICENSE).
