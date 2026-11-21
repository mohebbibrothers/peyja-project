"""روتر وب‌هوک — تأیید HMAC + حذف تکراری + ثبت رویدادها.

نکته‌ی حیاتی: raw body قبل از هر parse برای امضا استفاده می‌شود.
رویدادهای action.* نتایج اکشن‌های async (202) هستند.
"""

from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Header, HTTPException, Request
from sqlalchemy import select

from peyja.config import get_settings
from peyja.db import get_session_factory
from peyja.ingest.webhook import verify_boxapi_signature
from peyja.models import WebhookEvent, utcnow

router = APIRouter(tags=["webhooks"])

# حافظه‌ی کوتاه‌مدت event_id برای dedup سریع (DB هم مرجع نهایی است)
_seen_events: dict[str, float] = {}
_DEDUP_TTL = 3600.0


def _prune_seen() -> None:
    now = time.time()
    for k, v in list(_seen_events.items()):
        if now - v > _DEDUP_TTL:
            del _seen_events[k]


@router.post("/boxapi")
@router.post("/boxapi/")
async def boxapi_webhook(
    request: Request,
    x_boxapi_signature: str | None = Header(default=None),
    x_boxapi_timestamp: str | None = Header(default=None),
    x_boxapi_event_id: str | None = Header(default=None),
) -> dict[str, Any]:
    settings = get_settings()
    raw = await request.body()

    verification = verify_boxapi_signature(
        secret=settings.boxapi_webhook_secret,
        raw_body=raw,
        signature_header=x_boxapi_signature or "",
        timestamp_header=x_boxapi_timestamp or "",
    )
    if not verification.ok:
        raise HTTPException(status_code=401, detail=verification.reason)

    event_id = x_boxapi_event_id or ""
    if not event_id:
        raise HTTPException(status_code=400, detail="X-BoxApi-Event-Id الزامی است")

    _prune_seen()
    if event_id in _seen_events:
        return {"status": "duplicate"}
    _seen_events[event_id] = time.time()

    payload = await request.json()
    event_type = str(payload.get("event_type", "unknown"))

    factory = get_session_factory()
    async with factory() as session:
        existing = (
            await session.execute(select(WebhookEvent).where(WebhookEvent.event_id == event_id))
        ).scalar_one_or_none()
        if existing:
            return {"status": "duplicate"}

        # طبقه‌بندی + ثبت پیام‌های ورودی (حلقه‌ی رسمی)
        from peyja.loop.dispatcher import classify_event, record_inbound

        classified = classify_event(payload)
        await record_inbound(session, classified)

        processed = classified.kind in (
            "action_result",
            "inbound_message",
            "echo_message",
            "comment",
        )
        session.add(
            WebhookEvent(
                event_id=event_id,
                account_id=payload.get("account_id"),
                event_type=event_type,
                payload=payload,
                received_at=utcnow(),
                processed_at=utcnow() if processed else None,
            )
        )
        await session.commit()

    return {"status": "accepted", "event_type": event_type}


@router.get("/boxapi")
async def boxapi_webhook_health() -> dict[str, str]:
    """برخی داشبوردها هنگام ثبت URL با GET آن را بررسی می‌کنند."""
    return {"status": "ok", "endpoint": "boxapi-webhook"}
