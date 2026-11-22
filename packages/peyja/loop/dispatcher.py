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

EventKind = Literal[
    "inbound_message", "echo_message", "comment", "action_result", "unknown"
]


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


def _classify_item(
    account_id: str | None,
    item: dict[str, Any],
    payload: dict[str, Any],
    force_echo: bool = False,
) -> ClassifiedEvent:
    """یک آیتم از data.messaging[] (یا شکل تخت قدیمی) را طبقه‌بندی می‌کند.

    معناشناسی ig_user_id همیشه «کاربرِ مقابل گفتگو» است: در پیام ورودی = فرستنده،
    در echo/پیام خودمان = گیرنده (تا پیام‌های out درست به مکالمه بچسبند).
    """
    sender = item.get("sender") or item.get("from") or {}
    recipient = item.get("recipient") or {}
    msg = item.get("message") or item.get("postback") or {}
    is_echo = (
        force_echo
        or bool(msg.get("is_echo"))
        or (bool(recipient.get("id")) and sender.get("id") == recipient.get("id"))
    )
    if is_echo:
        # پیام خودمان: طرفِ مقابل = گیرنده
        ig_user_id = str(recipient.get("id") or recipient.get("username") or "") or None
    else:
        ig_user_id = str(sender.get("id") or sender.get("username") or "") or None
    return ClassifiedEvent(
        kind="echo_message" if is_echo else "inbound_message",
        account_id=account_id,
        ig_user_id=ig_user_id,
        text=msg.get("text") or msg.get("title"),
        mid=msg.get("mid") or msg.get("message_id"),
        at=_parse_ts(item.get("timestamp")),
        raw=payload,
    )


def classify_events(payload: dict[str, Any]) -> list[ClassifiedEvent]:
    """همه‌ی رویدادهای یک payload وب‌هوک را طبقه‌بندی می‌کند.

    طبق قرارداد رسمی BoxAPI پیام‌ها داخل آرایه‌ی data.messaging[] می‌آیند
    (چند پیام در یک payload ممکن است). شکل تخت قدیمی هم برای سازگاری پذیرفته می‌شود.
    """
    event_type = str(payload.get("event_type", ""))
    account_id = payload.get("account_id")
    data = payload.get("data") or {}

    if event_type.startswith("action."):
        return [ClassifiedEvent("action_result", account_id, raw=payload)]

    if event_type in (
        "messaging",
        "message",
        "messaging_postbacks",
        "echo",
        "message_echoes",
    ):
        # message_echoes = نوعِ جداگانه‌ی echo در واقعیت (پیام‌های خودمان) — همه echo اند
        force_echo = event_type in ("echo", "message_echoes")
        items = data.get("messaging")
        if not isinstance(items, list):
            items = [data]
        return [
            _classify_item(account_id, item, payload, force_echo=force_echo)
            for item in items
        ]

    if event_type in ("comments", "mentions"):
        return [ClassifiedEvent("comment", account_id, raw=payload)]

    return [ClassifiedEvent("unknown", account_id, raw=payload)]


def classify_event(payload: dict[str, Any]) -> ClassifiedEvent:
    """سازگاری با قبل: اولین رویداد طبقه‌بندی‌شده."""
    events = classify_events(payload)
    return events[0] if events else ClassifiedEvent("unknown", raw=payload)


async def record_inbound(session: AsyncSession, ev: ClassifiedEvent) -> None:
    """پیام را در گفتگو ثبت می‌کند.

    ورودی ⇒ باز/تازه‌سازی پنجره‌ی ۲۴ ساعته (direction=in)؛
    echo/پیام خودمان ⇒ فقط آرشیو (direction=out) و پنجره را دست نمی‌زند.
    """
    if ev.kind not in ("inbound_message", "echo_message") or not (
        ev.account_id and ev.ig_user_id
    ):
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
            # پنجره فقط با پیام ورودی باز می‌شود — echo هرگز
            window_expires_at=(
                at + dt.timedelta(hours=24) if ev.kind == "inbound_message" else None
            ),
        )
        session.add(conv)
        await session.flush()
    elif ev.kind == "inbound_message":
        conv.state = "open"
        conv.window_expires_at = at + dt.timedelta(hours=24)
    session.add(
        Message(
            conversation_id=conv.id,
            direction="in" if ev.kind == "inbound_message" else "out",
            text=ev.text,
            mid=ev.mid,
            sent_at=at,
        )
    )
