from app.db import _normalize_database_url


def test_render_style_postgres_scheme_is_normalized_for_sqlalchemy():
    """Render (and Heroku before it) hand out "postgres://" connection strings --
    SQLAlchemy 1.4+ requires "postgresql://" for the identical DSN, so this must be
    rewritten or a Render-hosted deploy's DATABASE_URL would fail to connect at all."""
    assert _normalize_database_url("postgres://user:pass@host:5432/dbname") == (
        "postgresql://user:pass@host:5432/dbname"
    )


def test_already_correct_postgresql_scheme_is_left_alone():
    url = "postgresql://user:pass@host:5432/dbname"
    assert _normalize_database_url(url) == url


def test_sqlite_url_is_left_alone():
    url = "sqlite:///C:/some/path/seo_analyzer.db"
    assert _normalize_database_url(url) == url
