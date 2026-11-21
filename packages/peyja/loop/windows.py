"""قوانین پنجره‌ی پیام‌رسانی متا (از مستندات رسمی + خطاهای BoxAPI).

قواعد (نقض = خطای ۱۰ یا ۲۰ دقیقه‌ای/مسدودی موقت):
- پنجره‌ی ۲۴ ساعته: پس از هر پیام ورودی کاربر، تا ۲۴h می‌توان آزادانه پیام داد.
- پاسخ ۷ روزه (reply tag): خارج از ۲۴h — فقط یک پاسخ به هر پیام ورودی.
- Human Agent: تا ۷ روز با تگ ویژه (سهمیه‌ای) — برای پشتیبانی انسانی.
- پیام‌های echo (پیام‌های خودمان) پنجره را باز نمی‌کنند — فقط سیگنال بازخوردند.
"""

from __future__ import annotations

import datetime as dt
from enum import StrEnum

WINDOW_24H = dt.timedelta(hours=24)
WINDOW_7D = dt.timedelta(days=7)


class WindowState(StrEnum):
    OPEN_24H = "open_24h"  # ارسال آزاد
    REPLY_7D = "reply_7d"  # فقط یک پاسخ (تگ reply) — یا Human Agent
    HUMAN_AGENT = "human_agent"  # فقط با تگ Human Agent
    CLOSED = "closed"  # هیچ ارسالی مجاز نیست


def window_state(
    last_inbound_at: dt.datetime | None, now: dt.datetime, reply_7d_used: bool = False
) -> WindowState:
    """وضعیت پنجره‌ی گفتگو را تعیین می‌کند."""
    if last_inbound_at is None:
        return WindowState.CLOSED
    delta = now - last_inbound_at
    if delta <= WINDOW_24H:
        return WindowState.OPEN_24H
    if delta <= WINDOW_7D:
        return WindowState.HUMAN_AGENT if reply_7d_used else WindowState.REPLY_7D
    return WindowState.CLOSED


def can_send(state: WindowState, *, human_agent_tag: bool = False) -> tuple[bool, str]:
    """آیا ارسال مجاز است؟ (مجاز؟، دلیل/راه‌حل)"""
    if state == WindowState.OPEN_24H:
        return True, "پنجره‌ی ۲۴ ساعته باز است"
    if state == WindowState.REPLY_7D:
        return True, "پاسخ ۷ روزه (تگ reply) — فقط یک پاسخ"
    if state == WindowState.HUMAN_AGENT:
        if human_agent_tag:
            return True, "تگ Human Agent"
        return False, "خارج از ۲۴ ساعت و پاسخ ۷ روزه مصرف شده — فقط با تگ Human Agent"
    return False, "پنجره بسته است — منتظر پیام بعدی کاربر بمانید"
