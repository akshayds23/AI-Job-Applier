"""Copy every table from the local SQLite database into Postgres (e.g. Neon).

Usage (from backend/):
    ..\\.venv312\\Scripts\\python.exe -m scripts.migrate_sqlite_to_postgres "postgresql://user:pass@host/db?sslmode=require"

* Creates the schema on the target if it does not exist yet.
* Copies tables parent-first so foreign keys hold; rows already present
  (same primary key) are skipped, so the script can be re-run safely.
* SQLite never enforces VARCHAR lengths but Postgres does: over-long values
  are truncated to fit, and every truncation is reported.

Keep NEXTAUTH_SECRET identical on the new server - stored API keys and mailbox
passwords are encrypted with it.
"""
from __future__ import annotations

import asyncio
import os
import sys

from sqlalchemy import String, create_engine, select
from sqlalchemy.dialects.postgresql import insert as pg_insert


async def main(target_url: str) -> None:
    os.environ["DATABASE_URL"] = target_url  # config must see the target before importing the engine

    from config import PROJECT_ROOT, settings
    from database import models  # noqa: F401  (register tables)
    from database.database import Base, engine, init_db

    source = create_engine(f"sqlite:///{(PROJECT_ROOT / 'data' / 'app.db').as_posix()}")
    print(f"Target: {settings.async_database_url.split('@')[-1]}")
    await init_db()

    truncations = 0
    for table in Base.metadata.sorted_tables:
        with source.connect() as conn:
            try:
                rows = [dict(r._mapping) for r in conn.execute(select(table))]
            except Exception as exc:  # table not present in an older local DB
                print(f"  {table.name:22} skipped ({str(exc).splitlines()[0][:60]})")
                continue
        if not rows:
            print(f"  {table.name:22} 0 rows")
            continue

        limits = {c.name: c.type.length for c in table.columns if isinstance(c.type, String) and c.type.length}
        for row in rows:
            for column, limit in limits.items():
                value = row.get(column)
                if isinstance(value, str) and len(value) > limit:
                    row[column] = value[:limit]
                    truncations += 1
                    print(f"    truncated {table.name}.{column} to {limit} chars")

        pk = [c.name for c in table.primary_key.columns]
        async with engine.begin() as conn:
            for start in range(0, len(rows), 500):
                statement = pg_insert(table).values(rows[start:start + 500]).on_conflict_do_nothing(index_elements=pk)
                await conn.execute(statement)
        print(f"  {table.name:22} {len(rows)} rows")

    await engine.dispose()
    print(f"Done. {truncations} value(s) truncated.")


if __name__ == "__main__":
    if len(sys.argv) != 2 or not sys.argv[1].startswith(("postgres://", "postgresql")):
        sys.exit(__doc__)
    asyncio.run(main(sys.argv[1]))
