from __future__ import annotations

import calendar

from fastapi.templating import Jinja2Templates

from app.paths import TEMPLATES_DIR

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def month_label(month_index: int | None) -> str:
    if month_index is None:
        return "Unscheduled"
    return f"Month {month_index + 1}"


templates.env.filters["month_label"] = month_label
templates.env.globals["calendar"] = calendar
