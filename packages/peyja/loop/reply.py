"""پاسخ خودکار اولیه (P0) — با رعایت سخت‌گیرانه‌ی قوانین پنجره‌ی متا.

- echo (پیام خودمان) هرگز پاسخ نمی‌گیرد (echo = بازخورد فروشنده است، نه مشتری).
- پنجره‌ی بسته ⇒ بدون پاسخ (طبق windows.can_send).
- auto_reply_text خالی ⇒ موتور غیرفعال.
- ارسال همیشه از مسیر provider رسمی (BoxApiOfficialProvider)، هرگز مستقیم.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from peyja.loop.dispatcher import ClassifiedEvent
from peyja.loop.windows import WindowState, can_send

log = logging.getLogger("peyja.autoreply")


@dataclass(frozen=True)
class AutoReply:
    """دستور ارسال پاسخ خودکار (DM یا private_reply روی کامنت)."""

    account_id: str
    recipient_id: str
    message: str
    comment_id: str | None = None


def build_catalog_cards(items: list[dict[str, Any]], max_cards: int = 5) -> list[dict[str, Any]]:
    """ساخت کارت محصول برای send_generic_template با رعایت سخت‌گیرانه‌ی قانون صفر."""
    cards: list[dict[str, Any]] = []
    for it in items[:max_cards]:
        title = (it.get("title_fa") or it.get("product_name") or "محصول")[:80]
        if it.get("verified") and it.get("price_toman"):
            price_txt = f"{int(it['price_toman']):,} تومان ✓"
        elif it.get("stock_state") == "on_request":
            price_txt = "قیمت استعلامی در دایرکت"
        else:
            price_txt = "در انتظار تأیید فروشنده"
        brand_txt = f"برند {it['brand']} · " if it.get("brand") else ""
        cards.append({"title": title, "subtitle": f"{brand_txt}{price_txt}"[:80]})
    return cards


def build_auto_reply(
    ev: ClassifiedEvent,
    *,
    auto_reply_text: str,
    state: WindowState = WindowState.OPEN_24H,
    human_agent_tag: bool = False,
) -> AutoReply | None:
    """اگر پاسخ خودکار لازم و مجاز است، دستور ارسال را بسازد؛ وگرنه None."""
    if not auto_reply_text:
        return None
    if ev.kind != "inbound_message" or not (ev.account_id and ev.ig_user_id):
        return None
    allowed, _reason = can_send(state, human_agent_tag=human_agent_tag)
    if not allowed:
        return None
    return AutoReply(ev.account_id, ev.ig_user_id, auto_reply_text)


_COMMENT_INQUIRY_RE = ("قیمت", "چنده", "موجود", "سفارش", "شرایط")


async def build_catalog_auto_reply(
    session: Any,
    ev: ClassifiedEvent,
    *,
    fallback_text: str,
    use_catalog: bool = False,
    state: WindowState = WindowState.OPEN_24H,
    human_agent_tag: bool = False,
) -> tuple[AutoReply | None, str | None]:
    """تولید پاسخ مدرک‌محور از کاتالوگ (DM + private_reply روی کامنت)."""
    if ev.kind == "comment":
        if not (ev.account_id and ev.mid):
            return None, None
        draft_text: str | None = None
        if ev.text and ev.text.strip():
            from peyja.catalog_query import answer_product_query

            ans = await answer_product_query(session, ev.text)
            if ans.get("matched"):
                draft_text = ans.get("reply_text")
            elif any(w in ev.text for w in _COMMENT_INQUIRY_RE):
                draft_text = fallback_text or (
                    "سلام 🌸 پیام شما ثبت شد؛ برای استعلام قیمت دقیق و موجودی همینجا در دایرکت پاسخ می‌دهیم."
                )
        if use_catalog and draft_text:
            return (
                AutoReply(
                    account_id=ev.account_id,
                    recipient_id=ev.ig_user_id or ev.mid,
                    message=draft_text,
                    comment_id=ev.mid,
                ),
                draft_text,
            )
        return None, draft_text

    if ev.kind != "inbound_message" or not (ev.account_id and ev.ig_user_id):
        return None, None
    allowed, _reason = can_send(state, human_agent_tag=human_agent_tag)
    if not allowed:
        return None, None

    draft_text = None
    if ev.text and ev.text.strip():
        from peyja.catalog_query import answer_product_query

        ans = await answer_product_query(session, ev.text)
        if ans.get("matched"):
            draft_text = ans.get("reply_text")

    final_text = (draft_text if use_catalog and draft_text else None) or fallback_text
    if not final_text:
        return None, draft_text
    return AutoReply(ev.account_id, ev.ig_user_id, final_text), draft_text


async def send_auto_reply(reply: AutoReply) -> bool:
    """ارسال از مسیر API رسمی (DM یا private_reply کامنت) — خطا هرگز حلقه را نمی‌شکند."""
    from peyja.provider.boxapi_official import BoxApiOfficialProvider

    provider = BoxApiOfficialProvider()
    try:
        log.info(
            "🤖 تلاش ارسال پاسخ خودکار → %s (account=%s comment=%s)",
            reply.recipient_id,
            reply.account_id,
            reply.comment_id or "-",
        )
        if reply.comment_id:
            result = await provider.private_reply(
                account_id=reply.account_id,
                comment_id=reply.comment_id,
                message=reply.message,
            )
        else:
            result = await provider.send_message(
                account_id=reply.account_id,
                recipient_id=reply.recipient_id,
                message=reply.message,
            )
        log.info("پاسخ خودکار ارسال شد → %s (%s)", reply.recipient_id, result.get("accepted"))
        return True
    except Exception as e:
        log.warning("ارسال پاسخ خودکار ناموفق: %s", e)
        return False
    finally:
        await provider.aclose()
