"""تست‌های موتور پاسخ خودکار P0."""

from peyja.loop.dispatcher import ClassifiedEvent
from peyja.loop.reply import build_auto_reply
from peyja.loop.windows import WindowState

TEXT = "سلام! پیام شما دریافت شد."


def test_auto_reply_built_for_inbound_message():
    ev = ClassifiedEvent("inbound_message", account_id="acc-1", ig_user_id="user-9", text="سلام")
    reply = build_auto_reply(ev, auto_reply_text=TEXT)
    assert reply is not None
    assert reply.account_id == "acc-1"
    assert reply.recipient_id == "user-9"
    assert reply.message == TEXT


def test_no_auto_reply_for_echo_unknown_or_empty_text():
    echo = ClassifiedEvent("echo_message", account_id="acc-1", ig_user_id="user-9")
    assert build_auto_reply(echo, auto_reply_text=TEXT) is None

    unknown = ClassifiedEvent("unknown", account_id="acc-1", ig_user_id="user-9")
    assert build_auto_reply(unknown, auto_reply_text=TEXT) is None

    inbound = ClassifiedEvent("inbound_message", account_id="acc-1", ig_user_id="user-9")
    assert build_auto_reply(inbound, auto_reply_text="") is None


def test_no_auto_reply_when_window_closed():
    ev = ClassifiedEvent("inbound_message", account_id="acc-1", ig_user_id="user-9")
    assert build_auto_reply(ev, auto_reply_text=TEXT, state=WindowState.CLOSED) is None
