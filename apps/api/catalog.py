"""مسیرهای کاتالوگ ایجنت‌فهم + ویترین وب (UCP / Agent-Native Surface)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query
from fastapi.responses import HTMLResponse

from peyja.catalog_query import answer_product_query, query_catalog, render_storefront_html
from peyja.db import ensure_schema, get_session_factory

router = APIRouter(tags=["catalog"])


@router.get("/api/catalog")
async def get_catalog_json(
    q: str = Query(default="", description="عبارت جستجو (نام محصول یا برند)"),
    category: str | None = Query(default=None, description="فیلتر دسته"),
    confirmed_only: bool = Query(default=False, description="فقط اقلام تأییدشده"),
    limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, Any]:
    await ensure_schema()
    factory = get_session_factory()
    async with factory() as session:
        items = await query_catalog(
            session, q=q, category=category, confirmed_only=confirmed_only, limit=limit
        )
    return {
        "schema_version": "ucp-peyja-v0.2",
        "count": len(items),
        "items": items,
    }


@router.get("/api/catalog/ask")
async def ask_catalog(q: str = Query(..., min_length=1)) -> dict[str, Any]:
    """پاسخ مدرک‌محور به سؤال خریدار (قانون صفر: بدون ادعای قیمت تأییدنشده)."""
    await ensure_schema()
    factory = get_session_factory()
    async with factory() as session:
        return await answer_product_query(session, q)


@router.get("/catalog", response_class=HTMLResponse)
async def storefront_page(
    q: str = Query(default=""),
    category: str | None = Query(default=None),
) -> HTMLResponse:
    await ensure_schema()
    factory = get_session_factory()
    async with factory() as session:
        items = await query_catalog(session, q=q, category=category, limit=200)
    return HTMLResponse(render_storefront_html(items))


@router.get("/.well-known/ucp")
async def ucp_manifest() -> dict[str, Any]:
    """مانیفست استاندارد کشف فروشگاه توسط ایجنت‌های خریدار."""
    return {
        "name": "Peyja Supply-Side OS",
        "protocol": "ucp-v0.2",
        "capabilities": ["catalog.search", "catalog.evidence", "dm.inquiry"],
        "endpoints": {
            "catalog_json": "/api/catalog",
            "catalog_ask": "/api/catalog/ask",
            "storefront_html": "/catalog",
            "webhook": "/webhooks/boxapi",
        },
    }
