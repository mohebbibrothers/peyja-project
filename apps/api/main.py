"""API اصلی Peyja — FastAPI.

«سیستم‌عامل سمت عرضه» — فاز یک: ستون فقرات داده.
وب‌هوک روی چند مسیر مستقر است تا با هر تنظیم داشبورد سازگار باشد.
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from apps.api.callback import router as callback_router
from apps.api.webhook import boxapi_webhook, boxapi_webhook_health
from apps.api.webhook import router as webhook_router

# لاگ عملیاتی: بدون این دو خط، سطح INFO زیر آستانه‌ی پیش‌فرض می‌ماند و تصمیمات
# حلقه‌ی پاسخ در لاگ میزبان نامرئی می‌شود (تجربه‌ی تلخِ دیباگ ۱۴۰۵/۰۹/۰۵).
if not logging.getLogger().handlers:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
logging.getLogger("peyja").setLevel(logging.INFO)


@asynccontextmanager
async def _lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """برای استقرارهای ASGI (uvicorn). تحت Passenger/a2wsgi اجرا نمی‌شود —
    آنجا مسیر وب‌هوک خودش schema را می‌سازد (_ensure_schema_once)."""
    from peyja.db import ensure_schema

    await ensure_schema()
    yield


app = FastAPI(
    title="Peyja — Supply-Side OS",
    version="0.2.0",
    description="Data Spine for Agentic Commerce · لوازم آرایشی بهداشتی",
    lifespan=_lifespan,
)
app.include_router(webhook_router, prefix="/webhooks")
app.include_router(webhook_router, prefix="/webhook")  # alias
app.include_router(callback_router)  # بازگشت پس از ورود رسمی اینستاگرام

# مسیرهای سازگار با تنظیمات رایج داشبورد
app.add_api_route("/webhook", boxapi_webhook, methods=["POST"], include_in_schema=False)
app.add_api_route("/webhook/", boxapi_webhook_health, methods=["GET"], include_in_schema=False)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "service": "peyja-spine"}


@app.get("/")
async def root() -> dict[str, str]:
    return {"service": "peyja", "docs": "/docs", "webhook": "/webhooks/boxapi"}
