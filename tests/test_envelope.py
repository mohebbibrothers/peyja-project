"""رگرسیون قرارداد رسمی BoxAPI — انولوپ data.messaging[] و پیشوند sha256=.

منبع: https://boxapi.ir/docs/instagram/webhook/
سه باگ واقعی که فقط با پیام‌های حقیقی ظاهر شدند (selftest قبلی شکل تختِ
دست‌ساز داشت و فقط خودش را تأیید می‌کرد):
1. پیام‌ها داخل آرایه‌ی data.messaging[] می‌آیند، نه data.sender/data.message.
2. هدر امضا شامل پیشوند «sha256=» است.
3. اگر Webhook Secret در پنل ست نشده باشد، BoxAPI اصلاً هدر امضا نمی‌فرستد.
"""

import hashlib
import hmac

from peyja.ingest.webhook import verify_boxapi_signature
from peyja.loop.dispatcher import classify_events


def _envelope(items: list[dict]) -> dict:
    return {
        "event_id": "client_ig_1",
        "event_type": "messaging",
        "account_id": "acc-1",
        "data": {"time": 1785081317446, "id": "page-1", "messaging": items},
    }


def test_envelope_single_message():
    payload = _envelope(
        [
            {
                "sender": {"id": "user-9"},
                "recipient": {"id": "page-1"},
                "timestamp": 1785081317081,
                "message": {"mid": "m1", "text": "سلام"},
            }
        ]
    )
    events = classify_events(payload)
    assert len(events) == 1
    ev = events[0]
    assert ev.kind == "inbound_message"
    assert ev.ig_user_id == "user-9"
    assert ev.text == "سلام"
    assert ev.mid == "m1"


def test_envelope_multi_items_and_echo():
    payload = _envelope(
        [
            {
                "sender": {"id": "u1"},
                "recipient": {"id": "page-1"},
                "message": {"mid": "a", "text": "پیام اول"},
            },
            {
                "sender": {"id": "page-1"},
                "recipient": {"id": "u1"},
                "message": {"mid": "b", "text": "پاسخ من", "is_echo": True},
            },
        ]
    )
    events = classify_events(payload)
    assert [e.kind for e in events] == ["inbound_message", "echo_message"]
    # معناشناسی ig_user_id = «کاربر مقابل»؛ در echo = گیرنده (نه فرستنده‌ی صفحه)
    assert events[1].ig_user_id == "u1"


def test_envelope_postback_title():
    payload = {
        "event_id": "client_ig_2",
        "event_type": "messaging_postbacks",
        "account_id": "acc-1",
        "data": {
            "messaging": [
                {
                    "sender": {"id": "u1"},
                    "recipient": {"id": "page-1"},
                    "postback": {"title": "قیمت"},
                }
            ]
        },
    }
    ev = classify_events(payload)[0]
    assert ev.kind == "inbound_message"
    assert ev.text == "قیمت"


def test_flat_shape_still_supported():
    """سازگاری با شکل تخت قدیمی (جمع‌آوری‌کننده‌های داخلی)."""
    payload = {
        "event_type": "messaging",
        "account_id": "acc",
        "data": {
            "sender": {"id": "u1"},
            "recipient": {"id": "p"},
            "message": {"mid": "m", "text": "سلام"},
            "timestamp": 1763726400000,
        },
    }
    ev = classify_events(payload)[0]
    assert ev.kind == "inbound_message"
    assert ev.ig_user_id == "u1"


def test_message_echoes_type_classified_as_echo():
    """نوعِ واقعی message_echoes (تجربه‌ی هاست ۱۴۰۵/۰۹/۰۵): همه echo اند؛
    طرف مقابل = گیرنده، نه صفحه."""
    payload = {
        "event_id": "client_ig_3",
        "event_type": "message_echoes",
        "account_id": "acc-1",
        "data": {
            "messaging": [
                {
                    "sender": {"id": "page-1"},
                    "recipient": {"id": "u1"},
                    "message": {"mid": "m9", "text": "سلام! پیام شما ثبت شد"},
                }
            ]
        },
    }
    events = classify_events(payload)
    assert events[0].kind == "echo_message"
    assert events[0].ig_user_id == "u1"
    assert events[0].text == "سلام! پیام شما ثبت شد"


def test_signature_with_sha256_prefix_accepted():
    """فرمت رسمی BoxAPI: هدر امضا = «sha256=» + hex."""
    body = b'{"event_type":"messaging"}'
    ts = "1770000000"
    message = f"{ts}.".encode() + body
    hexsig = hmac.new(b"whsec_test_secret", message, hashlib.sha256).hexdigest()
    result = verify_boxapi_signature(
        secret="whsec_test_secret",
        raw_body=body,
        signature_header="sha256=" + hexsig,
        timestamp_header=ts,
        now=1_770_000_000.0,
    )
    assert result.ok


def test_signature_bare_hex_still_accepted():
    """سازگاری: هگز خالی (بدون پیشوند) هم پذیرفته می‌شود."""
    body = b'{"event_type":"messaging"}'
    ts = "1770000000"
    message = f"{ts}.".encode() + body
    hexsig = hmac.new(b"whsec_test_secret", message, hashlib.sha256).hexdigest()
    result = verify_boxapi_signature(
        secret="whsec_test_secret",
        raw_body=body,
        signature_header=hexsig,
        timestamp_header=ts,
        now=1_770_000_000.0,
    )
    assert result.ok


def test_no_secret_accepts_unsigned_per_docs():
    """داک رسمی: secret اختیاری — بدون secret، رویداد بدون امضا باید پذیرفته شود."""
    result = verify_boxapi_signature(
        secret="",
        raw_body=b"{}",
        signature_header="",
        timestamp_header="",
    )
    assert result.ok

    # اما وقتی secret ست است، نبود هدر امضا = 401
    rejected = verify_boxapi_signature(
        secret="whsec_test_secret",
        raw_body=b"{}",
        signature_header="",
        timestamp_header="",
    )
    assert not rejected.ok
