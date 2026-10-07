"""Provider-agnostic LLM client.

Talks to Groq, OpenAI, Gemini and Anthropic over plain HTTP (no heavyweight
framework dependency), with retries, native JSON modes, response repair and
token accounting. When no provider is configured, callers are told so
explicitly via :class:`LLMUnavailable` rather than being handed fabricated
output - the agents then fall back to their deterministic implementations.
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass, field
from typing import Any

from config import settings
from core.http import request_with_retry
from core.logging_config import get_logger
from utils.llm_keys import (
    ERROR_COOLDOWN_SECONDS,
    DeferJob,
    PooledKey,
    UserKeyring,
    current_job_policy,
    current_keyring,
    describe_limit,
    mark_invalid,
    persist_cooldown,
    proactive_cooldown,
    record_usage,
    retry_after_from,
)

logger = get_logger("llm")

_JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)
_TRAILING_COMMA_RE = re.compile(r",\s*([}\]])")

# Roughly 4 characters per token; good enough for budgeting and cost display.
_CHARS_PER_TOKEN = 4

# Populated at runtime when a configured model has been retired upstream.
_MODEL_OVERRIDES: dict[str, str] = {}

_NON_CHAT_MODEL_HINTS = (
    "whisper", "tts", "embed", "guard", "orpheus", "moderation", "dall-e", "vision-only",
)

_PREFERRED_MODEL_HINTS = ("120b", "70b", "gpt-oss", "compound", "qwen", "llama", "gpt-4o", "27b")


def _model_rank(model_id: str) -> tuple[int, int]:
    lowered = model_id.lower()
    hint_score = sum(
        len(_PREFERRED_MODEL_HINTS) - i
        for i, hint in enumerate(_PREFERRED_MODEL_HINTS)
        if hint in lowered
    )
    return hint_score, len(model_id)


class LLMUnavailable(RuntimeError):
    """Raised when no provider is configured or every provider failed."""


@dataclass
class LLMUsage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    calls: int = 0
    failures: int = 0
    by_provider: dict[str, int] = field(default_factory=dict)

    def record(self, provider: str, prompt_tokens: int, completion_tokens: int) -> None:
        self.calls += 1
        self.prompt_tokens += prompt_tokens
        self.completion_tokens += completion_tokens
        self.by_provider[provider] = self.by_provider.get(provider, 0) + 1

    def snapshot(self) -> dict[str, Any]:
        return {
            "calls": self.calls,
            "failures": self.failures,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "by_provider": dict(self.by_provider),
        }


usage_tracker = LLMUsage()


NO_KEYS_MESSAGE = "Add your own API key in Settings -> AI keys (Groq and Gemini have free tiers)."


class RateLimited(Exception):
    def __init__(self, retry_after: float, detail: str, reason: str = "rate limited") -> None:
        super().__init__(detail)
        self.retry_after = retry_after
        self.reason = reason


class KeyRejected(Exception):
    """The provider rejected the key itself (invalid, revoked, no access)."""


class LLMClient:
    """Chat completions over the current user's own API keys (BYOK).

    Keys rotate round-robin; a rate-limited key cools down until its reported
    reset, and when every key is cooling the call waits (up to the user's max
    wait) rather than failing. ``LLMUnavailable`` is raised only when there
    are no usable keys, or the wait would exceed the limit - callers then use
    their non-AI fallback as a last resort.
    """

    def __init__(self, provider: str | None = None, keyring: UserKeyring | None = None,
                 max_wait_seconds: float | None = None) -> None:
        self._keyring = keyring
        self.preferred = (provider or "").lower() or None
        # Interactive calls (a user is watching a spinner) cap the rate-limit wait.
        self.max_wait_seconds = max_wait_seconds

    @property
    def keyring(self) -> UserKeyring | None:
        return self._keyring or current_keyring()

    @property
    def providers(self) -> list[str]:
        keyring = self.keyring
        return sorted({k.provider for k in keyring.keys if k.usable}) if keyring else []

    @property
    def is_available(self) -> bool:
        keyring = self.keyring
        return bool(keyring and any(k.usable for k in keyring.keys))

    async def generate_text(
        self,
        prompt: str,
        system_prompt: str = "",
        *,
        temperature: float = 0.2,
        max_tokens: int = 2048,
        json_mode: bool = False,
    ) -> str:
        keyring = self.keyring
        if keyring is None or not keyring.keys:
            raise LLMUnavailable(NO_KEYS_MESSAGE)

        loop = asyncio.get_running_loop()
        max_wait = keyring.max_wait_seconds if self.max_wait_seconds is None else min(keyring.max_wait_seconds, self.max_wait_seconds)
        deadline = loop.time() + max_wait
        failures = 0
        last_error: Exception | None = None

        while True:
            # The wait limit covers the whole call, including repeated 429s on a "ready" key.
            if loop.time() > deadline:
                raise LLMUnavailable(
                    f"API keys stayed rate-limited beyond the {max_wait:.0f}s wait limit for this action"
                    + (f" (last: {last_error})" if last_error else "")
                )
            key = keyring.next_ready()
            if key is None:
                wait = keyring.seconds_until_ready()
                if wait is None:
                    raise LLMUnavailable(f"All your API keys were rejected by their providers. {NO_KEYS_MESSAGE}")
                policy = current_job_policy()
                if policy is not None:
                    # Background job: never park a server process on a long wait.
                    if policy.allow_fallback:
                        raise LLMUnavailable("API keys still rate-limited after your wait limit")
                    if wait > policy.inline_wait_seconds:
                        reasons = {k.cooldown_reason for k in keyring.keys if k.usable and k.cooldown_reason}
                        raise DeferJob(time.time() + wait, ", ".join(sorted(reasons)) or "rate limited")
                elif loop.time() + wait > deadline:
                    raise LLMUnavailable(
                        f"Every API key is rate-limited for {wait:.0f}s more, beyond the "
                        f"{max_wait:.0f}s wait limit for this action"
                    )
                logger.info("All keys cooling down; waiting %.0fs for user %s", wait, keyring.user_id)
                await asyncio.sleep(wait + 0.2)
                continue

            try:
                text = await self._dispatch(key, prompt, system_prompt, temperature, max_tokens, json_mode)
            except RateLimited as exc:
                key.cool_down(exc.retry_after, exc.reason)
                await persist_cooldown(key)
                last_error = exc
                continue
            except KeyRejected as exc:
                await mark_invalid(key, str(exc))
                logger.warning("Key %s rejected: %s", key.label, exc)
                last_error = exc
                continue
            except Exception as exc:  # network errors, 5xx, malformed responses
                key.failures += 1
                failures += 1
                key.cool_down(ERROR_COOLDOWN_SECONDS, f"error: {str(exc)[:40]}")
                await persist_cooldown(key)
                last_error = exc
                if failures >= 3 * max(1, len(keyring.keys)):
                    raise LLMUnavailable(f"LLM calls keep failing. Last error: {last_error}")
                continue

            if not text or not text.strip():
                failures += 1
                last_error = LLMUnavailable(f"{key.provider} returned an empty completion")
                if failures >= 3 * max(1, len(keyring.keys)):
                    raise last_error
                continue

            prompt_tokens = (len(prompt) + len(system_prompt)) // _CHARS_PER_TOKEN
            completion_tokens = len(text) // _CHARS_PER_TOKEN
            key.calls += 1
            key.tokens += prompt_tokens + completion_tokens
            usage_tracker.record(key.provider, prompt_tokens, completion_tokens)
            await record_usage(key, prompt_tokens + completion_tokens)
            if key.cooldown_until > time.time():  # proactive rest from rate-limit headers
                await persist_cooldown(key)
            return text

    async def generate_json(
        self,
        prompt: str,
        system_prompt: str = "",
        *,
        temperature: float = 0.1,
        max_tokens: int = 2048,
    ) -> dict[str, Any]:
        """Return parsed JSON, retrying once with a stricter instruction."""
        instruction = (
            f"{prompt}\n\nRespond with a single valid JSON object and nothing else. "
            "No prose, no markdown fences."
        )

        for attempt in range(2):
            raw = await self.generate_text(
                instruction,
                system_prompt,
                temperature=temperature if attempt == 0 else 0.0,
                max_tokens=max_tokens,
                json_mode=True,
            )
            parsed = extract_json(raw)
            if parsed is not None:
                return parsed
            logger.warning("LLM returned unparseable JSON (attempt %d/2)", attempt + 1)
            instruction = (
                f"{prompt}\n\nYour previous reply was not valid JSON. "
                "Reply with ONLY a JSON object starting with { and ending with }."
            )

        raise LLMUnavailable("LLM did not return parseable JSON after 2 attempts")

    # -- provider dispatch -------------------------------------------------

    async def _dispatch(
        self,
        key: PooledKey,
        prompt: str,
        system_prompt: str,
        temperature: float,
        max_tokens: int,
        json_mode: bool,
    ) -> str:
        if key.provider == "groq":
            return await self._openai_compatible(
                "https://api.groq.com/openai/v1/chat/completions", key, prompt, system_prompt,
                temperature, max_tokens, json_mode,
            )
        if key.provider == "openai":
            return await self._openai_compatible(
                "https://api.openai.com/v1/chat/completions", key, prompt, system_prompt,
                temperature, max_tokens, json_mode,
            )
        if key.provider == "gemini":
            return await self._gemini(key, prompt, system_prompt, temperature, max_tokens, json_mode)
        if key.provider == "anthropic":
            return await self._anthropic(key, prompt, system_prompt, max_tokens)
        raise KeyRejected(f"Unknown provider: {key.provider}")

    @staticmethod
    def _check(response, key: PooledKey) -> None:
        """Map an HTTP response onto rate-limit / bad-key / generic errors."""
        if response is None:
            raise LLMUnavailable(f"network error contacting {key.provider}")
        status = response.status_code
        body = response.text[:600] if response.text else ""
        if status == 429 or (status in (503, 529) and "overload" in body.lower()):
            raise RateLimited(retry_after_from(response.headers, body), f"HTTP {status}: {body[:200]}", describe_limit(body))
        if status in (401, 403) or "API_KEY_INVALID" in body or "invalid api key" in body.lower():
            raise KeyRejected(f"HTTP {status}: {body[:200]}")
        if status >= 400:
            raise LLMUnavailable(f"HTTP {status}: {body[:300]}")
        rest = proactive_cooldown(response.headers)
        if rest:
            key.cool_down(rest, "token budget nearly used")

    async def _openai_compatible(
        self,
        url: str,
        key: PooledKey,
        prompt: str,
        system_prompt: str,
        temperature: float,
        max_tokens: int,
        json_mode: bool,
    ) -> str:
        messages: list[dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        model = _MODEL_OVERRIDES.get(key.model, key.model)
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        headers = {"Authorization": f"Bearer {key.api_key}", "Content-Type": "application/json"}

        async def post():
            return await request_with_retry(
                "POST", url, json=payload, headers=headers,
                timeout=settings.LLM_TIMEOUT_SECONDS, respect_rate_limit=False, retry_on_429=False,
            )

        response = await post()

        # Hosted models get retired without notice; resolve a live replacement once.
        if response is not None and response.status_code == 404 and "model" in response.text.lower():
            replacement = await self._resolve_live_model(url, key.api_key, model)
            if replacement:
                _MODEL_OVERRIDES[key.model] = replacement
                payload["model"] = replacement
                response = await post()

        # Some models reject JSON mode for a given prompt; retry once without it.
        if response is not None and response.status_code == 400 and json_mode and "json_validate_failed" in response.text:
            payload.pop("response_format", None)
            response = await post()

        self._check(response, key)
        data = response.json()
        return (data.get("choices") or [{}])[0].get("message", {}).get("content", "") or ""

    async def _resolve_live_model(self, chat_url: str, api_key: str, missing: str) -> str | None:
        """Pick a currently-served chat model when the configured one is gone."""
        models_url = chat_url.replace("/chat/completions", "/models")
        response = await request_with_retry(
            "GET",
            models_url,
            headers={"Authorization": f"Bearer {api_key}"},
            max_retries=1,
            respect_rate_limit=False,
            retry_on_429=False,
        )
        if response is None or response.status_code != 200:
            return None
        try:
            ids = [m.get("id", "") for m in response.json().get("data", [])]
        except ValueError:
            return None

        chat_models = [
            mid
            for mid in ids
            if mid and not any(skip in mid.lower() for skip in _NON_CHAT_MODEL_HINTS)
        ]
        if not chat_models:
            return None

        # Prefer larger general-purpose instruction models.
        chat_models.sort(key=_model_rank, reverse=True)
        chosen = chat_models[0]
        logger.warning("Model %r is unavailable - falling back to %r", missing, chosen)
        return chosen

    async def _gemini(
        self,
        key: PooledKey,
        prompt: str,
        system_prompt: str,
        temperature: float,
        max_tokens: int,
        json_mode: bool,
    ) -> str:
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{key.model}:generateContent"
        generation_config: dict[str, Any] = {
            "temperature": temperature,
            "maxOutputTokens": max_tokens,
        }
        if json_mode:
            generation_config["responseMimeType"] = "application/json"

        payload: dict[str, Any] = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": generation_config,
        }
        if system_prompt:
            payload["systemInstruction"] = {"parts": [{"text": system_prompt}]}

        response = await request_with_retry(
            "POST",
            url,
            json=payload,
            headers={"x-goog-api-key": key.api_key, "Content-Type": "application/json"},
            timeout=settings.LLM_TIMEOUT_SECONDS,
            respect_rate_limit=False,
            retry_on_429=False,
        )
        self._check(response, key)

        data = response.json()
        candidates = data.get("candidates") or []
        if not candidates:
            raise LLMUnavailable(f"Gemini returned no candidates: {str(data)[:200]}")
        parts = candidates[0].get("content", {}).get("parts") or []
        return "".join(part.get("text", "") for part in parts)

    async def _anthropic(self, key: PooledKey, prompt: str, system_prompt: str, max_tokens: int) -> str:
        # Current Claude models think adaptively and reject sampling parameters
        # (temperature/top_p), so none are sent. Low effort keeps these short
        # extraction/rewrite calls cheap; thinking shares max_tokens, so give room.
        payload: dict[str, Any] = {
            "model": key.model,
            "max_tokens": max(max_tokens * 2, 8000),
            "messages": [{"role": "user", "content": prompt}],
            "output_config": {"effort": "low"},
        }
        if system_prompt:
            payload["system"] = system_prompt
        headers = {
            "x-api-key": key.api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }
        if key.model in _ANTHROPIC_FALLBACK_MODELS:
            # Server-side refusal fallback: a declined request is retried on a
            # suitable model inside the same call instead of failing.
            headers["anthropic-beta"] = "server-side-fallback-2026-07-01"
            payload["fallbacks"] = "default"

        response = await request_with_retry(
            "POST",
            "https://api.anthropic.com/v1/messages",
            json=payload,
            headers=headers,
            timeout=settings.LLM_TIMEOUT_SECONDS,
            respect_rate_limit=False,
            retry_on_429=False,
        )
        self._check(response, key)

        data = response.json()
        if data.get("stop_reason") == "refusal":
            raise LLMUnavailable("Claude declined this request")
        blocks = data.get("content") or []
        return "".join(block.get("text", "") for block in blocks if block.get("type") == "text")


_ANTHROPIC_FALLBACK_MODELS = {"claude-opus-5-5", "claude-opus-5", "claude-fable-5-1", "claude-sonnet-5-5"}


# ---------------------------------------------------------------------------
# JSON extraction / repair
# ---------------------------------------------------------------------------


def extract_json(text: str) -> dict[str, Any] | None:
    """Pull a JSON object out of an LLM reply, repairing common mistakes."""
    if not text:
        return None

    candidates: list[str] = []
    stripped = text.strip()
    candidates.append(stripped)

    fenced = _JSON_BLOCK_RE.search(stripped)
    if fenced:
        candidates.append(fenced.group(1).strip())

    # Outermost balanced braces - handles leading/trailing prose.
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start != -1 and end > start:
        candidates.append(stripped[start : end + 1])

    for candidate in candidates:
        for attempt in (candidate, _TRAILING_COMMA_RE.sub(r"\1", candidate)):
            try:
                parsed = json.loads(attempt)
                if isinstance(parsed, dict):
                    return parsed
                if isinstance(parsed, list) and parsed and isinstance(parsed[0], dict):
                    return parsed[0]
            except json.JSONDecodeError:
                continue
    return None


INTERACTIVE_MAX_WAIT_SECONDS = 30.0


def get_llm_client(provider: str | None = None, interactive: bool = False) -> LLMClient:
    """A client bound to the current user's keys (resolved at call time).

    ``interactive=True`` for calls a user waits on in the UI: rate-limit waits are
    capped at 30s instead of the user's background limit.
    """
    return LLMClient(provider, max_wait_seconds=INTERACTIVE_MAX_WAIT_SECONDS if interactive else None)


async def probe_key(key: PooledKey) -> dict[str, Any]:
    """Test one key with a tiny request; used when a key is added or tested."""
    from utils.llm_keys import UserKeyring

    client = LLMClient(keyring=UserKeyring(user_id="probe", keys=[key], max_wait_seconds=0))
    try:
        # Reasoning models spend tokens before emitting text, so give the probe headroom.
        reply = await asyncio.wait_for(
            client.generate_text("Reply with the single word: ready", max_tokens=512),
            timeout=settings.LLM_TIMEOUT_SECONDS,
        )
        return {"ok": True, "reply": reply.strip()[:80]}
    except Exception as exc:
        if key.invalid_reason:
            return {"ok": False, "error": f"Key rejected: {key.invalid_reason[:200]}"}
        if key.cooldown_until and key.cooldown_reason == "rate limited":
            return {"ok": True, "reply": "Key works but is rate-limited right now", "rate_limited": True}
        return {"ok": False, "error": str(exc)[:300]}
