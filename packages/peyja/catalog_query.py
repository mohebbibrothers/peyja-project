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
from peyja.models import Evidence, Offer, Page, Product, Variant, utcnow

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
    "داره",
    "هست",
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
    "شما",
}


async def query_catalog(
    session: AsyncSession,
    *,
    q: str = "",
    category: str | None = None,
    confirmed_only: bool = False,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """دریافت کاتالوگ ساختاریافته همراه با وضعیت تأیید، رتبه‌بندی انطباق و مدارک (Evidence)."""
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

    scored_items: list[tuple[tuple[int, int, int, float, int], dict[str, Any]]] = []
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
        brand_hit = bool(
            q_brand and attrs.get("brand") and fold(str(attrs.get("brand") or "")) == fold(q_brand)
        )
        tok_hits = sum(1 for tok in q_tokens if tok in searchable)
        if q_tokens and not brand_hit and tok_hits == 0:
            continue
        match_score = (2 if brand_hit else 0) + tok_hits

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
                (
                    await session.execute(
                        select(Offer).where(Offer.variant_id == var.id).order_by(Offer.id.desc())
                    )
                )
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

        item = {
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
        rank_key = (
            match_score if q_tokens else 0,
            1 if product.status == "confirmed" else 0,
            1 if best_price else 0,
            best_conf,
            product.id,
        )
        scored_items.append((rank_key, item))

    if q_tokens:
        scored_items.sort(key=lambda pair: pair[0], reverse=True)
    return [item for _, item in scored_items[:limit]]


async def review_catalog_product(
    session: AsyncSession,
    product_id: int,
    *,
    action: str,
    price_toman: int | None = None,
    category: str | None = None,
    reviewer: str = "seller",
) -> dict[str, Any] | None:
    """تأیید یا رد انسانی یک محصول در کاتالوگ با ثبت کامل Evidence (قانون صفر)."""
    product = (
        await session.execute(select(Product).where(Product.id == product_id))
    ).scalar_one_or_none()
    if product is None:
        return None
    if action not in ("confirm", "reject"):
        raise ValueError(f"اکشن نامعتبر: {action}")

    if category:
        product.category = category
    product.status = "confirmed" if action == "confirm" else "rejected"

    variant = (
        (await session.execute(select(Variant).where(Variant.product_id == product.id)))
        .scalars()
        .first()
    )
    if variant is None:
        variant = Variant(product_id=product.id, options=None)
        session.add(variant)
        await session.flush()

    offer_id: int | None = None
    if action == "confirm" and price_toman is not None and price_toman > 0:
        offer = Offer(
            variant_id=variant.id,
            price_minor=int(price_toman),
            stock_state="in_stock",
            valid_from=utcnow(),
            ttl_hours=24,
            stale=False,
        )
        session.add(offer)
        await session.flush()
        offer_id = offer.id
    else:
        existing_offer = (
            (
                await session.execute(
                    select(Offer).where(Offer.variant_id == variant.id).order_by(Offer.id.desc())
                )
            )
            .scalars()
            .first()
        )
        if existing_offer is not None:
            offer_id = existing_offer.id

    session.add(
        Evidence(
            offer_id=offer_id,
            product_id=product.id,
            source_type="seller_echo",
            source_id=f"review:{product.id}",
            method="human_review",
            confidence=1.0 if action == "confirm" else 0.0,
            verified_by=reviewer,
            verified_at=utcnow(),
        )
    )
    await session.flush()
    items = await query_catalog(session, limit=500)
    for it in items:
        if it["id"] == product.id:
            return it
    return {"id": product.id, "status": product.status, "verified": product.status == "confirmed"}


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
    items: list[dict[str, Any]], *, page_title: str = "ویترین و کنسول تأیید پیجا"
) -> str:
    """ویترین + کنسول تأیید مستقل RTL بدون درخواست بیرونی (قابل‌پیش‌نمایش در هر مرورگر و sandbox)."""
    confirmed = sum(1 for i in items if i.get("verified"))
    priced = sum(1 for i in items if i.get("price_toman"))
    cats = sorted({str(i.get("category") or "بدون دسته") for i in items})
    cat_opts = ['<option value="all">همه دسته‌ها</option>'] + [
        f'<option value="{html.escape(c)}">{html.escape(c)}</option>' for c in cats
    ]
    cards: list[str] = []
    for it in items:
        pid = int(it.get("id") or 0)
        title = html.escape(str(it.get("title_fa") or it.get("product_name") or "محصول"))
        cat = html.escape(str(it.get("category") or "بدون دسته"))
        brand = html.escape(str(it.get("brand") or "—"))
        status = html.escape(str(it.get("status") or "candidate"))
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
        badge_cls = "ok" if it.get("verified") else ("rej" if status == "rejected" else "rev")
        badge_txt = (
            "تأییدشده"
            if it.get("verified")
            else ("ردشده" if status == "rejected" else "در صف بررسی")
        )
        conf = round(float(it.get("confidence") or 0) * 100)
        actions = ""
        if pid > 0:
            actions = (
                f'<div class="acts">'
                f'<input type="number" min="1000" step="1000" placeholder="قیمت تومان" '
                f'value="{int(price_val) if price_val else ""}" class="pinp" id="p-{pid}">'
                f'<button type="button" class="btn ok" onclick="rev({pid},\'confirm\')">✓ تأیید</button>'
                f'<button type="button" class="btn no" onclick="rev({pid},\'reject\')">✗</button>'
                f"</div>"
            )
        cards.append(
            f'<div class="card" data-id="{pid}" data-cat="{cat}" data-status="{status}" '
            f'data-q="{title} {brand} {cat}">'
            f'<div class="top"><span class="cat">{cat}</span>'
            f'<span class="badge {badge_cls}" id="b-{pid}">{badge_txt} ({conf}٪)</span></div>'
            f'<h3>{title}</h3><div class="meta">برند: {brand}</div>'
            f'<div class="price" id="pr-{pid}">{html.escape(price_str)}</div>{actions}</div>'
        )
    cards_html = "\n".join(cards) or '<p class="empty">هنوز محصولی در کاتالوگ ثبت نشده است.</p>'
    return f"""<!DOCTYPE html>
<html lang="fa" dir="rtl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(page_title)}</title>
<style>
body{{font-family:Tahoma,sans-serif;background:#0f172a;color:#e2e8f0;margin:0;padding:1.5rem}}
.hdr,.bar{{max-width:1100px;margin:0 auto 1rem;display:flex;flex-wrap:wrap;gap:.8rem;align-items:center;justify-content:space-between}}
.stats{{display:flex;gap:.6rem;font-size:.85rem;flex-wrap:wrap}}
.pill{{background:#1e293b;border:1px solid #334155;padding:.35rem .7rem;border-radius:999px}}
.bar input,.bar select{{background:#1e293b;color:#f8fafc;border:1px solid #334155;border-radius:.5rem;padding:.45rem .7rem;font-family:inherit;font-size:.85rem}}
.bar input{{flex:1;min-width:200px}}
.grid{{max-width:1100px;margin:0 auto;display:grid;grid-template-columns:repeat(auto-fill,minmax(250px,1fr));gap:.9rem}}
.card{{background:#1e293b;border:1px solid #334155;border-radius:.8rem;padding:.95rem;display:flex;flex-direction:column;gap:.45rem}}
.top{{display:flex;justify-content:space-between;align-items:center;font-size:.75rem}}
.cat{{background:#334155;padding:.15rem .5rem;border-radius:.4rem}}
.badge.ok{{background:#065f46;color:#a7f3d0;padding:.15rem .5rem;border-radius:.4rem}}
.badge.rev{{background:#78350f;color:#fde68a;padding:.15rem .5rem;border-radius:.4rem}}
.badge.rej{{background:#7f1d1d;color:#fecaca;padding:.15rem .5rem;border-radius:.4rem}}
h3{{margin:.2rem 0;font-size:.95rem;line-height:1.45;color:#f8fafc}}
.meta{{font-size:.8rem;color:#94a3b8}}
.price{{margin-top:auto;font-weight:700;color:#38bdf8;font-size:.92rem;padding-top:.3rem}}
.acts{{display:flex;gap:.35rem;margin-top:.35rem;align-items:center}}
.pinp{{width:100%;min-width:0;background:#0f172a;color:#f8fafc;border:1px solid #334155;border-radius:.4rem;padding:.3rem .45rem;font-size:.78rem}}
.btn{{border:0;border-radius:.4rem;padding:.32rem .55rem;cursor:pointer;font-family:inherit;font-size:.78rem;white-space:nowrap}}
.btn.ok{{background:#059669;color:#fff}}
.btn.no{{background:#b91c1c;color:#fff}}
</style></head><body>
<div class="hdr"><h1>🛍️ {html.escape(page_title)}</h1>
<div class="stats"><span class="pill">کل: {len(items)}</span>
<span class="pill">تأییدشده: {confirmed}</span>
<span class="pill">قیمت‌دار: {priced}</span></div></div>
<div class="bar"><input id="q" type="search" placeholder="جستجو در عنوان، برند یا دسته..." oninput="flt()">
<select id="fc" onchange="flt()">{"".join(cat_opts)}</select>
<select id="fs" onchange="flt()"><option value="all">همه وضعیت‌ها</option><option value="confirmed">تأییدشده</option><option value="candidate">در صف بررسی</option><option value="rejected">ردشده</option></select></div>
<div class="grid" id="grid">{cards_html}</div>
<script>
function flt(){{var q=(document.getElementById('q').value||'').toLowerCase(),c=document.getElementById('fc').value,s=document.getElementById('fs').value;document.querySelectorAll('.card').forEach(function(el){{var ok=(!q||el.dataset.q.toLowerCase().indexOf(q)>=0)&&(c==='all'||el.dataset.cat===c)&&(s==='all'||el.dataset.status===s);el.style.display=ok?'flex':'none';}});}}
async function rev(id,act){{var pEl=document.getElementById('p-'+id);var pv=pEl&&pEl.value?parseInt(pEl.value,10):null;try{{var r=await fetch('/api/catalog/'+id+'/review',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{action:act,price_toman:pv}})}});if(!r.ok)throw new Error(r.status);var d=await r.json();var b=document.getElementById('b-'+id);var pr=document.getElementById('pr-'+id);var card=b.closest('.card');card.dataset.status=d.item.status;b.className='badge '+(d.item.verified?'ok':'rej');b.textContent=d.item.verified?'تأییدشده (100٪)':'ردشده';if(d.item.price_toman)pr.textContent=Number(d.item.price_toman).toLocaleString('en-US')+' تومان';}}catch(e){{alert('برای ثبت تغییر باید از سرور /catalog باز شده باشد.');}}}}
</script></body></html>"""
