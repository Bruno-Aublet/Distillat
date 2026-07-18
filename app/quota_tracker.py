"""Suivi local (estimatif) des quotas du palier gratuit Gemini pour gemini-3.5-flash.

Valeurs relevées le 18/07/2026 sur le dashboard AI Studio
(https://aistudio.google.com/rate-limit) du compte utilisé — ce sont les
limites réelles constatées pour CE compte, pas des chiffres génériques.
Google ne les expose pas via l'API ; elles peuvent varier d'un compte à
l'autre et changer dans le temps : vérifier sur le lien ci-dessus en cas de
doute et ajuster si besoin.

Le suivi lui-même est purement local : il ne reflète que ce que CETTE
application a envoyé. Si la même clé API est utilisée ailleurs en parallèle,
les compteurs ne seront plus fiables.
"""
import json
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

RPM_LIMIT = 5
TPM_LIMIT = 250_000
RPD_LIMIT = 20

_WINDOW_SECONDS = 60.0


@dataclass
class QuotaSnapshot:
    input_tokens_total: int
    output_tokens_total: int
    requests_per_minute: int
    tokens_per_minute: int
    requests_today: int
    rpm_limit: int = RPM_LIMIT
    tpm_limit: int = TPM_LIMIT
    rpd_limit: int = RPD_LIMIT


@dataclass
class _Call:
    timestamp: float
    tokens: int


@dataclass
class QuotaTracker:
    daily_state_path: Path
    input_tokens_total: int = 0
    output_tokens_total: int = 0
    _recent_calls: list[_Call] = field(default_factory=list)
    _requests_today: int = 0
    _today: date = field(default_factory=date.today)

    def __post_init__(self) -> None:
        self._load_daily_state()

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
        self._roll_day_if_needed()
        now = time.monotonic()

        self.input_tokens_total += input_tokens
        self.output_tokens_total += output_tokens
        self._recent_calls.append(_Call(timestamp=now, tokens=input_tokens + output_tokens))
        self._requests_today += 1
        self._save_daily_state()

        return self.snapshot()

    def snapshot(self) -> QuotaSnapshot:
        now = time.monotonic()
        self._prune_window(now)
        return QuotaSnapshot(
            input_tokens_total=self.input_tokens_total,
            output_tokens_total=self.output_tokens_total,
            requests_per_minute=len(self._recent_calls),
            tokens_per_minute=sum(c.tokens for c in self._recent_calls),
            requests_today=self._requests_today,
        )
