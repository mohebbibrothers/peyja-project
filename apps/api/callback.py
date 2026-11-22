"""بازگشت پس از ورود رسمی اینستاگرام (Login Redirect) — ثبت خام + صفحه‌ی تأیید.

قرارداد جریان login-link: کاربر پس از ورود به اینستاگرام، به «آدرس بازگشت پس از ورود»
تنظیم‌شده در پنل BoxAPI بازمی‌گردد (ممکن است توکن/کد در query باشد). اینجا همه‌چیز
خام (JSONL) ثبت می‌شود تا هیچ داده‌ای از دست نرود؛ نتیجه‌ی نهایی اتصال از طریق
وب‌هوک می‌آید (الگوی async خودِ BoxAPI).
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

log = logging.getLogger("peyja.login")

router = APIRouter(tags=["login"])

_DONE_PAGE = """<!doctype html>
<html lang="fa" dir="rtl">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>اتصال انجام شد — پیجا</title></head>
<body style="margin:0;font-family:Tahoma,sans-serif;background:#0f766e;color:#fff;
display:flex;align-items:center;justify-content:center;height:100vh">
<div style="text-align:center;background:rgba(255,255,255,.12);padding:38px 46px;border-radius:18px">
<div style="font-size:64px">✅</div>
<h1 style="margin:12px 0 6px;font-size:24px">اتصال اینستاگرام انجام شد!</h1>
<p style="margin:0;opacity:.92">پیجا در حال ثبت اتصال شماست؛ می‌توانید این پنجره را ببندید<br>
و به پنل مدیریت بازگردید.</p>
</div></body></html>"""


def record_callback(params: dict[str, Any], path: Path) -> None:
    """ثبت خام پارامترهای بازگشت — خطاهای ذخیره‌سازی هرگز صفحه را نمی‌شکنند."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"ts": time.time(), "params": params}, ensure_ascii=False) + "\n")


def default_record_path() -> Path:
    from peyja.config import get_settings

    return Path(get_settings().db_path).parent / "login_callbacks.jsonl"


@router.get("/boxapi/callback", response_class=HTMLResponse, include_in_schema=False)
async def boxapi_callback(request: Request) -> HTMLResponse:
    params = dict(request.query_params)
    try:
        record_callback(params, default_record_path())
        log.info("login callback: %s", list(params))
    except Exception:
        log.warning("login callback record failed", exc_info=True)
    return HTMLResponse(_DONE_PAGE)
