"""Runs one turn of the plan-editing chat: sends the conversation + tool
definitions to Claude, executes any tool calls the model makes against the
real DB (see chat_tools.py), and loops until the model returns a final
text-only reply.
"""
from __future__ import annotations

import anthropic
from anthropic import Anthropic
from sqlalchemy.orm import Session

from app import config
from app.ai.chat_tools import TOOLS, execute_tool
from app.models import Campaign, ChatMessage, Site

MODEL = "claude-sonnet-5"
MAX_TOOL_ROUNDS = 8  # safety valve against a runaway tool-call loop

SYSTEM_PROMPT_TEMPLATE = """You are an SEO analyst's assistant embedded in a task-planning platform \
for {domain}. You can see and edit the current task plan via tools (list_tasks, create_task, \
update_task, delete_task). When the analyst asks you to change something, actually call the tools \
to do it -- don't just describe what you would do.

Current campaign: starts {start_date}, {duration_months} months, {capacity} tasks/week analyst capacity.
Package: {content_pieces} content pieces/month, {pages_to_optimize} pages to optimize/month.

Be concise. Call list_tasks first if you need to see what exists before editing something specific \
(e.g. "the March tasks" or "the 404 fixes"). When you make changes, end your reply with a short, \
plain-English summary of exactly what changed."""


def _build_system_prompt(site: Site, campaign: Campaign | None) -> str:
    return SYSTEM_PROMPT_TEMPLATE.format(
        domain=site.domain,
        start_date=campaign.start_date if campaign else "not set",
        duration_months=campaign.duration_months if campaign else "?",
        capacity=campaign.capacity_per_week if campaign else "?",
        content_pieces=campaign.content_pieces_per_month if campaign else "?",
        pages_to_optimize=campaign.pages_to_optimize_per_month if campaign else "?",
    )


def run_chat_turn(db: Session, site_id: int, user_message: str) -> tuple[str, str | None]:
    """Persists the user message, runs the tool-use loop, persists the assistant's
    reply, and returns (reply_text, actions_summary_or_None)."""
    site = db.get(Site, site_id)
    campaign = (
        db.query(Campaign).filter(Campaign.site_id == site_id).order_by(Campaign.start_date.desc()).first()
    )

    db.add(ChatMessage(site_id=site_id, role="user", content=user_message))
    db.commit()

    history = (
        db.query(ChatMessage).filter(ChatMessage.site_id == site_id).order_by(ChatMessage.created_at).all()
    )
    messages = [{"role": m.role, "content": m.content} for m in history]

    system_prompt = _build_system_prompt(site, campaign)
    client = Anthropic(api_key=config.ANTHROPIC_API_KEY)
    action_summaries: list[str] = []

    for _ in range(MAX_TOOL_ROUNDS):
        try:
            response = client.messages.create(
                model=MODEL,
                max_tokens=1500,
                system=system_prompt,
                tools=TOOLS,
                messages=messages,
            )
        except anthropic.APIStatusError as exc:
            # Surface billing/auth/rate-limit errors as a normal chat reply instead of a 500 --
            # the user message is already saved, so it isn't lost; they can retry once fixed.
            if exc.status_code == 400 and "credit balance" in str(exc).lower():
                reply = (
                    "The Anthropic account behind this key has no credit balance. Add a payment "
                    "method / credits at console.anthropic.com -> Plans & Billing, then try again."
                )
            elif exc.status_code == 401:
                reply = "The Anthropic API key looks invalid or expired -- check ANTHROPIC_API_KEY in .env."
            elif exc.status_code == 429:
                reply = "Rate-limited by Anthropic -- wait a moment and try again."
            else:
                reply = f"Anthropic API error ({exc.status_code}): {exc.message}"
            db.add(ChatMessage(site_id=site_id, role="assistant", content=reply))
            db.commit()
            return reply, None
        except anthropic.APIConnectionError:
            reply = "Couldn't reach the Anthropic API -- check your network connection and try again."
            db.add(ChatMessage(site_id=site_id, role="assistant", content=reply))
            db.commit()
            return reply, None

        if response.stop_reason != "tool_use":
            final_text = "".join(block.text for block in response.content if block.type == "text")
            summary = "; ".join(action_summaries) if action_summaries else None
            db.add(ChatMessage(site_id=site_id, role="assistant", content=final_text, actions_summary=summary))
            db.commit()
            return final_text, summary

        messages.append({"role": "assistant", "content": response.content})
        tool_results = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            result, summary = execute_tool(
                db, site_id, block.name, block.input,
                campaign_start_date=campaign.start_date if campaign else None,
            )
            if summary:
                action_summaries.append(summary)
            tool_results.append({"type": "tool_result", "tool_use_id": block.id, "content": str(result)})
        messages.append({"role": "user", "content": tool_results})

    fallback = "I ran into trouble completing that in a reasonable number of steps -- try breaking your request into smaller pieces."
    db.add(ChatMessage(site_id=site_id, role="assistant", content=fallback))
    db.commit()
    return fallback, None
