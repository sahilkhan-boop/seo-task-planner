"""Database engine/session setup.

SQLite for local dev (zero setup). Models are written in a Postgres-compatible
way (no SQLite-only types) so swapping DATABASE_URL to a Postgres DSN later is
a config change, not a rewrite.
"""
from __future__ import annotations

import os
from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.paths import DB_PATH


def _normalize_database_url(url: str) -> str:
    """Render (and Heroku before it) hand out connection strings starting
    "postgres://" -- SQLAlchemy 1.4+ dropped that alias and requires
    "postgresql://" for the exact same DSN, so a Render-provided DATABASE_URL
    would otherwise fail to connect at all. A plain function (not inlined into
    the module-level assignment below) so it can be unit-tested directly rather
    than via reloading this module, which would leak a second Base/engine into
    other tests still holding a reference to this module's original ones."""
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql://", 1)
    return url


DATABASE_URL = _normalize_database_url(os.environ.get("DATABASE_URL", f"sqlite:///{DB_PATH.as_posix()}"))

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


def init_db() -> None:
    # Import models so they're registered on Base.metadata before create_all.
    from app import models  # noqa: F401

    Base.metadata.create_all(bind=engine)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
