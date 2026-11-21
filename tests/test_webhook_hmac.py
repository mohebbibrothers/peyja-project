"""تست‌های امنیتی وب‌هوک — HMAC-SHA256 طبق مشخصات BoxAPI."""

import hashlib
import hmac
import time

from peyja.ingest.webhook import verify_boxapi_signature

SECRET = "test-secret-123"


def _sign(raw: bytes, ts: str, secret: str = SECRET) -> str:
    message = f"{ts}.".encode() + raw
    return hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def test_valid_signature_accepted():
    raw = b'{"event_type":"messaging","data":{}}'
    ts = str(int(time.time()))
    sig = _sign(raw, ts)
    result = verify_boxapi_signature(SECRET, raw, sig, ts)
    assert result.ok


def test_tampered_body_rejected():
    raw = b'{"event_type":"messaging"}'
    ts = str(int(time.time()))
    sig = _sign(raw, ts)
    tampered = b'{"event_type":"comments"}'
    result = verify_boxapi_signature(SECRET, tampered, sig, ts)
    assert not result.ok


def test_wrong_secret_rejected():
    raw = b'{"a":1}'
    ts = str(int(time.time()))
    sig = _sign(raw, ts, secret="wrong-secret")
    result = verify_boxapi_signature(SECRET, raw, sig, ts)
    assert not result.ok


def test_replay_window_rejected():
    """تایم‌استمپ قدیمی‌تر از ۳۰۰ ثانیه باید رد شود (حمله‌ی replay)."""
    raw = b'{"a":1}'
    old_ts = str(int(time.time()) - 400)
    sig = _sign(raw, old_ts)
    result = verify_boxapi_signature(SECRET, raw, sig, old_ts)
    assert not result.ok
    assert (
        "بازپخش" in result.reason or "replay" in result.reason.lower() or "پنجره" in result.reason
    )


def test_missing_headers_rejected():
    result = verify_boxapi_signature(SECRET, b"{}", "", "")
    assert not result.ok


def test_invalid_timestamp_rejected():
    raw = b'{"a":1}'
    sig = _sign(raw, "not-a-number")
    result = verify_boxapi_signature(SECRET, raw, sig, "not-a-number")
    assert not result.ok


def test_signature_case_insensitive():
    raw = b'{"a":1}'
    ts = str(int(time.time()))
    sig = _sign(raw, ts).upper()
    result = verify_boxapi_signature(SECRET, raw, sig, ts)
    assert result.ok


def test_whsec_prefixed_secret_accepted():
    """فرمت رایج whsec_ — کلید با/بدون پیشوند هر دو پذیرفته می‌شوند."""
    raw = b'{"event_type":"messaging"}'
    ts = str(int(time.time()))
    sig = _sign(raw, ts)  # امضا با کلید بدون پیشوند
    result = verify_boxapi_signature("whsec_" + SECRET, raw, sig, ts)
    assert result.ok
