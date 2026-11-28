"""طبقه‌بندی رویدادهای وب‌هوک + ثبت پیام‌ها — آماده‌سازی حلقه‌ی رسمی.

echo (پیام خودمان) ≠ پیام ورودی: echo سیگنال «بازخورد فروشنده» است
(قیمتی که فروشنده در چت می‌گوید = قیمت نهایی) و پنجره را باز نمی‌کند.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from peyja.models import Conversation, Evidence, Message, Offer, Product, Variant, utcnow

EventKind = Literal["inbound_message", "echo_message", "comment", "action_result", "unknown"]
_log = logging.getLogger("peyja.dispatcher")


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
        return [_classify_item(account_id, item, payload, force_echo=force_echo) for item in items]

    if event_type in ("comments", "mentions"):
        sender = data.get("from") or data.get("sender") or {}
        ig_user = str(sender.get("id") or sender.get("username") or "") or None
        cid = data.get("id") or data.get("comment_id")
        return [
            ClassifiedEvent(
                kind="comment",
                account_id=account_id,
                ig_user_id=ig_user,
                text=data.get("text"),
                mid=str(cid) if cid is not None else None,
                at=_parse_ts(data.get("timestamp")),
                raw=payload,
            )
        ]

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
    if ev.kind not in ("inbound_message", "echo_message") or not (ev.account_id and ev.ig_user_id):
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


async def learn_from_seller_echo(
    session: AsyncSession, ev: ClassifiedEvent
) -> dict[str, Any] | None:
    """سیگنال طلایی echo: استخراج قیمت تأییدشده از پاسخ فروشنده و ثبت Evidence (`seller_echo`).

    قانون صفر: اگر فروشنده در دایرکت قیمت بدهد و محصولِ مرجع (از متنِ echo یا آخرین پیام مشتری)
    در کاتالوگ پیدا شود، پیشنهادِ قیمت + مدرک `seller_echo` ثبت و محصول تأیید می‌شود.
    """
    if ev.kind != "echo_message" or not (ev.text and ev.text.strip()):
        return None

    from peyja.catalog_query import query_catalog
    from peyja.extract.caption import analyze_caption
    from peyja.extract.catalog import find_brand, find_product_name

    parsed = analyze_caption(ev.text)
    cand = parsed.prices.current
    if cand is None or cand.confidence < 0.8 or not cand.value_toman:
        return None

    search_text = ev.text
    has_product_cue = bool(find_brand(ev.text) or find_product_name(ev.text))
    if not has_product_cue and ev.account_id and ev.ig_user_id:
        conv = (
            await session.execute(
                select(Conversation).where(
                    Conversation.account_id == ev.account_id,
                    Conversation.ig_user_id == ev.ig_user_id,
                )
            )
        ).scalar_one_or_none()
        if conv is not None:
            last_in = (
                (
                    await session.execute(
                        select(Message)
                        .where(Message.conversation_id == conv.id, Message.direction == "in")
                        .order_by(Message.id.desc())
                        .limit(1)
                    )
                )
                .scalars()
                .first()
            )
            if last_in and last_in.text:
                search_text = f"{last_in.text} {ev.text}"

    matches = await query_catalog(session, q=search_text, limit=3)
    if not matches:
        return None

    target_id = int(matches[0]["id"])
    product = (
        await session.execute(select(Product).where(Product.id == target_id))
    ).scalar_one_or_none()
    if product is None:
        return None

    variant = (
        (await session.execute(select(Variant).where(Variant.product_id == product.id)))
        .scalars()
        .first()
    )
    if variant is None:
        variant = Variant(product_id=product.id, options=None)
        session.add(variant)
        await session.flush()

    offer = Offer(
        variant_id=variant.id,
        price_minor=int(cand.value_toman),
        stock_state="in_stock",
        valid_from=utcnow(),
        ttl_hours=24,
        stale=False,
    )
    session.add(offer)
    await session.flush()

    product.status = "confirmed"
    session.add(
        Evidence(
            offer_id=offer.id,
            product_id=product.id,
            source_type="seller_echo",
            source_id=ev.mid,
            method="echo_dm",
            confidence=0.95,
            verified_by=f"seller:{ev.account_id or 'echo'}",
            verified_at=utcnow(),
        )
    )
    _log.info(
        "💎 قیمت از echo فروشنده آموخته شد: product_id=%s ← %s تومان",
        product.id,
        cand.value_toman,
    )
    return {"product_id": product.id, "price_toman": int(cand.value_toman)}
