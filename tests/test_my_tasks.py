"""_tasks_for_assignee (routers/tasks.py) -- the cross-site "what do I need to do"
query backing the /my-tasks page. Every other task view in this app is scoped to
one site; this is the one place a person's tasks across every site they're
assigned on show up together, keyed off their login email against Task.assignee.
"""
import datetime as dt

from app.models import Site, Task
from app.routers.tasks import (
    _bulk_reassign,
    _calendar_months_for_tasks,
    _labeled_tasks_for_calendar,
    _SiteLabeledTask,
    _tasks_for_assignee,
)
from app.templating import short_site_label

EMAIL = "priya@peppercontent.io"


def _site(db_session, domain="example.com"):
    site = Site(domain=domain)
    db_session.add(site)
    db_session.commit()
    return site


def _task(db_session, site_id, assignee, status="todo", target_date=None, title="Task"):
    task = Task(
        site_id=site_id, source="gsc", category="ctr_optimization", severity="medium",
        title=title, description="desc", status=status, assignee=assignee, target_date=target_date,
    )
    db_session.add(task)
    db_session.commit()
    return task


def test_returns_only_tasks_assigned_to_this_email(db_session):
    site = _site(db_session)
    mine = _task(db_session, site.id, EMAIL, title="Mine")
    _task(db_session, site.id, "someone.else@peppercontent.io", title="Not mine")
    _task(db_session, site.id, None, title="Unassigned")

    result = _tasks_for_assignee(db_session, EMAIL)

    assert [t.id for t in result] == [mine.id]


def test_pulls_tasks_across_every_site_the_person_is_assigned_on(db_session):
    """The actual point of the feature: no repeating this per site."""
    site_a = _site(db_session, "a.com")
    site_b = _site(db_session, "b.com")
    t1 = _task(db_session, site_a.id, EMAIL, title="On site A")
    t2 = _task(db_session, site_b.id, EMAIL, title="On site B")

    result = _tasks_for_assignee(db_session, EMAIL)

    assert {t.id for t in result} == {t1.id, t2.id}


def test_sorted_by_due_date_with_undated_tasks_last(db_session):
    site = _site(db_session)
    undated = _task(db_session, site.id, EMAIL, target_date=None, title="No date")
    later = _task(db_session, site.id, EMAIL, target_date=dt.date(2026, 6, 1), title="Later")
    sooner = _task(db_session, site.id, EMAIL, target_date=dt.date(2026, 1, 1), title="Sooner")

    result = _tasks_for_assignee(db_session, EMAIL)

    assert [t.id for t in result] == [sooner.id, later.id, undated.id]


def test_status_filter_applies_on_top_of_the_assignee_match(db_session):
    site = _site(db_session)
    done = _task(db_session, site.id, EMAIL, status="done", title="Done")
    _task(db_session, site.id, EMAIL, status="todo", title="Todo")

    result = _tasks_for_assignee(db_session, EMAIL, status="done")

    assert [t.id for t in result] == [done.id]


def test_no_email_returns_nothing_rather_than_every_unassigned_task(db_session):
    """Login-optional deployments (config.LOGIN_REQUIRED off) have no session
    email at all -- must not fall through to matching every task site-wide."""
    site = _site(db_session)
    _task(db_session, site.id, None, title="Unassigned")
    _task(db_session, site.id, EMAIL, title="Assigned")

    assert _tasks_for_assignee(db_session, None) == []


def test_name_only_legacy_assignees_dont_match_an_email(db_session):
    """Tasks assigned by first name only, from before this convention -- expected
    to not show up here until reassigned, not a bug."""
    site = _site(db_session)
    _task(db_session, site.id, "Priya", title="Legacy name-only assignee")

    assert _tasks_for_assignee(db_session, EMAIL) == []


def test_bulk_reassign_moves_every_matching_task_across_all_sites(db_session):
    site_a = _site(db_session, "a.com")
    site_b = _site(db_session, "b.com")
    t1 = _task(db_session, site_a.id, "Sahil", title="On site A")
    t2 = _task(db_session, site_b.id, "Sahil Khan", title="On site B, different spelling")
    untouched = _task(db_session, site_a.id, "someone.else@peppercontent.io", title="Not Sahil's")

    moved = _bulk_reassign(db_session, "Sahil", EMAIL)

    assert moved == 1
    db_session.refresh(t1)
    db_session.refresh(t2)
    db_session.refresh(untouched)
    assert t1.assignee == EMAIL
    assert t2.assignee == "Sahil Khan"  # a different string -- exact match only, not a substring/fuzzy one
    assert untouched.assignee == "someone.else@peppercontent.io"


def test_bulk_reassign_is_a_no_op_on_blank_input(db_session):
    site = _site(db_session)
    t = _task(db_session, site.id, "Sahil", title="Task")

    assert _bulk_reassign(db_session, "", EMAIL) == 0
    assert _bulk_reassign(db_session, "Sahil", "  ") == 0
    db_session.refresh(t)
    assert t.assignee == "Sahil"


def test_short_site_label_strips_scheme_www_and_trailing_slash():
    assert short_site_label("https://www.oreilly.com/") == "oreilly.com"
    assert short_site_label("http://accuquote.com/") == "accuquote.com"
    assert short_site_label("cluballiance.aaa.com") == "cluballiance.aaa.com"


def test_site_labeled_task_prefixes_title_without_touching_the_real_task(db_session):
    site = _site(db_session, "www.oreilly.com")
    task = _task(db_session, site.id, EMAIL, title="Fix broken links")

    labeled = _SiteLabeledTask(task, "oreilly.com")

    assert labeled.title == "[oreilly.com] Fix broken links"
    assert labeled.severity == task.severity  # delegates every other attribute through
    assert labeled.target_date == task.target_date
    assert task.title == "Fix broken links"  # the real row is untouched


def test_labeled_tasks_for_calendar_tags_each_task_with_its_own_site(db_session):
    site_a = _site(db_session, "https://www.oreilly.com/")
    site_b = _site(db_session, "https://accuquote.com/")
    _task(db_session, site_a.id, EMAIL, title="On A")
    _task(db_session, site_b.id, EMAIL, title="On B")

    labeled = _labeled_tasks_for_calendar(db_session, EMAIL)

    titles = sorted(t.title for t in labeled)
    assert titles == ["[accuquote.com] On B", "[oreilly.com] On A"]


def test_calendar_months_spans_from_earliest_to_latest_due_date(db_session):
    site = _site(db_session)
    _task(db_session, site.id, EMAIL, target_date=dt.date(2026, 1, 15), title="Jan")
    _task(db_session, site.id, EMAIL, target_date=dt.date(2026, 3, 5), title="Mar")

    months = _calendar_months_for_tasks(_tasks_for_assignee(db_session, EMAIL))

    assert [m.label for m in months] == ["January 2026", "February 2026", "March 2026"]


def test_calendar_months_empty_when_nothing_has_a_due_date(db_session):
    site = _site(db_session)
    _task(db_session, site.id, EMAIL, target_date=None, title="Undated")

    assert _calendar_months_for_tasks(_tasks_for_assignee(db_session, EMAIL)) == []
