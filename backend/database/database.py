"""Async SQLAlchemy engine, session factory and schema bootstrap."""
from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import NullPool

from config import settings
from core.logging_config import get_logger

logger = get_logger("database")

settings.ensure_directories()

_is_sqlite = settings.async_database_url.startswith("sqlite")

engine = create_async_engine(
    settings.async_database_url,
    echo=settings.SQL_ECHO,
    future=True,
    # SQLite + aiosqlite does not benefit from pooling and NullPool avoids
    # cross-event-loop reuse issues when the scheduler runs alongside the API.
    poolclass=NullPool if _is_sqlite else None,
    pool_pre_ping=not _is_sqlite,
    # Serverless Postgres (Neon) closes idle connections; recycle before that happens.
    **({} if _is_sqlite else {"pool_size": 5, "max_overflow": 5, "pool_recycle": 240}),
    connect_args=settings.database_connect_args,
)

if _is_sqlite:

    @event.listens_for(engine.sync_engine, "connect")
    def _sqlite_pragmas(dbapi_connection, _record):  # pragma: no cover - driver hook
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA busy_timeout=8000")
        cursor.close()


AsyncSessionLocal = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency. Rolls back on error; endpoints commit explicitly."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


async def init_db() -> None:
    from database import models  # noqa: F401  (register mappers)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await _apply_lightweight_migrations()
    logger.info("Schema ready (%s)", "sqlite" if _is_sqlite else "postgres")


# Columns added after the first release. Keeping this tiny, explicit list means
# existing dev databases upgrade in place without a full migration toolchain.
_ADDITIVE_COLUMNS: list[tuple[str, str, str]] = [
    ("users", "password_hash", "VARCHAR(255)"),
    ("users", "is_active", "BOOLEAN DEFAULT 1"),
    ("users", "last_login_at", "DATETIME"),
    ("user_profiles", "auto_apply_min_score", "FLOAT DEFAULT 75.0"),
    ("user_profiles", "daily_application_limit", "INTEGER DEFAULT 10"),
    ("user_profiles", "enabled_platforms", "JSON"),
    ("user_profiles", "excluded_companies", "JSON"),
    ("user_profiles", "keywords_exclude", "JSON"),
    ("user_profiles", "llm_provider", "VARCHAR(20)"),
    ("user_profiles", "llm_max_wait_minutes", "INTEGER DEFAULT 15"),
    ("user_profiles", "remote_preference", "VARCHAR(10) DEFAULT 'any'"),
    ("user_profiles", "max_job_age_days", "INTEGER DEFAULT 30"),
    ("user_profiles", "title_suggestions", "JSON"),
    ("ai_keys", "cooldown_until", "TIMESTAMP"),
    ("ai_keys", "cooldown_reason", "VARCHAR(60)"),
    ("ai_keys", "calls", "INTEGER DEFAULT 0"),
    ("ai_keys", "tokens", "INTEGER DEFAULT 0"),
    ("applications", "prepare_state", "JSON"),
    ("job_listings", "raw_payload", "JSON"),
    ("scrape_runs", "jobs_verified", "INTEGER DEFAULT 0"),
    ("scrape_runs", "jobs_suspicious", "INTEGER DEFAULT 0"),
    ("job_listings", "trust_score", "FLOAT"),
    ("job_listings", "trust_label", "VARCHAR(20)"),
    ("job_listings", "trust_flags", "JSON"),
    ("job_listings", "verified_url", "VARCHAR(1000)"),
    ("applications", "cover_letter_path", "TEXT"),
    ("applications", "achievements", "JSON"),
    ("applications", "tailored_headline", "VARCHAR(200)"),
    ("applications", "tailored_skill_groups", "JSON"),
    ("user_profiles", "headline", "VARCHAR(200)"),
    ("user_profiles", "pdf_engine", "VARCHAR(20) DEFAULT 'latex'"),
    ("user_profiles", "contact_email", "VARCHAR(255)"),
    ("user_profiles", "achievements", "JSON"),
    ("user_profiles", "certifications", "JSON"),
    ("applications", "submission_method", "VARCHAR(30)"),
    ("applications", "external_reference", "VARCHAR(255)"),
    ("user_job_matches", "reasoning", "TEXT"),
    ("user_job_matches", "scoring_method", "VARCHAR(20)"),
]


async def _apply_lightweight_migrations() -> None:
    async with engine.begin() as conn:
        for table, column, ddl_type in _ADDITIVE_COLUMNS:
            if not await _table_exists(conn, table):
                continue
            if await _column_exists(conn, table, column):
                continue
            try:
                await conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl_type}"))
                logger.info("Migrated: added %s.%s", table, column)
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning("Could not add %s.%s: %s", table, column, exc)


async def _table_exists(conn, table: str) -> bool:
    if _is_sqlite:
        result = await conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='table' AND name=:t"), {"t": table}
        )
    else:
        result = await conn.execute(
            text("SELECT table_name FROM information_schema.tables WHERE table_name=:t"), {"t": table}
        )
    return result.first() is not None


async def _column_exists(conn, table: str, column: str) -> bool:
    if _is_sqlite:
        result = await conn.execute(text(f"PRAGMA table_info({table})"))
        return any(row[1] == column for row in result.fetchall())
    result = await conn.execute(
        text("SELECT column_name FROM information_schema.columns WHERE table_name=:t AND column_name=:c"),
        {"t": table, "c": column},
    )
    return result.first() is not None


async def dispose_engine() -> None:
    await engine.dispose()
