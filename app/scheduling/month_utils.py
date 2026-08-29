"""Shared calendar-month arithmetic used by calendar_grid.py, content_rules.py, and timeline.py."""
from __future__ import annotations

import datetime as dt


def add_months(d: dt.date, n: int) -> dt.date:
    """First-of-month date `n` calendar months after the first of `d`'s month."""
    month_index = d.month - 1 + n
    year = d.year + month_index // 12
    month = month_index % 12 + 1
    return dt.date(year, month, 1)
