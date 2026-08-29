"""One-time copy of every row from the local SQLite database into a fresh Render
Postgres database, preserving real ids (so foreign keys still point at the right
rows) and fixing each table's auto-increment sequence afterward so new inserts on
Postgres continue from the real max id rather than colliding with row 1.

Usage:
    # See what would be copied, without touching the target at all:
    .venv\\Scripts\\python.exe scripts\\migrate_sqlite_to_postgres.py --dry-run "postgresql://..."

    # Actually copy everything:
    .venv\\Scripts\\python.exe scripts\\migrate_sqlite_to_postgres.py "postgresql://..."

The Postgres URL is the one Render's dashboard shows under the database's "Connect"
tab (the "External Database URL") -- paste it directly as the one required argument.
This never reads or needs your Render/Google/Anthropic credentials, only that one
connection string.

Refuses to run (without --force) if the target's `sites` table already has rows --
protects against accidentally double-importing on a second run.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.orm import sessionmaker

from app import models  # noqa: F401 -- registers every table on Base.metadata
from app.db import Base, DATABASE_URL as SOURCE_DATABASE_URL

# Parents before children -- CrawlIssue is the only table with a second foreign key
# (crawl_import_id) beyond site_id, so it has to come after CrawlImport.
MODELS_IN_DEPENDENCY_ORDER = [
    models.Site,
    models.Campaign,
    models.Connection,
    models.Benchmark,
    models.MetricSnapshot,
    models.CrawlImport,
    models.Task,
    models.ChatMessage,
    models.CrawlIssue,
]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("postgres_url", help="Render's Postgres 'External Database URL'")
    parser.add_argument("--dry-run", action="store_true", help="Only print row counts, touch nothing")
    parser.add_argument("--force", action="store_true", help="Proceed even if the target already has data")
    args = parser.parse_args()

    source_engine = create_engine(SOURCE_DATABASE_URL)
    target_engine = create_engine(args.postgres_url)
    SourceSession = sessionmaker(bind=source_engine)
    TargetSession = sessionmaker(bind=target_engine)

    source = SourceSession()
    print(f"Source: {SOURCE_DATABASE_URL}")
    print(f"Target: {args.postgres_url.split('@')[-1]}")  # never print the target's own credentials half of the URL
    print()

    counts = {}
    for model in MODELS_IN_DEPENDENCY_ORDER:
        n = len(source.scalars(select(model)).all())
        counts[model.__tablename__] = n
        print(f"  {model.__tablename__:20s} {n:>6} row(s)")

    if args.dry_run:
        print("\n--dry-run: target was not touched.")
        return

    Base.metadata.create_all(bind=target_engine)  # ensure every table exists on the target first

    target = TargetSession()
    existing_sites = target.execute(text("SELECT count(*) FROM sites")).scalar()
    if existing_sites and not args.force:
        print(f"\nTarget already has {existing_sites} row(s) in 'sites' -- refusing to proceed (pass --force to override).")
        return

    BATCH_SIZE = 5000  # metric_snapshots/crawl_issues run into the hundreds of thousands
    # of rows on a real campaign -- one INSERT per row over a network connection to
    # Render would take minutes to hours; a single executemany-style batch per chunk
    # is what actually keeps this practical.
    try:
        for model in MODELS_IN_DEPENDENCY_ORDER:
            columns = [c.key for c in inspect(model).mapper.column_attrs]
            total = 0
            batch: list[dict] = []
            for row in source.scalars(select(model)).yield_per(BATCH_SIZE):
                batch.append({c: getattr(row, c) for c in columns})
                if len(batch) >= BATCH_SIZE:
                    target.execute(model.__table__.insert(), batch)
                    total += len(batch)
                    batch = []
            if batch:
                target.execute(model.__table__.insert(), batch)
                total += len(batch)
            if total:
                # Postgres' own auto-increment sequence still starts at 1 -- without
                # this, the very next INSERT with no explicit id (any normal app
                # action, e.g. adding a new site) would collide with a real copied row.
                target.execute(
                    text(
                        f"SELECT setval(pg_get_serial_sequence('{model.__tablename__}', 'id'), "
                        f"(SELECT COALESCE(MAX(id), 1) FROM {model.__tablename__}))"
                    )
                )
            print(f"  copied {total:>6} row(s) into {model.__tablename__}")
        target.commit()
        print("\nDone -- committed.")
    except Exception:
        target.rollback()
        print("\nFailed -- rolled back, target left untouched by this run.")
        raise
    finally:
        source.close()
        target.close()


if __name__ == "__main__":
    main()
