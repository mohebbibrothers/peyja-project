"""لایه‌ی پرس‌وجوی کاتالوگ + پاسخ‌گوی DM مدرک‌محور (قانون صفر: استخراج‌شده ≠ تأییدشده).

مسیر واحد بین:
- API عامل‌محور (`GET /api/catalog` و `GET /catalog`)
- خروجیِ CLI (`peyja catalog-export`)
- دستیارِ پاسخ به مشتری (`answer_product_query` — بدون توهم و فقط با اتکا به Evidence)
"""

from __future__ import annotations

import html
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from peyja.extract.catalog import canonical_brand, fold
from peyja.models import Evidence, Offer, Page, Product, Variant

_QUERY_STOP = {
    "سلام",
    "وقت",
    "بخیر",
    "قیمت",
    "چنده",
    "چند",
    "موجود",
    "موجوده",
    "دارید",
    "دارین",
    "این",
    "اینو",
    "لطفا",
    "لطفاً",
    "تومان",
    "تومن",
    "محصول",
    "رو",
    "را",
    "از",
    "میخوام",
    "میخواستم",
}


async def query_catalog(
    session: AsyncSession,
    *,
    q: str = "",
    category: str | None = None,
    confirmed_only: bool = False,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """دریافت کاتالوگ ساختاریافته همراه با وضعیت تأیید و مدارک (Evidence)."""
    stmt = select(Product, Page.username).join(Page, Page.id == Product.page_id, isouter=True)
    if category:
        stmt = stmt.where(Product.category == category)
    if confirmed_only:
        stmt = stmt.where(Product.status == "confirmed")
    stmt = stmt.order_by(Product.id.desc())

    rows = (await session.execute(stmt)).all()
    q_norm = fold(q).strip()
    q_brand = canonical_brand(q) if q else None
    q_tokens = [t for t in re.split(r"\s+", q_norm) if len(t) >= 2 and t not in _QUERY_STOP]

    items: list[dict[str, Any]] = []
    for product, username in rows:
        attrs = dict(product.attributes or {})
        searchable = fold(
            " ".join(
                str(x or "")
                for x in (
                    product.title_fa,
                    attrs.get("product_name"),
                    attrs.get("brand"),
                    product.category,
                    " ".join(attrs.get("colors") or []),
                )
            )
        )
        if q_tokens:
            brand_hit = bool(q_brand and fold(str(attrs.get("brand") or "")) == fold(q_brand))
            tok_hits = sum(1 for tok in q_tokens if tok in searchable)
            if not brand_hit and tok_hits == 0:
                continue

        variants = (
            (await session.execute(select(Variant).where(Variant.product_id == product.id)))
            .scalars()
            .all()
        )
        offers_out: list[dict[str, Any]] = []
        ev_out: list[dict[str, Any]] = []
        best_price: int | None = None
        best_stock = "unknown"
        best_conf = 0.0

        for var in variants:
            offers = (
                (await session.execute(select(Offer).where(Offer.variant_id == var.id)))
                .scalars()
                .all()
            )
            for off in offers:
                if best_price is None and off.price_minor:
                    best_price = off.price_minor
                if best_stock == "unknown" and off.stock_state:
                    best_stock = off.stock_state
                offers_out.append(
                    {
                        "price_toman": off.price_minor,
                        "stock_state": off.stock_state,
                        "stale": off.stale,
                    }
                )
                evs = (
                    (await session.execute(select(Evidence).where(Evidence.offer_id == off.id)))
                    .scalars()
                    .all()
                )
                for ev in evs:
                    best_conf = max(best_conf, float(ev.confidence or 0.0))
                    ev_out.append(
                        {
                            "source_type": ev.source_type,
                            "source_id": ev.source_id,
                            "method": ev.method,
                            "confidence": ev.confidence,
                        }
                    )

        items.append(
            {
                "id": product.id,
                "seller": username,
                "title_fa": product.title_fa,
                "product_name": attrs.get("product_name"),
                "brand": attrs.get("brand"),
                "category": product.category,
                "colors": attrs.get("colors") or [],
                "shades": attrs.get("shades") or [],
                "sizes": attrs.get("sizes") or [],
                "status": product.status,
                "verified": product.status == "confirmed",
                "price_toman": best_price,
                "stock_state": best_stock,
                "confidence": round(best_conf, 3),
                "offers": offers_out,
                "evidence": ev_out,
            }
        )
        if len(items) >= limit:
            break

    return items


async def answer_product_query(session: AsyncSession, query_text: str) -> dict[str, Any]:
    """تولید پیش‌نویس پاسخ DM با اتکا به کاتالوگ و قانون صفر (بدون ادعای قیمت تأییدنشده)."""
    matches = await query_catalog(session, q=query_text, limit=5)
    if not matches:
        return {"matched": False, "items": [], "reply_text": None}

    # اولویت با محصولات تأییدشده‌ی دارای قیمت
    matches.sort(key=lambda x: (not x["verified"], x["price_toman"] is None, -x["confidence"]))
    top = matches[0]
    name = top["title_fa"] or top["product_name"] or "محصول"
    if top["verified"] and top["price_toman"]:
        reply = f"سلام 🌸 درباره‌ی «{name}»: قیمت تأییدشده‌ی ثبت‌شده {top['price_toman']:,} تومان است."
    elif top["stock_state"] == "on_request":
        reply = (
            f"سلام 🌸 برای «{name}» قیمت به‌صورت استعلامی ثبت شده؛ "
            "همکاران ما همین الان قیمت روز و موجودی دقیق را برایتان ارسال می‌کنند."
        )
    else:
        reply = (
            f"سلام 🌸 پیام شما درباره‌ی «{name}» ثبت شد؛ "
            "چون قیمت/موجودی این کالا در انتظار تأیید نهایی فروشنده است، به‌زودی دقیق پاسخ می‌دهیم."
        )
    return {"matched": True, "items": matches, "reply_text": reply}


def render_storefront_html(
    items: list[dict[str, Any]], *, page_title: str = "ویترین کاتالوگ پیجا"
) -> str:
    """صفحه‌ی ویترین مستقل RTL بدون درخواست بیرونی (قابل‌پیش‌نمایش در هر مرورگر و sandbox)."""
    confirmed = sum(1 for i in items if i.get("verified"))
    priced = sum(1 for i in items if i.get("price_toman"))
    cards: list[str] = []
    for it in items:
        title = html.escape(str(it.get("title_fa") or it.get("product_name") or "محصول"))
        cat = html.escape(str(it.get("category") or "بدون دسته"))
        brand = html.escape(str(it.get("brand") or "—"))
        price_val = it.get("price_toman")
        price_str = (
            f"{price_val:,} تومان"
            if price_val
            else (
                "استعلام قیمت در دایرکت"
                if it.get("stock_state") == "on_request"
                else "نامشخص / بدون قیمت"
            )
        )
        badge_cls = "ok" if it.get("verified") else "rev"
        badge_txt = "تأییدشده" if it.get("verified") else "در صف بررسی"
        conf = round(float(it.get("confidence") or 0) * 100)
        cards.append(
            f'<div class="card"><div class="top"><span class="cat">{cat}</span>'
            f'<span class="badge {badge_cls}">{badge_txt} ({conf}٪)</span></div>'
            f'<h3>{title}</h3><div class="meta">برند: {brand}</div>'
            f'<div class="price">{html.escape(price_str)}</div></div>'
        )
    cards_html = "\n".join(cards) or '<p class="empty">هنوز محصولی در کاتالوگ ثبت نشده است.</p>'
    return f"""<!DOCTYPE html>
<html lang="fa" dir="rtl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(page_title)}</title>
<style>
body{{font-family:Tahoma,sans-serif;background:#0f172a;color:#e2e8f0;margin:0;padding:1.5rem}}
.hdr{{max-width:1100px;margin:0 auto 1.2rem;display:flex;flex-wrap:wrap;gap:1rem;align-items:center;justify-content:space-between}}
.stats{{display:flex;gap:.6rem;font-size:.85rem}}
.pill{{background:#1e293b;border:1px solid #334155;padding:.35rem .7rem;border-radius:999px}}
.grid{{max-width:1100px;margin:0 auto;display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:.9rem}}
.card{{background:#1e293b;border:1px solid #334155;border-radius:.8rem;padding:.95rem;display:flex;flex-direction:column;gap:.45rem}}
.top{{display:flex;justify-content:space-between;align-items:center;font-size:.75rem}}
.cat{{background:#334155;padding:.15rem .5rem;border-radius:.4rem}}
.badge.ok{{background:#065f46;color:#a7f3d0;padding:.15rem .5rem;border-radius:.4rem}}
.badge.rev{{background:#78350f;color:#fde68a;padding:.15rem .5rem;border-radius:.4rem}}
h3{{margin:.2rem 0;font-size:.95rem;line-height:1.45;color:#f8fafc}}
.meta{{font-size:.8rem;color:#94a3b8}}
.price{{margin-top:auto;font-weight:700;color:#38bdf8;font-size:.92rem;padding-top:.3rem}}
</style></head><body>
<div class="hdr"><h1>🛍️ {html.escape(page_title)}</h1>
<div class="stats"><span class="pill">کل: {len(items)}</span>
<span class="pill">تأییدشده: {confirmed}</span>
<span class="pill">قیمت‌دار: {priced}</span></div></div>
<div class="grid">{cards_html}</div></body></html>"""
