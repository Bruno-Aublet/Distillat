"""Suivi local (estimatif) des quotas du palier gratuit Gemini pour gemini-3.5-flash.

Valeurs relevées le 18/07/2026 sur le dashboard AI Studio
(https://aistudio.google.com/rate-limit) du compte utilisé - ce sont les
limites réelles constatées pour CE compte, pas des chiffres génériques.
Google ne les expose pas via l'API ; elles peuvent varier d'un compte à
l'autre et changer dans le temps : vérifier sur le lien ci-dessus en cas de
doute et ajuster si besoin.

Le suivi lui-même est purement local : il ne reflète que ce que CETTE
application a envoyé. Si la même clé API est utilisée ailleurs en parallèle,
les compteurs ne seront plus fiables.
"""
import hashlib
import json
import threading
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

# Le quota RPD (requêtes/jour) du palier gratuit Gemini est remis à zéro par
# Google à minuit heure du Pacifique (00:00 PT), jamais à minuit heure locale
# de l'utilisateur : un reset basé sur date.today() (heure système Windows)
# désynchronisait le compteur local de plusieurs heures par rapport au vrai
# reset côté Google (jusqu'à 9h-10h du matin en France selon PST/PDT), bug
# constaté le 2026-07-21. ZoneInfo gère automatiquement la bascule PST/PDT.
_QUOTA_RESET_TZ = ZoneInfo("America/Los_Angeles")


def _pacific_today() -> date:
    """Date du jour telle que comptée par Google pour le reset du quota RPD
    (minuit heure du Pacifique), indépendante du fuseau horaire local."""
    return datetime.now(_QUOTA_RESET_TZ).date()

# Valeurs par défaut si l'utilisateur n'a pas encore personnalisé ses limites
# (voir load_quota_limits/save_quota_limits) - Google peut les faire évoluer
# sans préavis, d'où la possibilité de les ajuster depuis l'application.
DEFAULT_RPM_LIMIT = 5
DEFAULT_TPM_LIMIT = 250_000
DEFAULT_RPD_LIMIT = 20

RPM_LIMIT = DEFAULT_RPM_LIMIT
TPM_LIMIT = DEFAULT_TPM_LIMIT
RPD_LIMIT = DEFAULT_RPD_LIMIT

_WINDOW_SECONDS = 60.0
_LEGACY_DAILY_STATE_FILENAME = ".quota_state.json"
_LEGACY_QUOTA_LIMITS_FILENAME = "quota_limits.json"


def api_key_hash(api_key: str) -> str:
    """Hash court (8 caractères hex) d'une clé API, jamais la clé en clair :
    même dérivation que le hash déjà journalisé par main_window dans
    api_requests.log (`cle_api_hash=...`), pour qu'un même compte se
    reconnaisse visuellement entre le nom de fichier de quota et le journal."""
    return hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:8]


def daily_state_path_for_key(settings_dir: Path, api_key: str) -> Path:
    """Chemin du fichier de compteur quotidien (RPD) propre à une clé API
    donnée : un fichier par compte, pour ne jamais mélanger le quota de deux
    clés différentes utilisées sur la même machine (bug constaté le
    2026-07-21 en testant avec plusieurs comptes Google : le compteur
    continuait d'accumuler sur l'ancien fichier unique .quota_state.json
    quel que soit le compte réellement utilisé pour la génération)."""
    return settings_dir / f".quota_state_{api_key_hash(api_key)}.json"


def quota_limits_path_for_key(settings_dir: Path, api_key: str) -> Path:
    """Chemin du fichier de limites RPM/TPM/RPD propre à une clé API donnée :
    un fichier par compte (comme daily_state_path_for_key ci-dessus), pour
    que deux comptes Google avec des paliers différents (gratuit standard,
    payant...) puissent avoir des limites configurées différentes, plutôt que
    de partager un seul quota_limits.json global (2026-07-22, support des
    profils multiples, voir app/instance_lock.py)."""
    return settings_dir / f"quota_limits_{api_key_hash(api_key)}.json"


def load_quota_limits(limits_path: Path) -> tuple[int, int, int]:
    """Charge les limites RPM/TPM/RPD personnalisées par l'utilisateur pour ce
    fichier précis (voir quota_limits_path_for_key), ou les valeurs par
    défaut si aucune personnalisation n'a été enregistrée."""
    if limits_path.exists():
        try:
            data = json.loads(limits_path.read_text(encoding="utf-8"))
            return (
                max(1, int(data["rpm_limit"])),
                max(1, int(data["tpm_limit"])),
                max(1, int(data["rpd_limit"])),
            )
        except (OSError, ValueError, KeyError, TypeError):
            pass
    return DEFAULT_RPM_LIMIT, DEFAULT_TPM_LIMIT, DEFAULT_RPD_LIMIT


def save_quota_limits(limits_path: Path, rpm_limit: int, tpm_limit: int, rpd_limit: int) -> None:
    limits_path.write_text(
        json.dumps({"rpm_limit": rpm_limit, "tpm_limit": tpm_limit, "rpd_limit": rpd_limit}),
        encoding="utf-8",
    )


@dataclass
class QuotaSnapshot:
    input_tokens_total: int
    output_tokens_total: int
    requests_per_minute: int
    tokens_per_minute: int
    requests_today: int
    rpm_limit: int = DEFAULT_RPM_LIMIT
    tpm_limit: int = DEFAULT_TPM_LIMIT
    rpd_limit: int = DEFAULT_RPD_LIMIT
    requests_in_flight: int = 0


@dataclass
class _Call:
    timestamp: float
    tokens: int


@dataclass
class QuotaTracker:
    """record_call() est appelé depuis le thread worker (SummarizeWorker) tandis
    que snapshot() est appelé périodiquement depuis le thread UI (timer
    d'affichage) : _lock protège tout l'état mutable partagé entre les deux
    (compteurs et fenêtre glissante des appels récents)."""

    daily_state_path: Path
    settings_dir: Path | None = None
    quota_limits_path: Path | None = None
    input_tokens_total: int = 0
    output_tokens_total: int = 0
    _recent_calls: list[_Call] = field(default_factory=list)
    _requests_today: int = 0
    _today: date = field(default_factory=_pacific_today)
    _rpm_limit: int = DEFAULT_RPM_LIMIT
    _tpm_limit: int = DEFAULT_TPM_LIMIT
    _rpd_limit: int = DEFAULT_RPD_LIMIT
    _requests_in_flight: int = 0
    _api_key_hash: str | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def __post_init__(self) -> None:
        self._load_daily_state()
        if self.quota_limits_path is not None:
            self._rpm_limit, self._tpm_limit, self._rpd_limit = load_quota_limits(self.quota_limits_path)

    def reload_limits(self) -> None:
        """Recharge les limites depuis le disque (après modification par
        l'utilisateur via le dialogue de configuration des quotas)."""
        if self.quota_limits_path is not None:
            with self._lock:
                self._rpm_limit, self._tpm_limit, self._rpd_limit = load_quota_limits(self.quota_limits_path)

    def switch_api_key(self, api_key: str) -> QuotaSnapshot:
        """À appeler dès que la clé API à utiliser pour la prochaine
        génération est connue (avant de lancer le worker), pour pointer le
        suivi de quota sur le fichier propre à cette clé plutôt que de
        continuer à accumuler sur l'état d'une clé précédente. Sans effet si
        la clé n'a pas changé depuis le dernier appel (ou l'initialisation) :
        ne recharge/ne réinitialise alors rien, pour ne pas perdre l'état en
        mémoire d'une génération déjà en cours avec la même clé. L'état en
        mémoire (tokens cumulés, fenêtre glissante RPM, requêtes en vol) est
        remis à zéro avant de recharger le compteur RPD persistant du
        nouveau fichier : ces valeurs n'ont aucun sens pour un autre compte.
        Les limites RPM/TPM/RPD configurées sont elles aussi rechargées
        depuis le fichier propre à cette clé (quota_limits_<hash>.json,
        2026-07-22), pour le cas où deux comptes auraient des paliers
        différents (gratuit standard, payant...)."""
        with self._lock:
            new_hash = api_key_hash(api_key)
            if new_hash == self._api_key_hash:
                return self._snapshot_locked()
            self._api_key_hash = new_hash
            if self.settings_dir is not None:
                self.daily_state_path = daily_state_path_for_key(self.settings_dir, api_key)
                self._migrate_legacy_daily_state_if_needed()
                self.quota_limits_path = quota_limits_path_for_key(self.settings_dir, api_key)
                self._migrate_legacy_quota_limits_if_needed()
                self._rpm_limit, self._tpm_limit, self._rpd_limit = load_quota_limits(self.quota_limits_path)
            self.input_tokens_total = 0
            self.output_tokens_total = 0
            self._recent_calls = []
            self._requests_in_flight = 0
            self._requests_today = 0
            self._today = _pacific_today()
            self._load_daily_state()
            return self._snapshot_locked()

    def _load_daily_state(self) -> None:
        if not self.daily_state_path.exists():
            return
        try:
            data = json.loads(self.daily_state_path.read_text(encoding="utf-8"))
            stored_day = date.fromisoformat(data["date"])
            if stored_day == self._today:
                self._requests_today = int(data["requests"])
        except (OSError, ValueError, KeyError):
            pass

    def _save_daily_state(self) -> None:
        try:
            self.daily_state_path.write_text(
                json.dumps({"date": self._today.isoformat(), "requests": self._requests_today}),
                encoding="utf-8",
            )
        except OSError:
            pass

    def _migrate_legacy_daily_state_if_needed(self) -> None:
        """Reprend l'ancien fichier de compteur unique .quota_state.json (non
        distingué par clé API, avant ce fix) vers le fichier de la clé
        actuellement sélectionnée, uniquement si celui-ci n'existe pas encore
        - pour ne pas perdre le compteur du jour au premier lancement suivant
        cette mise à jour, sans jamais écraser un fichier par-clé déjà créé.
        Ne migre qu'une fois : l'ancien fichier est renommé (donc absent) dès
        la première clé utilisée après la mise à jour ; toute clé suivante
        démarre normalement de zéro puisqu'aucune trace de sa consommation
        n'existait avant la séparation par clé."""
        if self.settings_dir is None or self.daily_state_path.exists():
            return
        legacy_path = self.settings_dir / _LEGACY_DAILY_STATE_FILENAME
        if not legacy_path.exists():
            return
        try:
            legacy_path.replace(self.daily_state_path)
        except OSError:
            pass

    def _migrate_legacy_quota_limits_if_needed(self) -> None:
        """Reprend l'ancien fichier de limites unique quota_limits.json (non
        distingué par clé API, avant ce fix du 2026-07-22) vers le fichier de
        la clé actuellement sélectionnée, uniquement si celui-ci n'existe pas
        encore - même logique que _migrate_legacy_daily_state_if_needed()
        ci-dessus (renommage, pas de copie : ne migre qu'une fois, vers la
        première clé utilisée après cette mise à jour)."""
        if self.settings_dir is None or self.quota_limits_path is None or self.quota_limits_path.exists():
            return
        legacy_path = self.settings_dir / _LEGACY_QUOTA_LIMITS_FILENAME
        if not legacy_path.exists():
            return
        try:
            legacy_path.replace(self.quota_limits_path)
        except OSError:
            pass

    def _roll_day_if_needed(self) -> None:
        today = _pacific_today()
        if today != self._today:
            self._today = today
            self._requests_today = 0

    def _prune_window(self, now: float) -> None:
        cutoff = now - _WINDOW_SECONDS
        self._recent_calls = [c for c in self._recent_calls if c.timestamp >= cutoff]

    def begin_request(self) -> QuotaSnapshot:
        """À appeler juste avant d'envoyer une requête à Gemini
        (model.generate_content), pour que l'affichage puisse signaler
        qu'une requête est en attente de réponse avant même que le compteur
        RPD/RPM (record_call, appelé lui seulement au retour de l'appel,
        succès ou échec) n'ait bougé - sans quoi ce dernier restait figé
        pendant toute la durée de la génération (parfois plusieurs minutes
        pour un gros livre), donnant l'impression à tort qu'aucune requête
        n'avait encore été envoyée. requests_in_flight ne doit jamais
        influencer requests_today/_recent_calls : ce n'est qu'un indicateur
        visuel, pas une estimation anticipée du quota consommé."""
        with self._lock:
            self._requests_in_flight += 1
            return self._snapshot_locked()

    def end_request(self) -> QuotaSnapshot:
        """À appeler juste après le retour de l'appel réseau (succès ou
        échec), avant ou après record_call() indifféremment : symétrique de
        begin_request()."""
        with self._lock:
            self._requests_in_flight = max(0, self._requests_in_flight - 1)
            return self._snapshot_locked()

    def record_call(self, input_tokens: int, output_tokens: int) -> QuotaSnapshot:
        """À appeler dès que l'appel réseau à Gemini est revenu, que la
        réponse soit un succès ou une erreur (voir app/gemini_client.py,
        _call_gemini) : Google comptabilise la requête côté serveur (RPM et
        RPD) dans les deux cas, donc le suivi local doit faire de même pour
        rester synchronisé avec le dashboard Google (divergence constatée le
        2026-07-21 quand seuls les appels réussis étaient comptés ici)."""
        with self._lock:
            self._roll_day_if_needed()
            now = time.monotonic()

            self.input_tokens_total += input_tokens
            self.output_tokens_total += output_tokens
            self._recent_calls.append(_Call(timestamp=now, tokens=input_tokens + output_tokens))
            self._requests_today += 1
            self._save_daily_state()

            return self._snapshot_locked()

    def snapshot(self) -> QuotaSnapshot:
        with self._lock:
            # Sans cet appel, l'affichage « requêtes/jour » restait figé sur
            # la valeur de la veille après minuit jusqu'au prochain appel API
            # (seul record_call remettait le compteur à zéro auparavant).
            self._roll_day_if_needed()
            return self._snapshot_locked()

    def _snapshot_locked(self) -> QuotaSnapshot:
        """Construit le snapshot ; l'appelant doit détenir _lock."""
        now = time.monotonic()
        self._prune_window(now)
        return QuotaSnapshot(
            input_tokens_total=self.input_tokens_total,
            output_tokens_total=self.output_tokens_total,
            requests_per_minute=len(self._recent_calls),
            tokens_per_minute=sum(c.tokens for c in self._recent_calls),
            requests_today=self._requests_today,
            rpm_limit=self._rpm_limit,
            tpm_limit=self._tpm_limit,
            rpd_limit=self._rpd_limit,
            requests_in_flight=self._requests_in_flight,
        )
