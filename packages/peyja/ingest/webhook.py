"""وب‌هوک BoxAPI/Sendbox — تأیید HMAC-SHA256 دقیقاً طبق مشخصات رسمی.

Signature = HMAC_SHA256(secret, "{timestamp}.{raw_body}")
هدرها: X-BoxApi-Signature, X-BoxApi-Timestamp, X-BoxApi-Event-Id
پنجره‌ی بازپخش: ۳۰۰ ثانیه. ⚠️ raw body — هرگز parse قبل از verify.
فرمت کلید: ممکن است با پیشوند whsec_ باشد (هر دو پذیرفته می‌شود).
"""

from __future__ import annotations

import hashlib
import hmac
import time
from collections.abc import Iterable
from dataclasses import dataclass


@dataclass
class WebhookVerification:
    ok: bool
    reason: str = ""


def _candidate_keys(secret: str) -> Iterable[str]:
    yield secret
    if secret.startswith("whsec_"):
        yield secret[6:]


def verify_boxapi_signature(
    secret: str,
    raw_body: bytes,
    signature_header: str,
    timestamp_header: str,
    tolerance_seconds: int = 300,
    now: float | None = None,
) -> WebhookVerification:
    """بررسی امن امضا — مقایسه‌ی زمان‌امن (timing-safe)."""
    if not signature_header or not timestamp_header:
        return WebhookVerification(False, "هدر امضا/تایم‌استمپ وجود ندارد")

    try:
        ts = int(timestamp_header)
    except ValueError:
        return WebhookVerification(False, "تایم‌استمپ نامعتبر")

    current = now if now is not None else time.time()
    if abs(current - ts) > tolerance_seconds:
        return WebhookVerification(
            False, f"تایم‌استمپ خارج از پنجره‌ی {tolerance_seconds} ثانیه‌ای (احتمال replay)"
        )

    message = f"{timestamp_header}.".encode() + raw_body
    sig = signature_header.strip().lower()

    for key in _candidate_keys(secret):
        expected = hmac.new(key.encode(), message, hashlib.sha256).hexdigest()
        if hmac.compare_digest(expected, sig):
            return WebhookVerification(True)
    return WebhookVerification(False, "امضا نامعتبر")


def sign_payload(secret: str, raw_body: bytes, timestamp: int | None = None) -> tuple[str, str]:
    """امضای سازگار با BoxAPI: HMAC_SHA256(secret, "{timestamp}.{raw_body}").

    برای self-test حلقه استفاده می‌شود؛ تأییدکننده هر دو شکل کلید (با/بدون whsec_) را می‌پذیرد.
    """
    ts = str(int(timestamp if timestamp is not None else time.time()))
    sig = hmac.new(secret.encode(), f"{ts}.".encode() + raw_body, hashlib.sha256).hexdigest()
    return ts, sig
