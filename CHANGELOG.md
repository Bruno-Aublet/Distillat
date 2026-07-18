# Changelog

## [1.0.0] - 2026-07-18 - Première version

### Ajouté

- Génération d'une fiche de lecture complète à partir d'un livre `.epub` ou
  `.pdf`, via l'API Gemini (gratuite) : résumé court, résumé détaillé,
  personnages principaux et analyse littéraire, toujours en français.
- Interface PyQt5 avec glisser-déposer (fichier à résumer ou fiche déjà
  générée) et 5 onglets de résultat : Couverture, Résumé court, Résumé
  détaillé, Personnages, Analyse.
- Sauvegarde des fiches dans un format JSON autonome et rechargeable
  (`.distillat.json`), et export en document Word (`.docx`) mis en forme.
- Réduction et recompression automatiques des couvertures avant stockage.
- Suivi en temps réel de la consommation du palier gratuit Gemini (tokens,
  requêtes par minute et par jour), avec avertissement à l'approche des
  limites et alerte avec compte à rebours en cas de quota dépassé.
- Stockage chiffré de la clé API via le Gestionnaire d'identification Windows
  (keyring), jamais en clair sur disque.
- Emplacements de données persistants indépendants du dossier de
  l'exécutable (`%APPDATA%\Distillat` pour les données techniques,
  `Documents\Distillat\Fiches` pour les fiches), avec migration automatique
  depuis d'anciens emplacements utilisés par les versions de travail.
- Confirmation avant de perdre une fiche non sauvegardée (nouveau fichier,
  fermeture de la fiche ou de l'application).
- Script de compilation (`build.py`) produisant un exécutable Windows autonome
  via PyInstaller, avec icône et métadonnées de version.
- Fiche d'exemple fournie dans `Fiches/` pour visualiser le rendu sans
  consommer de quota Gemini.
