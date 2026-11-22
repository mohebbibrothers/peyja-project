"""روتر وب‌هوک — تأیید HMAC + حذف تکراری + ثبت رویدادها.

نکته‌ی حیاتی: raw body قبل از هر parse برای امضا استفاده می‌شود.
رویدادهای action.* نتایج اکشن‌های async (202) هستند.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request
from sqlalchemy import select

from peyja.config import get_settings
from peyja.db import get_session_factory
from peyja.ingest.webhook import verify_boxapi_signature
from peyja.models import WebhookEvent, utcnow

router = APIRouter(tags=["webhooks"])

# حافظه‌ی کوتاه‌مدت event_id برای dedup سریع (DB هم مرجع نهایی است)
_seen_events: dict[str, float] = {}
_DEDUP_TTL = 3600.0
_log = logging.getLogger("peyja.webhook")

_schema_ready = False


async def _ensure_schema_once() -> None:
    """ساخت schema پیش از اولین دسترسی DB — یک‌بار برای هر پروسه.

    تحت Passenger پل a2wsgi «lifespan» را اجرا نمی‌کند (0 mention در سورس)،
    پس تکیه‌ی صرف به startup event = خطای 500 «no such table» در محصول (تجربه‌ی واقعی).
    """
    global _schema_ready
    if _schema_ready:
        return
    from peyja.db import ensure_schema

    await ensure_schema()
    _schema_ready = True


def _prune_seen() -> None:
    now = time.time()
    for k, v in list(_seen_events.items()):
        if now - v > _DEDUP_TTL:
            del _seen_events[k]


@router.post("/boxapi")
@router.post("/boxapi/")
async def boxapi_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_boxapi_signature: str | None = Header(default=None),
    x_boxapi_timestamp: str | None = Header(default=None),
    x_boxapi_event_id: str | None = Header(default=None),
) -> dict[str, Any]:
    settings = get_settings()
    raw = await request.body()

    # احراز امضا — طبق داک رسمی: HMAC-SHA256 روی «{timestamp}.{raw_body}»؛
    # هدر شامل پیشوند sha256= و timestamp بر حسب ثانیه‌ی یونیکس
    verification = verify_boxapi_signature(
        secret=settings.boxapi_webhook_secret,
        raw_body=raw,
        signature_header=x_boxapi_signature or "",
        timestamp_header=x_boxapi_timestamp or "",
        tolerance_seconds=settings.webhook_tolerance_seconds,
    )
    if not verification.ok:
        # بدون این لاگ، رد شدن رویدادهای واقعی در عملیات نامرئی می‌ماند
        _log.warning(
            "webhook رد شد: %s (signature=%s… timestamp=%s secret=%s)",
            verification.reason,
            (x_boxapi_signature or "")[:16] or "-",
            x_boxapi_timestamp or "-",
            "set" if settings.boxapi_webhook_secret else "unset",
        )
        raise HTTPException(status_code=401, detail=verification.reason)

    try:
        payload = await request.json()
    except ValueError:
        raise HTTPException(status_code=400, detail="بدنه باید JSON معتبر باشد") from None
    event_type = str(payload.get("event_type", "unknown"))

    # event_id طبق داک: هدر X-BoxApi-Event-Id یا فیلد event_id بدنه (که همیشه می‌آید)
    event_id = x_boxapi_event_id or str(payload.get("event_id") or "")
    if not event_id:
        raise HTTPException(status_code=400, detail="event_id (هدر یا بدنه) الزامی است")

    _prune_seen()
    if event_id in _seen_events:
        return {"status": "duplicate"}
    _seen_events[event_id] = time.time()

    await _ensure_schema_once()
    factory = get_session_factory()
    async with factory() as session:
        existing = (
            await session.execute(select(WebhookEvent).where(WebhookEvent.event_id == event_id))
        ).scalar_one_or_none()
        if existing:
            return {"status": "duplicate"}

        # طبقه‌بندی + ثبت پیام‌های ورودی (حلقه‌ی رسمی)
        # قرارداد BoxAPI: پیام‌ها داخل آرایه‌ی data.messaging[] — چند رویداد در یک payload
        from peyja.loop.dispatcher import classify_events, record_inbound

        classified_list = classify_events(payload)
        for classified in classified_list:
            await record_inbound(session, classified)

        processed = any(
            c.kind in ("action_result", "inbound_message", "echo_message", "comment")
            for c in classified_list
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

    # پاسخ خودکار P0 — در پس‌زمینه، تا پاسخ وب‌هوک تأخیر نگیرد
    # (BoxAPI در صورت timeout تا ۳ بار retry می‌کند؛ پاسخ سریع = بدون پاسخ تکراری)
    from peyja.loop.reply import build_auto_reply, send_auto_reply

    # برای هر فرستنده فقط یک پاسخ خودکار (چند پیام در یک payload = یک پاسخ)
    replied_to: set[str] = set()
    for classified in classified_list:
        reply = build_auto_reply(classified, auto_reply_text=settings.auto_reply_text)
        if reply and reply.recipient_id not in replied_to:
            replied_to.add(reply.recipient_id)
            _log.info("🤖 پاسخ خودکار زمان‌بندی شد → %s", reply.recipient_id)
            background_tasks.add_task(send_auto_reply, reply)
        elif classified.kind == "inbound_message":
            _log.info(
                "↩️ بدون پاسخ خودکار برای %s (متن=%s account=%s kind=%s)",
                classified.ig_user_id,
                "set" if settings.auto_reply_text else "EMPTY",
                classified.account_id,
                classified.kind,
            )

    return {"status": "accepted", "event_type": event_type}


@router.get("/boxapi")
async def boxapi_webhook_health() -> dict[str, str]:
    """برخی داشبوردها هنگام ثبت URL با GET آن را بررسی می‌کنند."""
    return {"status": "ok", "endpoint": "boxapi-webhook"}
