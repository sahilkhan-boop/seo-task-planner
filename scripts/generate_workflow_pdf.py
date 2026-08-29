"""One-off script: renders the campaign-phasing workflow (month structure +
site-size if/else branching + technical priority logic) to a PDF for review.
Not part of the running app -- run manually: python scripts/generate_workflow_pdf.py
"""
from __future__ import annotations

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.units import inch
from reportlab.pdfgen import canvas

PAGE_W, PAGE_H = letter
MARGIN = 0.5 * inch

INDIGO = colors.HexColor("#4f46e5")
INDIGO_LIGHT = colors.HexColor("#eef2ff")
RED = colors.HexColor("#ef4444")
RED_LIGHT = colors.HexColor("#fef2f2")
AMBER = colors.HexColor("#f59e0b")
AMBER_LIGHT = colors.HexColor("#fffbeb")
GREEN = colors.HexColor("#10b981")
GREEN_LIGHT = colors.HexColor("#ecfdf5")
GRAY = colors.HexColor("#6b7280")
DARK = colors.HexColor("#14171f")
BORDER = colors.HexColor("#d1d5db")


def wrap_text(c, text, font, size, max_width):
    words = text.split()
    lines, current = [], ""
    for w in words:
        trial = (current + " " + w).strip()
        if c.stringWidth(trial, font, size) <= max_width:
            current = trial
        else:
            lines.append(current)
            current = w
    if current:
        lines.append(current)
    return lines


def draw_box(c, x, y, w, h, title, lines, fill, border=BORDER, title_color=DARK, body_color=GRAY,
             title_size=11, body_size=8.5):
    c.setFillColor(fill)
    c.setStrokeColor(border)
    c.roundRect(x, y - h, w, h, 6, fill=1, stroke=1)
    pad = 10
    ty = y - pad - title_size
    c.setFillColor(title_color)
    c.setFont("Helvetica-Bold", title_size)
    for line in wrap_text(c, title, "Helvetica-Bold", title_size, w - 2 * pad):
        c.drawString(x + pad, ty, line)
        ty -= title_size + 2
    ty -= 4
    c.setFont("Helvetica", body_size)
    c.setFillColor(body_color)
    for line in lines:
        for wrapped in wrap_text(c, line, "Helvetica", body_size, w - 2 * pad):
            if ty < y - h + pad:
                break
            c.drawString(x + pad, ty, wrapped)
            ty -= body_size + 3
        ty -= 2


def draw_arrow(c, x1, y1, x2, y2, color=GRAY):
    c.setStrokeColor(color)
    c.setLineWidth(1.5)
    c.line(x1, y1, x2, y2)
    # arrowhead
    import math
    angle = math.atan2(y2 - y1, x2 - x1)
    head_len = 7
    for a_off in (0.35, -0.35):
        a = angle + math.pi - a_off
        c.line(x2, y2, x2 + head_len * math.cos(a), y2 + head_len * math.sin(a))


def header(c, title, subtitle, page_num, total_pages):
    c.setFillColor(DARK)
    c.setFont("Helvetica-Bold", 18)
    c.drawString(MARGIN, PAGE_H - MARGIN - 6, title)
    c.setFillColor(GRAY)
    c.setFont("Helvetica", 10)
    c.drawString(MARGIN, PAGE_H - MARGIN - 24, subtitle)
    c.setStrokeColor(BORDER)
    c.line(MARGIN, PAGE_H - MARGIN - 34, PAGE_W - MARGIN, PAGE_H - MARGIN - 34)
    c.setFont("Helvetica", 8)
    c.setFillColor(GRAY)
    c.drawRightString(PAGE_W - MARGIN, MARGIN - 20, f"Page {page_num} of {total_pages}")


def footer_note(c, text):
    c.setFont("Helvetica-Oblique", 8)
    c.setFillColor(GRAY)
    for i, line in enumerate(wrap_text(c, text, "Helvetica-Oblique", 8, PAGE_W - 2 * MARGIN)):
        c.drawString(MARGIN, MARGIN + 6 + (len(wrap_text(c, text, "Helvetica-Oblique", 8, PAGE_W - 2*MARGIN)) - 1 - i) * 10, line)


TOTAL_PAGES = 3


def page_1_timeline(c):
    header(c, "Campaign Workflow — Overview", "Default month structure (customizable per campaign)", 1, TOTAL_PAGES)

    top = PAGE_H - MARGIN - 70
    box_w = (PAGE_W - 2 * MARGIN - 2 * 30) / 3
    box_h = 150
    xs = [MARGIN, MARGIN + box_w + 30, MARGIN + 2 * (box_w + 30)]

    draw_box(c, xs[0], top, box_w, box_h, "Month 1 — Technical",
              ["Crawl-issue tasks: 404s, redirects, server errors, indexation blockers.",
               "Exact scope depends on site size — see page 2.",
               "Goal: clear the debt that would undermine everything after it."],
              fill=RED_LIGHT, title_color=RED)
    draw_box(c, xs[1], top, box_w, box_h, "Month 2 — Content",
              ["Recurring content-creation + existing-page-optimization tasks kick in "
               "(sized from the client's package).",
               "Runs in parallel with any leftover technical tail from Month 1 on large sites."],
              fill=GREEN_LIGHT, title_color=GREEN)
    draw_box(c, xs[2], top, box_w, box_h, "Month 3+ — Customizable",
              ["Default: content + optimization continues, GSC/GA4-driven tasks activate "
               "once enough post-fix data exists (~4-8 weeks).",
               "Analyst can override the mix per month."],
              fill=INDIGO_LIGHT, title_color=INDIGO)

    arrow_y = top - box_h / 2
    draw_arrow(c, xs[0] + box_w + 4, arrow_y, xs[1] - 4, arrow_y, color=DARK)
    draw_arrow(c, xs[1] + box_w + 4, arrow_y, xs[2] - 4, arrow_y, color=DARK)

    # legend / key idea box
    ky = top - box_h - 50
    draw_box(c, MARGIN, ky, PAGE_W - 2 * MARGIN, 130, "The core idea",
              ["Technical always comes first because content and optimization work is wasted if the "
               "site can't be crawled/indexed properly underneath it.",
               "But \"Month 1 = all technical\" only holds for SMALL sites. Large sites can't finish "
               "technical remediation in 30 days — the plan has to prioritize and phase it. See page 2 "
               "for the site-size branching logic, and page 3 for how large-site technical issues get "
               "ranked against each other."],
              fill=colors.HexColor("#f8fafc"), title_color=DARK, title_size=12)


def page_2_decision_tree(c):
    header(c, "Site-Size Decision Logic", "Determines how much technical work Month 1 can realistically cover", 2, TOTAL_PAGES)

    top = PAGE_H - MARGIN - 70
    root_w, root_h = 260, 70
    root_x = (PAGE_W - root_w) / 2
    draw_box(c, root_x, top, root_w, root_h, "Assess site scale",
              ["From the crawl: total indexable URLs found"],
              fill=colors.HexColor("#f1f5f9"), title_color=DARK)

    branch_y = top - root_h - 55
    box_w = (PAGE_W - 2 * MARGIN - 2 * 24) / 3
    box_h = 260
    xs = [MARGIN, MARGIN + box_w + 24, MARGIN + 2 * (box_w + 24)]

    root_bottom_mid = root_x + root_w / 2
    root_bottom_y = top - root_h

    for x, w in zip(xs, [box_w] * 3):
        draw_arrow(c, root_bottom_mid, root_bottom_y, x + w / 2, branch_y, color=GRAY)

    draw_box(c, xs[0], branch_y, box_w, box_h, "SMALL\n(< ~1,000 URLs)",
              ["IF total URLs is small enough that every open technical issue fits within the "
               "campaign's weekly analyst capacity for one month:",
               "→ ALL technical issues (404s, redirects, 5xx) scheduled in Month 1.",
               "→ Content plan can start on schedule in Month 2 with zero technical carryover."],
              fill=GREEN_LIGHT, title_color=GREEN)

    draw_box(c, xs[1], branch_y, box_w, box_h, "MEDIUM\n(~1,000–50,000 URLs)",
              ["IF issue count exceeds Month 1 capacity but isn't at enterprise scale:",
               "→ Technical split across Month 1–2, highest-impact issues first (see page 3 "
               "for the ranking).",
               "→ Content plan still starts in Month 2, running alongside the technical tail."],
              fill=AMBER_LIGHT, title_color=AMBER)

    draw_box(c, xs[2], branch_y, box_w, box_h, "LARGE / ENTERPRISE\n(millions of URLs)",
              ["IF the crawl surfaces indexation-scale problems (e.g. millions of pages not indexed, "
               "blocked by robots.txt, or wasting crawl budget):",
               "→ Month 1 covers ONLY the top-priority technical issues — indexation blockers and "
               "server errors — never \"everything.\"",
               "→ Lower-impact technical work (redirect/inlink cleanup) is pushed into Month 2+ "
               "alongside content, not treated as a Month 1 blocker."],
              fill=RED_LIGHT, title_color=RED)

    note_y = branch_y - box_h - 40
    draw_box(c, MARGIN, note_y, PAGE_W - 2 * MARGIN, 90, "Why this matters",
              ["A flat \"fix everything in Month 1\" rule works for a 200-page site and fails "
               "completely for a 3-million-page one. The platform needs to detect scale from the "
               "crawl and phase technical work accordingly — see page 3 for exactly how issues "
               "get ranked against each other once there are too many to do at once."],
              fill=colors.HexColor("#f8fafc"), title_color=DARK, title_size=11)


def page_3_priority_table(c):
    header(c, "Large-Site Technical Priority Ranking", "How Month 1 picks which issues matter most when everything can't fit", 3, TOTAL_PAGES)

    top = PAGE_H - MARGIN - 60
    rows = [
        ("1", "Indexation-blocking issues at scale", RED,
         "Millions of pages noindexed/blocked/orphaned, robots.txt misconfigurations, crawl-budget "
         "waste. Highest priority — these affect whether pages can rank AT ALL, regardless of "
         "content quality."),
        ("2", "Server errors (5xx)", RED,
         "Can silently drop pages from the index. Site-health/uptime risk — always Month 1 "
         "regardless of site size."),
        ("3", "High-impact 404s", AMBER,
         "404s on pages with significant inlink equity, high historical traffic, or many internal "
         "links pointing to them (5+ inlinks = high severity in the platform today). Prioritized "
         "over low-impact 404s within Month 1-2."),
        ("4", "Redirect / internal-link cleanup", GREEN,
         "301/302s where internal links still point to the old URL. Real leverage, but rarely "
         "urgent — these roll into Month 2+ alongside content work rather than blocking it."),
        ("5", "Low-impact 404s / orphaned pages", GRAY,
         "No inlinks, low/no traffic. Lowest priority — addressed opportunistically once "
         "higher-impact work is clear."),
    ]

    y = top
    row_h = 95
    for num, title, color, desc in rows:
        c.setFillColor(color)
        c.circle(MARGIN + 14, y - 24, 14, fill=1, stroke=0)
        c.setFillColor(colors.white)
        c.setFont("Helvetica-Bold", 13)
        c.drawCentredString(MARGIN + 14, y - 29, num)

        box_x = MARGIN + 40
        box_w = PAGE_W - 2 * MARGIN - 40
        draw_box(c, box_x, y, box_w, row_h - 10, title, [desc], fill=colors.HexColor("#f8fafc"),
                 title_color=DARK, body_color=GRAY, title_size=11.5)
        y -= row_h

    note_y = y - 20
    c.setFont("Helvetica-Oblique", 8.5)
    c.setFillColor(GRAY)
    note_lines = wrap_text(
        c,
        "Impact signals used for ranking: number of URLs affected, whether the issue blocks "
        "indexation vs. just link efficiency, and (once GSC is connected) impressions/traffic on the "
        "affected pages. This ranking is the intended next build — extending the current severity "
        "model (high/medium/low, already live for 404s/redirects/5xx) with a scale-aware tier "
        "specifically for large sites.",
        "Helvetica-Oblique", 8.5, PAGE_W - 2 * MARGIN,
    )
    for line in note_lines:
        c.drawString(MARGIN, note_y, line)
        note_y -= 11


def main():
    c = canvas.Canvas("workflow.pdf", pagesize=letter)
    page_1_timeline(c)
    c.showPage()
    page_2_decision_tree(c)
    c.showPage()
    page_3_priority_table(c)
    c.showPage()
    c.save()
    print("wrote workflow.pdf")


if __name__ == "__main__":
    main()
