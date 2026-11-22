"""تست‌های مسیرهای عمومی API — سلامت، پروب وب‌هوک، بازگشت ورود."""

import json

from fastapi.testclient import TestClient

from apps.api.callback import record_callback
from apps.api.main import app

client = TestClient(app)


def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["service"] == "peyja-spine"


def test_webhook_get_probe():
    """برخی داشبوردها هنگام ثبت URL با GET آن را بررسی می‌کنند."""
    r = client.get("/webhooks/boxapi")
    assert r.status_code == 200
    assert r.json()["endpoint"] == "boxapi-webhook"


def test_login_callback_records_raw_params(tmp_path):
    p = tmp_path / "login_callbacks.jsonl"
    record_callback({"token": "abc", "page": "test"}, p)
    row = json.loads(p.read_text(encoding="utf-8").strip().splitlines()[-1])
    assert row["params"]["token"] == "abc"


def test_login_callback_page():
    r = client.get("/boxapi/callback", params={"code": "xyz"})
    assert r.status_code == 200
    assert "اتصال اینستاگرام انجام شد" in r.text


def test_webhook_self_heals_on_fresh_db(tmp_path, monkeypatch):
    """رگرسیون 500 «no such table»: وب‌هوک باید schema را خودش بسازد.

    نکته: TestClient بدون context manager lifespan را اجرا نمی‌کند — دقیقاً
    همان شرایط Passenger/a2wsgi (بدون lifespan) شبیه‌سازی می‌شود.
    """
    import time

    import peyja.config as cfg
    import peyja.db as dbmod
    from peyja.ingest.webhook import sign_payload

    monkeypatch.setenv("DB_PATH", str(tmp_path / "fresh.db"))
    monkeypatch.setenv("BOXAPI_WEBHOOK_SECRET", "whsec_regression")
    monkeypatch.delenv("AUTO_REPLY_TEXT", raising=False)
    cfg.get_settings.cache_clear()
    dbmod._engine = None
    dbmod._session_factory = None
    try:
        now_ms = int(time.time() * 1000)
        event_id = f"reg-{now_ms}"
        # شکل واقعی طبق داک رسمی: پیام‌ها داخل آرایه‌ی data.messaging[] + امضای sha256=
        body = {
            "event_id": event_id,
            "event_type": "messaging",
            "account_id": "acc",
            "data": {
                "time": now_ms,
                "id": "page",
                "messaging": [
                    {
                        "sender": {"id": "u1"},
                        "recipient": {"id": "page"},
                        "timestamp": now_ms,
                        "message": {"mid": event_id, "text": "سلام"},
                    }
                ],
            },
        }
        raw = json.dumps(body, ensure_ascii=False).encode()
        ts, sig = sign_payload("whsec_regression", raw)
        r = client.post(
            "/webhooks/boxapi",
            content=raw,
            headers={
                "Content-Type": "application/json",
                "X-BoxApi-Signature": f"sha256={sig}",
                "X-BoxApi-Timestamp": ts,
                "X-BoxApi-Event-Id": event_id,
            },
        )
        assert r.status_code == 200
        assert r.json()["status"] == "accepted"

        # پیام باید از دل انولوپ استخراج و ذخیره شده باشد (نه فقط رویداد)
        import asyncio

        from sqlalchemy import select

        from peyja.models import Message

        factory = dbmod.get_session_factory()

        async def _messages():
            async with factory() as s:
                return (await s.execute(select(Message))).scalars().all()

        msgs = asyncio.run(_messages())
        assert any(m.text == "سلام" and m.direction == "in" for m in msgs)

        # ig_user_id باید از دل انولوپ استخراج و روی مکالمه ثبت شده باشد
        from peyja.models import Conversation

        async def _convs():
            async with factory() as s:
                return (await s.execute(select(Conversation))).scalars().all()

        convs = asyncio.run(_convs())
        assert any(c.ig_user_id == "u1" for c in convs)
    finally:
        cfg.get_settings.cache_clear()
        dbmod._engine = None
        dbmod._session_factory = None
