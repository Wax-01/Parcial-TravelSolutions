"""Aplica las migraciones SQL de db/migrations una sola vez cada una."""
import os
import pathlib
import sys

import psycopg

MIGRATIONS = pathlib.Path(__file__).parent / "migrations"


def main() -> int:
    url = os.environ["DATABASE_URL"]
    with psycopg.connect(url, autocommit=True, prepare_threshold=None, connect_timeout=20) as conn:
        conn.execute("create schema if not exists wandersync")
        conn.execute(
            "create table if not exists wandersync.schema_migrations "
            "(name text primary key, applied_at timestamptz not null default now())"
        )
        done = {r[0] for r in conn.execute("select name from wandersync.schema_migrations")}
        for path in sorted(MIGRATIONS.glob("*.sql")):
            if path.name in done:
                print(f"[migrate] skip {path.name}")
                continue
            print(f"[migrate] apply {path.name}")
            with conn.transaction():
                conn.execute(path.read_text(encoding="utf-8"))
                conn.execute("insert into wandersync.schema_migrations(name) values (%s)", (path.name,))
    print("[migrate] done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
