from __future__ import annotations

import calendar

from fastapi.templating import Jinja2Templates

from app.paths import TEMPLATES_DIR

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def month_label(month_index: int | None) -> str:
    if month_index is None:
        return "Unscheduled"
    return f"Month {month_index + 1}"


def short_site_label(domain: str) -> str:
    """"oreilly.com", not "https://www.oreilly.com/" -- used wherever several
    sites' tasks are mixed onto one page/grid (My Tasks' list, calendar, PDF,
    and Excel views) and every task needs a short site tag, not its full URL."""
    label = domain.removeprefix("https://").removeprefix("http://").removesuffix("/")
    return label.removeprefix("www.")


templates.env.filters["month_label"] = month_label
templates.env.filters["short_site_label"] = short_site_label
templates.env.globals["calendar"] = calendar
