"""Renders the campaign's month-by-month calendar (see calendar_grid.py) to a
PDF -- one landscape page per month, laid out like a desktop calendar app,
each day cell listing the tasks due that day with project + assignee.
"""
from __future__ import annotations

import io

from reportlab.lib import colors
from reportlab.lib.pagesizes import landscape, letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.scheduling.calendar_grid import WEEKDAY_LABELS, MonthGrid

SEVERITY_COLOR = {"high": "#dc2626", "medium": "#d97706", "low": "#059669"}
MAX_TASKS_PER_CELL = 3
MARGIN = 0.4 * inch
HEADER_ROW_HEIGHT = 16
HEADER_BLOCK_RESERVED = 50  # title + subtitle + spacer, generously rounded up
SAFETY_FACTOR = 0.92  # leaves slack so wrapped text never pushes a row past the page


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _day_cell_paragraph(cell, project_name: str, style: ParagraphStyle) -> Paragraph:
    lines = [f"<b>{cell.date.day}</b>"]
    shown = cell.tasks[:MAX_TASKS_PER_CELL]
    for t in shown:
        color = SEVERITY_COLOR.get(t.severity, "#374151")
        title = _escape(t.title)
        if len(title) > 30:
            title = title[:27] + "..."
        assignee = _escape(t.assignee or "Unassigned")
        lines.append(
            f'<font color="{color}" size="6.5">&#8226; {title}</font><br/>'
            f'<font size="6" color="#6b7280">{_escape(project_name)} &middot; {assignee}</font>'
        )
    if len(cell.tasks) > MAX_TASKS_PER_CELL:
        lines.append(f'<font size="6" color="#6b7280">+{len(cell.tasks) - MAX_TASKS_PER_CELL} more</font>')
    text = "<br/>".join(lines)
    if not cell.in_month:
        text = f'<font color="#9ca3af">{text}</font>'
    return Paragraph(text, style)


def build_calendar_pdf(project_name: str, months: list[MonthGrid]) -> bytes:
    buffer = io.BytesIO()
    page_size = landscape(letter)
    doc = SimpleDocTemplate(
        buffer, pagesize=page_size, leftMargin=MARGIN, rightMargin=MARGIN,
        topMargin=MARGIN, bottomMargin=MARGIN,
    )
    styles = getSampleStyleSheet()
    title_style = ParagraphStyle("month_title", parent=styles["Normal"], fontSize=14, leading=16, spaceAfter=2)
    subtitle_style = ParagraphStyle(
        "subtitle", parent=styles["Normal"], fontSize=8, leading=10, textColor=colors.HexColor("#6b7280")
    )
    cell_style = ParagraphStyle("cell", parent=styles["Normal"], fontSize=7, leading=8)
    header_style = ParagraphStyle("header", parent=styles["Normal"], fontSize=9, textColor=colors.white, alignment=1)

    frame_height = page_size[1] - 2 * MARGIN
    usable_width = page_size[0] - 2 * MARGIN
    col_width = usable_width / 7

    story = []
    for i, month in enumerate(months):
        n_data_rows = len(month.weeks)
        table_budget = (frame_height - HEADER_BLOCK_RESERVED - HEADER_ROW_HEIGHT) * SAFETY_FACTOR
        data_row_height = table_budget / max(n_data_rows, 1)
        row_heights = [HEADER_ROW_HEIGHT] + [data_row_height] * n_data_rows

        header_row = [Paragraph(d, header_style) for d in WEEKDAY_LABELS]
        data = [header_row]
        for week in month.weeks:
            data.append([_day_cell_paragraph(cell, project_name, cell_style) for cell in week])

        table = Table(data, colWidths=[col_width] * 7, rowHeights=row_heights)
        table.setStyle(
            TableStyle(
                [
                    ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2563eb")),
                    ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#e5e7eb")),
                    ("VALIGN", (0, 0), (-1, -1), "TOP"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 4),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                    ("TOPPADDING", (0, 1), (-1, -1), 2),
                    ("TOPPADDING", (0, 0), (-1, 0), 4),
                ]
            )
        )

        month_block = [
            Paragraph(month.label, title_style),
            Paragraph(f"{project_name} &mdash; SEO task plan", subtitle_style),
            Spacer(1, 4),
            table,
        ]
        # KeepTogether means if this month's block doesn't fit the remaining space on the
        # current page it moves to a fresh page as a whole, instead of splitting the table
        # mid-row (which used to leave a near-empty trailing page per month).
        story.append(KeepTogether(month_block))
        if i < len(months) - 1:
            story.append(PageBreak())

    if not months:
        story.append(Paragraph("No campaign configured yet -- set one up in Settings.", styles["Normal"]))

    doc.build(story)
    return buffer.getvalue()
