"""تست‌های تاریخچه‌ی قیمت — «هشدار افت قیمت رقیب»."""

import datetime as dt

from peyja.extract.price_history import detect_price_changes


def _t(day: int) -> dt.datetime:
    return dt.datetime(2026, 11, day, tzinfo=dt.UTC)


def test_first_seen_and_drop():
    events = detect_price_changes(
        [
            (_t(1), "کرم ضد آفتاب ۴/۸۰"),
            (_t(5), "کرم ضد آفتاب ۴/۲۰"),  # افت قیمت
        ]
    )
    assert events[0].kind == "first_seen"
    assert events[1].kind == "drop"
    assert events[1].delta_pct == -12.5


def test_sale_start_detected():
    events = detect_price_changes(
        [
            (_t(1), "رژلب ۹۰۰"),
            (_t(2), "تخفیف از ۹۰۰ به ۷۵۰"),  # شروع تخفیف
        ]
    )
    kinds = [e.kind for e in events]
    assert "sale_start" in kinds


def test_no_change_no_event():
    events = detect_price_changes(
        [
            (_t(1), "قیمت ۴۸۰"),
            (_t(2), "قیمت ۴۸۰"),
        ]
    )
    assert len(events) == 1  # فقط first_seen
