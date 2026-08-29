import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import models  # noqa: F401 -- registers tables on Base.metadata
from app.db import Base


@pytest.fixture()
def db_session(tmp_path):
    """An isolated SQLite-backed session per test, sharing the app's real models.

    autoflush=False to match app.db.SessionLocal exactly -- a mismatch here once
    already masked a real bug (a query that assumed a same-transaction pending insert
    would be auto-flushed and visible, which worked in a default autoflush=True test
    session but silently didn't in production)."""
    engine = create_engine(f"sqlite:///{tmp_path}/test.db")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = Session()
    yield session
    session.close()
