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
import json
import threading
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

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
_LIMITS_FILENAME = "quota_limits.json"


def load_quota_limits(settings_dir: Path) -> tuple[int, int, int]:
    """Charge les limites RPM/TPM/RPD personnalisées par l'utilisateur, ou les
    valeurs par défaut si aucune personnalisation n'a été enregistrée."""
    limits_path = settings_dir / _LIMITS_FILENAME
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


def save_quota_limits(settings_dir: Path, rpm_limit: int, tpm_limit: int, rpd_limit: int) -> None:
    limits_path = settings_dir / _LIMITS_FILENAME
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
    input_tokens_total: int = 0
    output_tokens_total: int = 0
    _recent_calls: list[_Call] = field(default_factory=list)
    _requests_today: int = 0
    _today: date = field(default_factory=date.today)
    _rpm_limit: int = DEFAULT_RPM_LIMIT
    _tpm_limit: int = DEFAULT_TPM_LIMIT
    _rpd_limit: int = DEFAULT_RPD_LIMIT
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def __post_init__(self) -> None:
        self._load_daily_state()
        if self.settings_dir is not None:
            self._rpm_limit, self._tpm_limit, self._rpd_limit = load_quota_limits(self.settings_dir)

    def reload_limits(self) -> None:
        """Recharge les limites depuis le disque (après modification par
        l'utilisateur via le dialogue de configuration des quotas)."""
        if self.settings_dir is not None:
            with self._lock:
                self._rpm_limit, self._tpm_limit, self._rpd_limit = load_quota_limits(self.settings_dir)

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

    def _roll_day_if_needed(self) -> None:
        today = date.today()
        if today != self._today:
            self._today = today
            self._requests_today = 0

    def _prune_window(self, now: float) -> None:
        cutoff = now - _WINDOW_SECONDS
        self._recent_calls = [c for c in self._recent_calls if c.timestamp >= cutoff]

    def record_call(self, input_tokens: int, output_tokens: int) -> QuotaSnapshot:
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
        )
