"""find_connection (services.py) -- the shared, desktop-wide OAuth connection
(Connection.site_id is None) every site falls back to, so connecting once means
every later project skips Google's consent screen entirely. See Connection's own
model docstring and google_auth.py's oauth_callback for the full design.
"""
import datetime as dt

from app.models import Connection, Site
from app.services import find_connection

EXPIRES = dt.datetime.utcnow() + dt.timedelta(hours=1)


def _site(db_session, domain="example.com"):
    site = Site(domain=domain)
    db_session.add(site)
    db_session.commit()
    return site


def _connection(db_session, site_id, provider, access_token):
    conn = Connection(site_id=site_id, provider=provider, access_token=access_token, refresh_token="rt", expires_at=EXPIRES)
    db_session.add(conn)
    db_session.commit()
    return conn


def test_falls_back_to_the_shared_connection_when_the_site_has_none(db_session):
    site = _site(db_session)
    _connection(db_session, None, "gsc", "shared-token")

    found = find_connection(db_session, site.id, "gsc")

    assert found is not None
    assert found.access_token == "shared-token"


def test_a_real_site_specific_connection_wins_over_the_shared_one(db_session):
    """The rare client whose data genuinely needs a different Google account --
    a site-specific row still takes priority when one exists."""
    site = _site(db_session)
    _connection(db_session, None, "gsc", "shared-token")
    _connection(db_session, site.id, "gsc", "site-specific-token")

    found = find_connection(db_session, site.id, "gsc")

    assert found.access_token == "site-specific-token"


def test_returns_none_when_neither_exists(db_session):
    site = _site(db_session)
    assert find_connection(db_session, site.id, "gsc") is None


def test_a_second_site_reuses_the_first_sites_shared_connection(db_session):
    """The actual point: connecting via Google from ONE site's Connect page makes
    every OTHER site (that has no connection of its own) immediately "connected"
    too, with no Google consent screen involved for the second one at all."""
    site_a = _site(db_session, "a.com")
    site_b = _site(db_session, "b.com")
    _connection(db_session, None, "ga4", "shared-token")

    assert find_connection(db_session, site_a.id, "ga4").access_token == "shared-token"
    assert find_connection(db_session, site_b.id, "ga4").access_token == "shared-token"


def test_gsc_and_ga4_shared_connections_are_independent(db_session):
    site = _site(db_session)
    _connection(db_session, None, "gsc", "gsc-shared")
    # no ga4 connection at all

    assert find_connection(db_session, site.id, "gsc").access_token == "gsc-shared"
    assert find_connection(db_session, site.id, "ga4") is None
