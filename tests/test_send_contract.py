"""رگرسیون قرارداد ارسال — بدنه‌ی «تخت» + ابزارهای دیباگ.

درس ۱۴۰۵/۰۹/۰۵: پیچیدن payload در {"data": ...} ⇒ BoxAPI با 202ِ «در صف رفت»
می‌پذیرفت، ولی ورکر async با «recipient_id and message are required» رد می‌کرد
(خطای دیده‌شده در پنل). مستند رسمی boxapi.ir بدنه‌ی تخت می‌خواهد:
{"account_id":..., "recipient_id":..., "message":...}.
پروب زنده: تخت ⇒ 422 اعتبارسنجی فوری فیلدها (شکل درست)؛ پیچیده ⇒ 202 دروغین.
"""

import asyncio
import json

import httpx
import pytest

from peyja.provider.boxapi_official import BoxApiOfficialProvider, OfficialApiError


def _provider_with_capture(response: dict, status_code: int):
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(request.content)
        captured["path"] = request.url.path
        captured["headers"] = dict(request.headers)
        return httpx.Response(status_code, json=response)

    provider = BoxApiOfficialProvider(api_key="k-test", base_url="https://x.test/api/v1")
    provider._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return provider, captured


def test_send_message_body_is_flat() -> None:
    provider, captured = _provider_with_capture(
        {
            "success": True,
            "message": "Action accepted and queued for processing",
            "status_code": 202,
            "data": {"accepted": True, "action": "send_message", "event_id": "e1"},
        },
        202,
    )
    result = asyncio.run(provider.send_message("acc-1", "user-9", "سلام"))
    assert result["accepted"] is True
    body = captured["body"]
    # قرارداد رسمی: فیلدها در سطح بالا — نه داخل «data»
    assert body == {"account_id": "acc-1", "recipient_id": "user-9", "message": "سلام"}
    assert captured["path"].endswith("/service/actions/send_message")
    assert captured["headers"]["x-api-key"] == "k-test"


def test_action_error_carries_field_errors() -> None:
    provider, _ = _provider_with_capture(
        {
            "success": False,
            "message": "The selected account id is invalid.",
            "status_code": 422,
            "data": None,
            "errors": {"account_id": ["The selected account id is invalid."]},
        },
        422,
    )
    with pytest.raises(OfficialApiError) as ei:
        asyncio.run(provider.send_message("bad", "u", "t"))
    assert "invalid" in str(ei.value)
    assert ei.value.errors is not None


def test_events_cli_smoke(tmp_path, monkeypatch) -> None:
    """ابزار دیباگ peyja events باید اجرا شود (حتی روی دیتابیس خالی)."""
    import peyja.config as cfg
    import peyja.db as dbmod

    monkeypatch.setenv("DB_PATH", str(tmp_path / "diag.db"))
    monkeypatch.delenv("AUTO_REPLY_TEXT", raising=False)
    cfg.get_settings.cache_clear()
    dbmod._engine = None
    dbmod._session_factory = None
    try:
        from typer.testing import CliRunner

        from apps.cli.main import app

        r = CliRunner().invoke(app, ["events", "-n", "3"])
        assert r.exit_code == 0, r.output
        assert "رویداد وب‌هوک" in r.output
        assert "هیچ رویدادی ثبت نشده" in r.output
    finally:
        cfg.get_settings.cache_clear()
        dbmod._engine = None
        dbmod._session_factory = None
