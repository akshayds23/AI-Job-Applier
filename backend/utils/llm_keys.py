"""Per-user API key pool (bring-your-own-key), stateless across server instances.

Every LLM call runs with the keyring of the user it is working for. The
keyring is loaded from the database for each request / job step and bound to
the current asyncio context, so tasks started from it inherit it.

Rotation and rate limits:
* healthy keys are used round-robin (least recently used first), preferred
  provider first, so N keys give N-way parallelism;
* a key that is rate-limited cools down until the reset time the provider
  reports (Retry-After, Groq/OpenAI reset headers, Gemini retryDelay). The
  cooldown is written to the database, so every instance respects it;
* when every key is cooling down: an interactive request waits briefly; a
  background job is *deferred* until the first key is free (DeferJob), so no
  server process sits idle waiting. Only after the user's maximum wait is the
  non-AI fallback used.
"""
from __future__ import annotations

import contextvars
import re
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from core.logging_config import get_logger

logger = get_logger("llm.keys")

PROVIDERS = ("groq", "gemini", "openai", "anthropic")

# Cheapest capable model per provider: the app only extracts, scores and rewrites
# short text, so small models do the job at a fraction of the cost. Users can pick
# a bigger model per key in Settings.
DEFAULT_MODELS = {
    "groq": "openai/gpt-oss-20b",          # $0.075 / $0.30 per 1M tokens
    "gemini": "gemini-3.1-flash-lite",     # $0.25 / $1.50
    "openai": "gpt-4.1-nano",              # $0.10 / $0.40
    "anthropic": "claude-haiku-4-5",       # $1 / $5
}

DEFAULT_COOLDOWN_SECONDS = 30.0
ERROR_COOLDOWN_SECONDS = 15.0
# Proactively rest a key when the provider says fewer tokens than this remain.
LOW_TOKEN_HEADROOM = 2500


def _utc_naive(epoch: float) -> datetime:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).replace(tzinfo=None)


def _epoch(value: datetime | None) -> float:
    return value.replace(tzinfo=timezone.utc).timestamp() if value else 0.0


@dataclass
class PooledKey:
    id: str
    provider: str
    model: str
    api_key: str
    label: str
    cooldown_until: float = 0.0          # wall-clock epoch seconds (comparable across instances)
    cooldown_reason: str = ""
    invalid_reason: str | None = None
    last_used: float = 0.0
    calls: int = 0
    tokens: int = 0
    failures: int = 0

    @property
    def usable(self) -> bool:
        return self.invalid_reason is None

    def ready(self, now: float | None = None) -> bool:
        return self.usable and self.cooldown_until <= (now or time.time())

    def cool_down(self, seconds: float, reason: str) -> None:
        self.cooldown_until = max(self.cooldown_until, time.time() + max(1.0, seconds))
        self.cooldown_reason = reason
        logger.info("Key %s (%s) cooling down %.0fs: %s", self.label, self.provider, seconds, reason)

    def status(self) -> dict[str, Any]:
        remaining = max(0.0, self.cooldown_until - time.time())
        return {
            "id": self.id,
            "state": "invalid" if self.invalid_reason else ("cooling" if remaining > 0 else "ready"),
            "cooldown_seconds": round(remaining),
            "cooldown_reason": self.cooldown_reason if remaining > 0 else "",
            "invalid_reason": self.invalid_reason,
            "calls": self.calls,
            "tokens": self.tokens,
            "failures": self.failures,
        }


@dataclass
class UserKeyring:
    user_id: str
    keys: list[PooledKey] = field(default_factory=list)
    preferred: str | None = None
    max_wait_seconds: float = 900.0

    @property
    def parallelism(self) -> int:
        return max(1, min(6, sum(1 for k in self.keys if k.usable)))

    def next_ready(self) -> PooledKey | None:
        now = time.time()
        ready = [k for k in self.keys if k.ready(now)]
        if not ready:
            return None
        ready.sort(key=lambda k: (k.provider != self.preferred, k.last_used))
        chosen = ready[0]
        chosen.last_used = now
        return chosen

    def seconds_until_ready(self) -> float | None:
        usable = [k for k in self.keys if k.usable]
        if not usable:
            return None
        return max(0.0, min(k.cooldown_until for k in usable) - time.time())

    def status(self) -> dict[str, Any]:
        wait = self.seconds_until_ready()
        return {
            "keys": [k.status() for k in self.keys],
            "waiting_seconds": round(wait) if wait else 0,
            "parallelism": self.parallelism,
        }


_current: contextvars.ContextVar[UserKeyring | None] = contextvars.ContextVar("llm_keyring", default=None)


def current_keyring() -> UserKeyring | None:
    return _current.get()


async def load_keyring(db, user_id: str, refresh: bool = False) -> UserKeyring:
    """Build the user's keyring from the database (the source of truth for cooldowns)."""
    from sqlalchemy import select

    from core.crypto import decrypt
    from database.models import AIKey, UserProfile

    rows = (
        await db.execute(
            select(AIKey).where(AIKey.user_id == user_id, AIKey.is_enabled.is_(True)).order_by(AIKey.created_at)
        )
    ).scalars().all()
    profile = (await db.execute(select(UserProfile).where(UserProfile.user_id == user_id))).scalar_one_or_none()

    keys: list[PooledKey] = []
    for row in rows:
        try:
            secret = decrypt(row.key_encrypted)
        except Exception as exc:
            logger.warning("Could not decrypt key %s: %s", row.id, exc)
            continue
        keys.append(PooledKey(
            id=row.id,
            provider=row.provider,
            model=row.model or DEFAULT_MODELS.get(row.provider, ""),
            api_key=secret,
            label=row.label or f"{row.provider} ••{row.key_last4 or ''}",
            invalid_reason=row.last_error if row.status == "invalid" else None,
            cooldown_until=_epoch(row.cooldown_until),
            cooldown_reason=row.cooldown_reason or "",
            last_used=_epoch(row.last_used_at),
            calls=row.calls or 0,
            tokens=row.tokens or 0,
        ))

    return UserKeyring(
        user_id=user_id,
        keys=keys,
        preferred=(profile.llm_provider if profile else None),
        max_wait_seconds=60.0 * (profile.llm_max_wait_minutes if profile and profile.llm_max_wait_minutes is not None else 15),
    )


def invalidate_keyring(user_id: str) -> None:
    """Kept for callers; keyrings are no longer cached, so there is nothing to drop."""


async def bind_user_keys(db, user_id: str) -> UserKeyring:
    """Attach the user's keyring to the current context (and tasks it spawns)."""
    keyring = await load_keyring(db, user_id)
    _current.set(keyring)
    return keyring


@asynccontextmanager
async def use_user_keys(user_id: str):
    """For work outside a request (jobs, scripts): load and bind the user's keys."""
    from database.database import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        keyring = await load_keyring(db, user_id)
    token = _current.set(keyring)
    try:
        yield keyring
    finally:
        _current.reset(token)


async def _update_key(key_id: str, **values: Any) -> None:
    try:
        from sqlalchemy import update

        from database.database import AsyncSessionLocal
        from database.models import AIKey

        async with AsyncSessionLocal() as db:
            await db.execute(update(AIKey).where(AIKey.id == key_id).values(**values))
            await db.commit()
    except Exception as exc:  # bookkeeping must never break an AI call
        logger.debug("Could not persist key state: %s", exc)


async def persist_cooldown(key: PooledKey) -> None:
    await _update_key(key.id, cooldown_until=_utc_naive(key.cooldown_until), cooldown_reason=key.cooldown_reason[:60])


async def record_usage(key: PooledKey, tokens: int) -> None:
    from database.models import AIKey

    await _update_key(
        key.id,
        calls=AIKey.calls + 1,
        tokens=AIKey.tokens + tokens,
        last_used_at=_utc_naive(time.time()),
    )


async def mark_invalid(key: PooledKey, reason: str) -> None:
    """Persist that a key was rejected by its provider so the UI can show it."""
    key.invalid_reason = reason[:300]
    await _update_key(key.id, status="invalid", last_error=key.invalid_reason)


# ---------------------------------------------------------------------------
# Background-job waiting
# ---------------------------------------------------------------------------


class DeferJob(Exception):
    """Raised inside a background job when every key is rate-limited for a while.

    The job runner catches it and reschedules the job for ``until`` (epoch),
    instead of a server process sleeping through the wait.
    """

    def __init__(self, until: float, reason: str = "") -> None:
        super().__init__(f"deferred until {until:.0f} ({reason})")
        self.until = until
        self.reason = reason


@dataclass
class JobWaitPolicy:
    allow_fallback: bool = False       # true once the job has waited past the user's limit
    inline_wait_seconds: float = 20.0  # short waits are still taken in-process


_job_policy: contextvars.ContextVar[JobWaitPolicy | None] = contextvars.ContextVar("llm_job_policy", default=None)


def current_job_policy() -> JobWaitPolicy | None:
    return _job_policy.get()


def set_job_policy(policy: JobWaitPolicy | None):
    return _job_policy.set(policy)


def reset_job_policy(token) -> None:
    _job_policy.reset(token)


# ---------------------------------------------------------------------------
# Rate-limit signal parsing
# ---------------------------------------------------------------------------

_DURATION_RE = re.compile(r"(?:(\d+(?:\.\d+)?)h)?(?:(\d+(?:\.\d+)?)m(?!s))?(?:(\d+(?:\.\d+)?)s)?(?:(\d+(?:\.\d+)?)ms)?$")


def parse_duration(value: str | None) -> float | None:
    """'34s', '1m30.5s', '2h3m', '250ms', '12' -> seconds."""
    if not value:
        return None
    text = str(value).strip().lower()
    try:
        return float(text)
    except ValueError:
        pass
    match = _DURATION_RE.match(text)
    if not match or not any(match.groups()):
        return None
    hours, minutes, seconds, millis = (float(g) if g else 0.0 for g in match.groups())
    return hours * 3600 + minutes * 60 + seconds + millis / 1000


_DAILY_QUOTA_RE = re.compile(r"per day|\bTPD\b|\bRPD\b|daily|quota", re.I)
_TRY_AGAIN_RE = re.compile(r"try again in ([0-9hms.]+)", re.I)


def retry_after_from(headers: Any, body: str) -> float:
    """How long the provider asked us to wait after a 429.

    An explicit Retry-After (or Gemini's retryDelay, or "try again in ...")
    is authoritative: the per-minute reset headers can say "1s" while the
    limit that was actually hit is a daily quota hours away.
    """
    explicit = parse_duration(headers.get("retry-after")) if headers is not None else None
    if explicit is None:
        match = re.search(r'"retryDelay"\s*:\s*"([^"]+)"', body or "")  # Gemini RetryInfo
        explicit = parse_duration(match.group(1)) if match else None
    if explicit is None:
        match = _TRY_AGAIN_RE.search(body or "")
        explicit = parse_duration(match.group(1).rstrip(".")) if match else None
    if explicit is not None and explicit > 0:
        return explicit + 1.0

    resets = [
        s for s in (parse_duration(headers.get(n)) for n in ("x-ratelimit-reset-tokens", "x-ratelimit-reset-requests"))
        if s is not None and s > 0
    ] if headers is not None else []
    if _DAILY_QUOTA_RE.search(body or ""):
        return (max(resets) if resets else 3600.0) + 1.0
    return (min(resets) if resets else DEFAULT_COOLDOWN_SECONDS) + 1.0


def describe_limit(body: str) -> str:
    return "daily quota used up" if _DAILY_QUOTA_RE.search(body or "") else "rate limited"


def proactive_cooldown(headers: Any) -> float | None:
    """Rest a key before it hits a 429 when the remaining token budget is tiny."""
    if headers is None:
        return None
    remaining = headers.get("x-ratelimit-remaining-tokens")
    try:
        if remaining is not None and int(float(remaining)) < LOW_TOKEN_HEADROOM:
            return parse_duration(headers.get("x-ratelimit-reset-tokens")) or DEFAULT_COOLDOWN_SECONDS
    except ValueError:
        return None
    return None
