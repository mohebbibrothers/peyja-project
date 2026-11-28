"""طبقه‌بندی رویدادهای وب‌هوک + ثبت پیام‌ها — آماده‌سازی حلقه‌ی رسمی.

echo (پیام خودمان) ≠ پیام ورودی: echo سیگنال «بازخورد فروشنده» است
(قیمتی که فروشنده در چت می‌گوید = قیمت نهایی) و پنجره را باز نمی‌کند.
"""

from __future__ import annotations

import datetime as dt
import logging
import re
from dataclasses import dataclass
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from peyja.models import (
    Conversation,
    Evidence,
    Message,
    Offer,
    Page,
    Product,
    RawItem,
    Variant,
    utcnow,
)

EventKind = Literal["inbound_message", "echo_message", "comment", "action_result", "unknown"]
_log = logging.getLogger("peyja.dispatcher")

# نشانه‌های پیام خودِ ربات برای جلوگیری از حلقه‌ی بازخوردِ بی‌نهایت روی echo
_BOT_REPLY_MARKERS = (
    "قیمت تأییدشده‌ی ثبت‌شده",
    "قیمت به‌صورت استعلامی ثبت شده",
    "در انتظار تأیید نهایی فروشنده",
)
_ECHO_NON_PRODUCT_TOKENS = frozenset(
    {
        "سلام",
        "درود",
        "بله",
        "خیر",
        "ممنون",
        "مرسی",
        "لطفا",
        "لطفاً",
        "عزیزم",
        "مبارک",
        "تومان",
        "تومن",
        "هزار",
        "میلیون",
        "قیمت",
        "داریم",
        "دارید",
        "موجود",
        "موجوده",
        "است",
        "هست",
    }
)


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

    قانون صفر + ضدِ حلقه‌ی بازخورد:
    - پیام‌های خودکارِ خود ربات (شامل نشانگرهای قالب پاسخ) نادیده گرفته می‌شوند.
    - برای جلوگیری از ثبتِ تکراری، `source_id=ev.mid` یکتا نگه داشته می‌شود.
    - جست‌وجو بدون وجود واژه‌ی محصول/برند یا توکنِ غیرعددی انجام نمی‌شود تا «بله ۵۲۰» به کالای بی‌ربط نخورد.
    """
    if ev.kind != "echo_message" or not (ev.text and ev.text.strip()):
        return None
    if any(marker in ev.text for marker in _BOT_REPLY_MARKERS):
        return None
    if ev.mid:
        dup = (
            await session.execute(
                select(Evidence.id).where(
                    Evidence.source_type == "seller_echo", Evidence.source_id == ev.mid
                )
            )
        ).scalar_one_or_none()
        if dup is not None:
            return None

    from peyja.catalog_query import query_catalog
    from peyja.extract.caption import analyze_caption
    from peyja.extract.catalog import find_brand, find_product_name, fold

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

    # حتماً باید دست‌کم یک توکن غیرعددی/غیرتعارفی یا نام برند/محصول برای ارجاع وجود داشته باشد
    meaningful = [
        t
        for t in re.split(r"\s+", fold(search_text))
        if len(t) >= 2
        and not re.fullmatch(r"[\d۰-۹٠-٩.,/]+", t)
        and t not in _ECHO_NON_PRODUCT_TOKENS
    ]
    if not meaningful and not (find_brand(search_text) or find_product_name(search_text)):
        return None

    matches = await query_catalog(
        session, q=" ".join(meaningful) or search_text, include_rejected=False, limit=3
    )
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


async def ingest_official_posts_event(
    session: AsyncSession, payload: dict[str, Any]
) -> dict[str, Any] | None:
    """دریافت نتیجه `action.list_posts` از وب‌هوک رسمی و ساخت فوری کاتالوگ بدون نیاز به Data API."""
    if str(payload.get("event_type", "")) != "action.list_posts":
        return None
    account_id = str(payload.get("account_id") or "")
    data = payload.get("data") or {}
    if isinstance(data, list):
        posts = data
        username = ""
        ig_user_id = None
    elif isinstance(data, dict):
        posts = (
            data.get("posts") or data.get("media") or data.get("items") or data.get("data") or []
        )
        username = str(data.get("username") or "")
        ig_user_id = str(data.get("instagram_user_id") or data.get("user_id") or "") or None
    else:
        return None
    if not isinstance(posts, list) or not posts:
        return None

    page: Page | None = None
    if account_id:
        page = (
            await session.execute(select(Page).where(Page.account_id == account_id))
        ).scalar_one_or_none()
    if page is None and username:
        page = (
            await session.execute(select(Page).where(Page.username == username))
        ).scalar_one_or_none()
    if page is None:
        uname = username or f"official_{(account_id or 'page')[:12]}"
        page = Page(
            username=uname, ig_user_id=ig_user_id, source="official", account_id=account_id or None
        )
        session.add(page)
        await session.flush()

    archived = 0
    for idx, raw_post in enumerate(posts):
        if not isinstance(raw_post, dict):
            continue
        ig_id = str(
            raw_post.get("id")
            or raw_post.get("media_id")
            or raw_post.get("shortcode")
            or f"post-{idx}"
        )
        norm = dict(raw_post)
        if "caption" not in norm and "text" in norm:
            norm["caption"] = norm["text"]
        existing = (
            await session.execute(
                select(RawItem).where(
                    RawItem.page_id == page.id, RawItem.kind == "media", RawItem.ig_id == ig_id
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            existing.raw = norm
            existing.fetched_at = utcnow()
        else:
            session.add(RawItem(page_id=page.id, kind="media", ig_id=ig_id, raw=norm))
        archived += 1
    await session.flush()

    from peyja.extract.pipeline import ExtractionPipeline

    page.last_synced_at = utcnow()
    stats = await ExtractionPipeline(session, ai=None).run_for_page(page.id)
    _log.info("📥 پست‌های رسمی (@%s): %s پست ← %s محصول", page.username, archived, stats["products"])
    return {"page_id": page.id, "username": page.username, "archived": archived, **stats}
