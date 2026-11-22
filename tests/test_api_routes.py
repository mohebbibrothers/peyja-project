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
