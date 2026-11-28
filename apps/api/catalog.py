"""مسیرهای کاتالوگ ایجنت‌فهم + ویترین وب (UCP / Agent-Native Surface)."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from peyja.catalog_query import (
    answer_product_query,
    query_catalog,
    render_storefront_html,
    review_catalog_product,
)
from peyja.db import ensure_schema, get_session_factory

router = APIRouter(tags=["catalog"])


class ReviewRequest(BaseModel):
    action: Literal["confirm", "reject"]
    price_toman: int | None = Field(default=None, ge=1000, le=5_000_000_000)
    category: str | None = None
    reviewer: str = "seller"


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


@router.post("/api/catalog/{product_id}/review")
async def review_catalog_item(product_id: int, req: ReviewRequest) -> dict[str, Any]:
    """تأیید/رد انسانی یا ثبت قیمت فروشنده همراه با Evidence (قانون صفر)."""
    await ensure_schema()
    factory = get_session_factory()
    async with factory() as session:
        updated = await review_catalog_product(
            session,
            product_id,
            action=req.action,
            price_toman=req.price_toman,
            category=req.category,
            reviewer=req.reviewer,
        )
        if updated is None:
            raise HTTPException(status_code=404, detail="محصول پیدا نشد")
        await session.commit()
    return {"ok": True, "item": updated}


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
        "capabilities": ["catalog.search", "catalog.evidence", "catalog.review", "dm.inquiry"],
        "endpoints": {
            "catalog_json": "/api/catalog",
            "catalog_ask": "/api/catalog/ask",
            "catalog_review": "/api/catalog/{product_id}/review",
            "storefront_html": "/catalog",
            "webhook": "/webhooks/boxapi",
        },
    }
