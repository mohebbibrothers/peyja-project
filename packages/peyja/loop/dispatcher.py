"""طبقه‌بندی رویدادهای وب‌هوک + ثبت پیام‌ها — آماده‌سازی حلقه‌ی رسمی.

echo (پیام خودمان) ≠ پیام ورودی: echo سیگنال «بازخورد فروشنده» است
(قیمتی که فروشنده در چت می‌گوید = قیمت نهایی) و پنجره را باز نمی‌کند.
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from peyja.models import Conversation, Message, utcnow

EventKind = Literal["inbound_message", "echo_message", "comment", "action_result", "unknown"]


@dataclass
class ClassifiedEvent:
    kind: EventKind
    account_id: str | None = None
    ig_user_id: str | None = None
    text: str | None = None
    mid: str | None = None
    at: dt.datetime | None = None
    raw: dict[str, Any] | None = None


def _parse_ts(ms: Any) -> dt.datetime | None:
    try:
        return dt.datetime.fromtimestamp(int(ms) / 1000, tz=dt.UTC)
    except (TypeError, ValueError):
        return None


def classify_event(payload: dict[str, Any]) -> ClassifiedEvent:
    """یک رویداد وب‌هوک را طبقه‌بندی می‌کند."""
    event_type = str(payload.get("event_type", ""))
    account_id = payload.get("account_id")
    data = payload.get("data") or {}

    if event_type.startswith("action."):
        return ClassifiedEvent("action_result", account_id, raw=payload)

    if event_type in ("messaging", "message", "messaging_postbacks", "echo"):
        sender = data.get("sender") or data.get("from") or {}
        recipient = data.get("recipient") or {}
        msg = data.get("message") or data.get("postback") or {}
        ig_user_id = str(sender.get("id") or sender.get("username") or "") or None
        is_echo = bool(msg.get("is_echo")) or (
            bool(recipient.get("id")) and sender.get("id") == recipient.get("id")
        )
        return ClassifiedEvent(
            kind="echo_message" if is_echo else "inbound_message",
            account_id=account_id,
            ig_user_id=ig_user_id,
            text=msg.get("text") or msg.get("title"),
            mid=msg.get("mid") or msg.get("message_id"),
            at=_parse_ts(data.get("timestamp")),
            raw=payload,
        )

    if event_type in ("comments", "mentions"):
        return ClassifiedEvent("comment", account_id, raw=payload)

    return ClassifiedEvent("unknown", account_id, raw=payload)


async def record_inbound(session: AsyncSession, ev: ClassifiedEvent) -> None:
    """پیام ورودی را در گفتگو ثبت می‌کند (باز کردن پنجره‌ی ۲۴ ساعته)."""
    if ev.kind != "inbound_message" or not (ev.account_id and ev.ig_user_id):
        return
    conv = (
        await session.execute(
            select(Conversation).where(
                Conversation.account_id == ev.account_id,
                Conversation.ig_user_id == ev.ig_user_id,
            )
        )
    ).scalar_one_or_none()
    at = ev.at or utcnow()
    if conv is None:
        conv = Conversation(
            account_id=ev.account_id,
            ig_user_id=ev.ig_user_id,
            state="open",
            window_expires_at=at + dt.timedelta(hours=24),
        )
        session.add(conv)
        await session.flush()
    else:
        conv.state = "open"
        conv.window_expires_at = at + dt.timedelta(hours=24)
    session.add(
        Message(conversation_id=conv.id, direction="in", text=ev.text, mid=ev.mid, sent_at=at)
    )
