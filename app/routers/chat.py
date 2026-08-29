from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import config
from app.ai.chat_agent import run_chat_turn
from app.db import get_db
from app.models import ChatMessage, Site
from app.templating import templates

router = APIRouter()


@router.get("/sites/{site_id}/chat")
def chat_page(site_id: int, request: Request, db: Session = Depends(get_db)):
    site = db.get(Site, site_id)
    messages = db.scalars(
        select(ChatMessage).where(ChatMessage.site_id == site_id).order_by(ChatMessage.created_at)
    ).all()
    return templates.TemplateResponse(
        request,
        "chat.html",
        {"site": site, "messages": messages, "configured": config.anthropic_configured()},
    )


@router.post("/sites/{site_id}/chat")
def send_chat_message(site_id: int, message: str = Form(...), db: Session = Depends(get_db)):
    if config.anthropic_configured() and message.strip():
        run_chat_turn(db, site_id, message.strip())
    return RedirectResponse(url=f"/sites/{site_id}/chat", status_code=303)
