"""تست‌های قوانین پنجره‌ی پیام‌رسان متا."""

import datetime as dt

from peyja.loop.windows import WindowState, can_send, window_state

NOW = dt.datetime(2026, 11, 21, 12, 0, tzinfo=dt.UTC)


def _ago(hours: float) -> dt.datetime:
    return NOW - dt.timedelta(hours=hours)


def test_fresh_inbound_opens_24h_window():
    assert window_state(_ago(1), NOW) == WindowState.OPEN_24H
    ok, _ = can_send(WindowState.OPEN_24H)
    assert ok


def test_after_24h_one_reply_allowed():
    assert window_state(_ago(30), NOW) == WindowState.REPLY_7D
    ok, _ = can_send(WindowState.REPLY_7D)
    assert ok


def test_reply_7d_consumed_needs_human_agent():
    assert window_state(_ago(30), NOW, reply_7d_used=True) == WindowState.HUMAN_AGENT
    ok, _reason = can_send(WindowState.HUMAN_AGENT)
    assert not ok
    ok2, _ = can_send(WindowState.HUMAN_AGENT, human_agent_tag=True)
    assert ok2


def test_after_7d_closed():
    assert window_state(_ago(24 * 8), NOW) == WindowState.CLOSED
    ok, _ = can_send(WindowState.CLOSED)
    assert not ok


def test_no_inbound_ever_closed():
    assert window_state(None, NOW) == WindowState.CLOSED
