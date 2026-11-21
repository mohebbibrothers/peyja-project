"""تاریخچه‌ی قیمت = یک دارایی داده‌ای — «هشدار افت قیمت رقیب».

از نسخه‌های کپشن (CaptionVersion)، رویدادهای تغییر قیمت استخراج می‌شود:
کاهش قیمت، افزایش قیمت، شروع/پایان تخفیف. این همان چیزی است که فروشنده
حاضر است برایش پول بدهد: «رقیب ارزان کرد، خبرم کن».
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from peyja.extract.price_parser import parse_price_fa


@dataclass
class PriceChange:
    at: datetime
    old_price: int | None
    new_price: int | None
    kind: str  # drop | rise | sale_start | sale_end | first_seen

    @property
    def delta_pct(self) -> float | None:
        if self.old_price and self.new_price:
            return round((self.new_price - self.old_price) / self.old_price * 100, 1)
        return None


def detect_price_changes(
    versions: list[tuple[datetime, str | None]],
) -> list[PriceChange]:
    """نسخه‌های (زمان، کپشن) مرتب‌شده → رویدادهای تغییر قیمت."""
    events: list[PriceChange] = []
    prev_price: int | None = None
    prev_old: int | None = None

    for at, caption in sorted(versions, key=lambda v: v[0]):
        r = parse_price_fa(caption or "")
        cur = r.current.value_toman if r.current else None
        old = r.old.value_toman if r.old else None

        if prev_price is None:
            if cur is not None:
                events.append(PriceChange(at, None, cur, "first_seen"))
        elif cur != prev_price and cur is not None and prev_price is not None:
            if cur < prev_price:
                kind = "sale_start" if old else "drop"
            else:
                kind = "sale_end" if (prev_old is not None and not old) else "rise"
            events.append(PriceChange(at, prev_price, cur, kind))
        prev_price, prev_old = cur, old

    return events
