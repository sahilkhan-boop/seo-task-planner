import datetime as dt

from app.ai.chat_tools import execute_tool
from app.models import Site, Task


def _make_site(db_session, domain="example.com"):
    site = Site(domain=domain)
    db_session.add(site)
    db_session.commit()
    return site


def test_create_task_persists_and_returns_summary(db_session):
    site = _make_site(db_session)
    result, summary = execute_tool(
        db_session, site.id, "create_task",
        {"title": "Write a new guide", "description": "Cover topic X", "severity": "medium"},
    )
    assert result["title"] == "Write a new guide"
    assert "Created task" in summary
    task = db_session.query(Task).filter(Task.site_id == site.id).one()
    assert task.title == "Write a new guide"
    assert task.source == "chat"
    assert task.status == "todo"


def test_create_task_computes_month_index_from_campaign_start(db_session):
    site = _make_site(db_session)
    result, _ = execute_tool(
        db_session, site.id, "create_task",
        {"title": "T", "description": "D", "target_date": "2026-10-15"},
        campaign_start_date=dt.date(2026, 8, 1),
    )
    assert result["month_index"] == 2  # Aug=0, Sep=1, Oct=2


def test_update_task_changes_only_supplied_fields(db_session):
    site = _make_site(db_session)
    task = Task(site_id=site.id, source="crawl", category="404_fix", title="Fix the broken link",
                description="d", severity="medium", status="todo")
    db_session.add(task)
    db_session.commit()

    result, summary = execute_tool(db_session, site.id, "update_task", {"task_id": task.id, "status": "done"})
    assert result["status"] == "done"
    assert result["title"] == "Fix the broken link"  # untouched
    assert summary.startswith("Updated task #1 (status):")


def test_create_task_sets_optimization_level(db_session):
    site = _make_site(db_session)
    result, _ = execute_tool(
        db_session, site.id, "create_task",
        {"title": "T", "description": "D", "optimization_level": "quick_win"},
    )
    assert result["optimization_level"] == "quick_win"


def test_update_task_changes_optimization_level(db_session):
    site = _make_site(db_session)
    task = Task(site_id=site.id, source="crawl", category="404_fix", title="T",
                description="d", severity="medium", optimization_level="key_fix", status="todo")
    db_session.add(task)
    db_session.commit()

    result, summary = execute_tool(
        db_session, site.id, "update_task", {"task_id": task.id, "optimization_level": "quick_win"}
    )
    assert result["optimization_level"] == "quick_win"
    assert "optimization_level" in summary


def test_update_task_rejects_wrong_site(db_session):
    site_a = _make_site(db_session, "a.com")
    site_b = _make_site(db_session, "b.com")
    task = Task(site_id=site_a.id, source="crawl", category="404_fix", title="T",
                description="d", severity="medium", status="todo")
    db_session.add(task)
    db_session.commit()

    result, summary = execute_tool(db_session, site_b.id, "update_task", {"task_id": task.id, "status": "done"})
    assert "error" in result
    assert summary is None
    db_session.refresh(task)
    assert task.status == "todo"  # untouched


def test_delete_task_removes_it(db_session):
    site = _make_site(db_session)
    task = Task(site_id=site.id, source="crawl", category="404_fix", title="Delete me",
                description="d", severity="low", status="todo")
    db_session.add(task)
    db_session.commit()
    task_id = task.id

    result, summary = execute_tool(db_session, site.id, "delete_task", {"task_id": task_id})
    assert result == {"deleted": True}
    assert "Deleted task" in summary
    assert db_session.get(Task, task_id) is None


def test_list_tasks_filters_by_severity_and_stays_scoped_to_site(db_session):
    site_a = _make_site(db_session, "a.com")
    site_b = _make_site(db_session, "b.com")
    db_session.add_all([
        Task(site_id=site_a.id, source="crawl", category="404_fix", title="A-high",
             description="d", severity="high", status="todo"),
        Task(site_id=site_a.id, source="crawl", category="404_fix", title="A-low",
             description="d", severity="low", status="todo"),
        Task(site_id=site_b.id, source="crawl", category="404_fix", title="B-high",
             description="d", severity="high", status="todo"),
    ])
    db_session.commit()

    result, _ = execute_tool(db_session, site_a.id, "list_tasks", {"severity": "high"})
    assert [t["title"] for t in result] == ["A-high"]


def test_unknown_tool_returns_error_not_exception(db_session):
    site = _make_site(db_session)
    result, summary = execute_tool(db_session, site.id, "not_a_real_tool", {})
    assert "error" in result
    assert summary is None
