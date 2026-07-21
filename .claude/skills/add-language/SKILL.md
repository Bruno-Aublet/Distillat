---
name: add-language
description: Marche à suivre pour ajouter une nouvelle langue d'interface/sortie à Distillat (au-delà de français/anglais). À utiliser uniquement sur demande explicite d'ajout d'une langue.
---

# Ajouter une nouvelle langue à Distillat

Étapes à suivre dans cet ordre, sans en sauter aucune. Rappel du contexte
i18n : voir la règle 7 de `CLAUDE.md` (logique de détection de langue au
premier démarrage, à ne jamais simplifier).

1. **`app/i18n.py`** : ajouter le code de la langue (ex. `"es"`) à
   `SUPPORTED_LANGUAGES`. Insérer un nouveau cas spécifique dans
   `detect_system_language()` **avant** le repli générique sur l'anglais
   (la logique en 3 cas de la règle 7 devient alors 4 cas), sans toucher
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
   FRANÇAIS" / "ALWAYS IN ENGLISH" -> équivalent natif). Vérifier que les 3
   prompts de la nouvelle langue ont exactement les mêmes placeholders
   `.format()` que les prompts existants (`{book_title}`, `{full_text}`,
   `{chapter_summaries}`...), sous peine de `KeyError` selon la langue
   active. Ajouter la nouvelle langue à
   `_DEFAULT_PROMPT_TEMPLATES_BY_LANGUAGE`. Ajouter aussi son marqueur de
   titre de chapitre à `_CHAPTER_TITLE_MARKER_BY_LANGUAGE` (ex. `"TITRE"`/
   `"TITLE"` -> équivalent natif), qui doit rester cohérent avec celui annoncé
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
