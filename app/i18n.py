"""Internationalisation (français/anglais). Les traductions sont chargées
depuis locales/<langue>.json (clés imbriquées par fenêtre, .format() pour les
portions dynamiques). tr() lit toujours l'état de langue actuellement chargé
en mémoire (_current_translations) : changer de langue via set_language()
prend effet immédiatement, sans redémarrage de l'application."""
import json
from pathlib import Path

from PyQt5.QtCore import QLocale

from app import config

SUPPORTED_LANGUAGES = ("fr", "en")
DEFAULT_LANGUAGE = "en"

_current_language = DEFAULT_LANGUAGE
_current_translations: dict = {}


def get_locales_dir() -> Path:
    return config.get_resource_dir() / "locales"


def detect_system_language() -> str:
    """Langue à utiliser au tout premier démarrage (aucune langue encore
    enregistrée), déterminée depuis la langue système Windows selon une
    logique en 3 cas volontairement non simplifiée (voir CLAUDE.md, règle 7) :
    système français -> français ; système anglais -> anglais ; toute autre
    langue système -> repli sur l'anglais. Cette formulation en 3 cas (plutôt
    qu'un simple if/else) est pensée pour l'ajout ultérieur d'une 3e langue :
    il suffira d'insérer un nouveau cas avant le repli générique."""
    system_language = QLocale.system().name()  # ex. "fr_FR", "en_US", "de_DE"
    if system_language.startswith("fr"):
        return "fr"
    if system_language.startswith("en"):
        return "en"
    return DEFAULT_LANGUAGE


def current_language() -> str:
    return _current_language


def init_language() -> str:
    """À appeler une fois au démarrage de l'application. Charge la langue
    déjà enregistrée par l'utilisateur, ou la détecte depuis le système au
    tout premier démarrage (et l'enregistre aussitôt pour les lancements
    suivants)."""
    language = config.load_language_setting()
    if language not in SUPPORTED_LANGUAGES:
        language = detect_system_language()
        config.save_language_setting(language)
    set_language(language)
    return language


def set_language(language: str) -> None:
    global _current_language, _current_translations
    _current_language = language
    locale_path = get_locales_dir() / f"{language}.json"
    _current_translations = json.loads(locale_path.read_text(encoding="utf-8"))


def tr(key: str, **kwargs) -> str:
    """Résout une clé imbriquée (ex. "main_window.save_button") dans la
    langue actuellement chargée, puis applique .format(**kwargs) si des
    variables sont passées."""
    value = _current_translations
    for part in key.split("."):
        value = value[part]
    return value.format(**kwargs) if kwargs else value
