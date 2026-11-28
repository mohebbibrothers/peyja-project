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
    """دستور ارسال پاسخ خودکار."""

    account_id: str
    recipient_id: str
    message: str


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


async def build_catalog_auto_reply(
    session: Any,
    ev: ClassifiedEvent,
    *,
    fallback_text: str,
    use_catalog: bool = False,
    state: WindowState = WindowState.OPEN_24H,
    human_agent_tag: bool = False,
) -> tuple[AutoReply | None, str | None]:
    """تولید پاسخ مدرک‌محور از کاتالوگ (A1 shadow draft + اختیاری در ارسال خودکار)."""
    if ev.kind != "inbound_message" or not (ev.account_id and ev.ig_user_id):
        return None, None
    allowed, _reason = can_send(state, human_agent_tag=human_agent_tag)
    if not allowed:
        return None, None

    draft_text: str | None = None
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
    """ارسال از مسیر API رسمی — خطا هرگز حلقه را نمی‌شکند (فقط لاگ)."""
    from peyja.provider.boxapi_official import BoxApiOfficialProvider

    provider = BoxApiOfficialProvider()
    try:
        log.info(
            "🤖 تلاش ارسال پاسخ خودکار → %s (account=%s)",
            reply.recipient_id,
            reply.account_id,
        )
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
