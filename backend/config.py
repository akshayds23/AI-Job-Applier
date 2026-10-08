"""Application configuration.

All paths are anchored to the project root (the parent of ``backend/``) so the
server behaves identically no matter which directory it is launched from.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKEND_ROOT = Path(__file__).resolve().parent

# Load .env from the project root first, then allow backend/.env to override.
load_dotenv(PROJECT_ROOT / ".env")
load_dotenv(BACKEND_ROOT / ".env", override=True)


_LIBPQ_ONLY_PARAMS = {"sslmode", "channel_binding", "sslrootcert", "sslcert", "sslkey", "pgbouncer", "options"}


# Vercel (and similar serverless hosts) set VERCEL=1; only /tmp is writable there.
SERVERLESS = bool(os.environ.get("VERCEL"))


def _resolve(path_value: str, default_subdir: str) -> Path:
    """Resolve a configured path against the project root (or /tmp when serverless)."""
    raw = Path(path_value or default_subdir)
    if raw.is_absolute():
        return raw
    base = Path("/tmp/autoapplier") if SERVERLESS else PROJECT_ROOT
    return (base / raw).resolve()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=None, extra="ignore", case_sensitive=False)

    APP_NAME: str = "AI Auto Applier"
    ENVIRONMENT: Literal["development", "production", "test"] = "development"
    DEBUG: bool = True
    LOG_LEVEL: str = "INFO"
    SQL_ECHO: bool = False

    # --- Persistence -------------------------------------------------------
    DATABASE_URL: str = "sqlite+aiosqlite:///./data/app.db"
    DATA_DIR: str = "./data"
    SESSIONS_DIR: str = "./sessions"
    TEMPLATES_DIR: str = "./templates"

    # --- Security ----------------------------------------------------------
    SECRET_KEY: str = Field(default="", validation_alias="NEXTAUTH_SECRET")
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_TTL_MINUTES: int = 60 * 24 * 7  # 7 days
    ALLOWED_ORIGINS: str = "http://localhost:3000"
    # Optional regex for extra origins, e.g. Vercel preview deployments: https://.*\.vercel\.app
    ALLOWED_ORIGIN_REGEX: str = ""
    RATE_LIMIT_PER_MINUTE: int = 120

    # --- LLM ---------------------------------------------------------------
    DEFAULT_LLM_PROVIDER: Literal["groq", "gemini", "openai", "anthropic", "heuristic"] = "groq"
    GROQ_API_KEY: str = ""
    GEMINI_API_KEY: str = ""
    OPENAI_API_KEY: str = ""
    ANTHROPIC_API_KEY: str = ""
    GROQ_MODEL: str = "openai/gpt-oss-20b"
    GEMINI_MODEL: str = "gemini-3.1-flash-lite"
    OPENAI_MODEL: str = "gpt-4.1-nano"
    ANTHROPIC_MODEL: str = "claude-sonnet-4-5"
    LLM_TIMEOUT_SECONDS: float = 60.0
    LLM_MAX_RETRIES: int = 3

    # --- Pipeline behaviour ------------------------------------------------
    MIN_MATCH_SCORE_TO_TAILOR: float = 55.0
    MAX_JOBS_PER_SCRAPE: int = 40
    SCRAPER_CONCURRENCY: int = 4
    HTTP_TIMEOUT_SECONDS: float = 20.0
    USER_AGENT: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
    )

    # --- Automation --------------------------------------------------------
    ENABLE_SCHEDULER: bool = True
    # Shared secret for /api/internal/cron (Vercel Cron sends it as a Bearer token).
    CRON_SECRET: str = ""
    ENABLE_AUTO_SUBMIT: bool = False  # real form submission is opt-in
    # Assisted apply opens a visible browser on the machine running the backend,
    # so it only makes sense when that machine is the user's own computer.
    ASSISTED_APPLY_ENABLED: bool = not SERVERLESS  # off by default on serverless hosts
    PLAYWRIGHT_HEADLESS: bool = True
    AUTOMATION_MIN_DELAY: float = 1.5
    AUTOMATION_MAX_DELAY: float = 4.5

    @field_validator("SECRET_KEY", mode="after")
    @classmethod
    def _require_secret(cls, value: str) -> str:
        return value or os.getenv("SECRET_KEY", "")

    @property
    def serverless(self) -> bool:
        return SERVERLESS

    # --- Derived paths -----------------------------------------------------
    @property
    def data_path(self) -> Path:
        return _resolve(self.DATA_DIR, "data")

    @property
    def sessions_path(self) -> Path:
        return _resolve(self.SESSIONS_DIR, "sessions")

    @property
    def templates_path(self) -> Path:
        return _resolve(self.TEMPLATES_DIR, "templates")

    @property
    def uploads_path(self) -> Path:
        return self.data_path / "uploads"

    @property
    def generated_path(self) -> Path:
        return self.data_path / "generated"

    @property
    def screenshots_path(self) -> Path:
        return self.data_path / "screenshots"

    @property
    def allowed_origins(self) -> list[str]:
        return [o.strip() for o in self.ALLOWED_ORIGINS.split(",") if o.strip()]

    @property
    def async_database_url(self) -> str:
        """Normalise the DSN to an async driver and absolute sqlite path."""
        url = self.DATABASE_URL
        if url.startswith("postgres://"):
            url = url.replace("postgres://", "postgresql+asyncpg://", 1)
        elif url.startswith("postgresql://"):
            url = url.replace("postgresql://", "postgresql+asyncpg://", 1)
        if url.startswith("postgresql+asyncpg://"):
            # asyncpg rejects libpq-only query options (Neon adds sslmode/channel_binding);
            # SSL is passed through database_connect_args instead.
            base, _, query = url.partition("?")
            kept = [p for p in query.split("&") if p and p.split("=")[0] not in _LIBPQ_ONLY_PARAMS]
            url = base + ("?" + "&".join(kept) if kept else "")
        elif url.startswith("sqlite://") and "+aiosqlite" not in url:
            url = url.replace("sqlite://", "sqlite+aiosqlite://", 1)

        if url.startswith("sqlite+aiosqlite:///") and not url.startswith("sqlite+aiosqlite:////"):
            relative = url.replace("sqlite+aiosqlite:///", "", 1)
            if not Path(relative).is_absolute():
                absolute = (PROJECT_ROOT / relative).resolve()
                absolute.parent.mkdir(parents=True, exist_ok=True)
                url = f"sqlite+aiosqlite:///{absolute.as_posix()}"
        return url

    @property
    def database_connect_args(self) -> dict:
        """Driver options for Postgres: SSL when the URL asks for it (always for Neon),
        and no prepared-statement cache behind a transaction pooler (Neon '-pooler' hosts)."""
        url = self.DATABASE_URL
        if not url.startswith(("postgres://", "postgresql")):
            return {}
        args: dict = {}
        lowered = url.lower()
        if "sslmode=require" in lowered or "sslmode=verify" in lowered or ".neon.tech" in lowered:
            args["ssl"] = "require"
        if "-pooler." in lowered or "pgbouncer=true" in lowered:
            args["statement_cache_size"] = 0
        return args

    def configured_llm_providers(self) -> list[str]:
        """Providers that actually have credentials, best first."""
        keys = {
            "groq": self.GROQ_API_KEY,
            "gemini": self.GEMINI_API_KEY,
            "openai": self.OPENAI_API_KEY,
            "anthropic": self.ANTHROPIC_API_KEY,
        }
        ordered = [self.DEFAULT_LLM_PROVIDER] + [p for p in keys if p != self.DEFAULT_LLM_PROVIDER]
        return [p for p in ordered if keys.get(p)]

    def ensure_directories(self) -> None:
        for path in (
            self.data_path,
            self.sessions_path,
            self.templates_path,
            self.uploads_path,
            self.generated_path,
            self.screenshots_path,
        ):
            path.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
