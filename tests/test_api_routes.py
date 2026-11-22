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
        body = {
            "event_type": "messaging",
            "account_id": "acc",
            "data": {
                "sender": {"id": "u1"},
                "recipient": {"id": "page"},
                "message": {"mid": event_id, "text": "سلام"},
                "timestamp": now_ms,
            },
        }
        raw = json.dumps(body, ensure_ascii=False).encode()
        ts, sig = sign_payload("whsec_regression", raw)
        r = client.post(
            "/webhooks/boxapi",
            content=raw,
            headers={
                "Content-Type": "application/json",
                "X-BoxApi-Signature": sig,
                "X-BoxApi-Timestamp": ts,
                "X-BoxApi-Event-Id": event_id,
            },
        )
        assert r.status_code == 200
        assert r.json()["status"] == "accepted"
    finally:
        cfg.get_settings.cache_clear()
        dbmod._engine = None
        dbmod._session_factory = None
