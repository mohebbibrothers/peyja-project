"""CLI پیجا — «کاتالوگ بساز» در یک فرمان.

peyja catalog build <username>
peyja crawl <username> --max-media 60
peyja extract --page-id 1
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import typer

app = typer.Typer(help="Peyja — Supply-Side OS · Data Spine CLI", add_completion=False)


@app.command()
def crawl(username: str, max_media: int = typer.Option(60, help="حداکثر تعداد رسانه")) -> None:
    """کرال یک پیج عمومی + آرشیو خام."""
    asyncio.run(_crawl(username, max_media))


async def _crawl(username: str, max_media: int) -> None:
    from peyja.db import ensure_schema, get_session_factory
    from peyja.ingest.crawler import BudgetExceeded, CrawlBudgetGuard, Crawler
    from peyja.provider.boxapi_data import BoxApiDataProvider

    await ensure_schema()
    factory = get_session_factory()
    provider = BoxApiDataProvider()
    async with factory() as session:
        crawler = Crawler(session, provider, CrawlBudgetGuard())
        try:
            stats = await crawler.crawl_page(username, max_media=max_media)
            typer.echo(json.dumps(stats, ensure_ascii=False, indent=2))
        except BudgetExceeded as e:
            typer.echo(f"⚠️ {e} — اجرا متوقف شد؛ از sync_cursor ادامه دهید.")
            sys.exit(2)
    await provider.aclose()


@app.command()
def extract(page_id: int) -> None:
    """استخراج کاتالوگ از raw_items یک پیج."""
    asyncio.run(_extract(page_id))


async def _extract(page_id: int) -> None:
    from peyja.db import ensure_schema, get_session_factory
    from peyja.extract.pipeline import ExtractionPipeline

    await ensure_schema()
    factory = get_session_factory()
    async with factory() as session:
        pipeline = ExtractionPipeline(session)
        stats = await pipeline.run_for_page(page_id)
        await session.commit()
        typer.echo(json.dumps(stats, ensure_ascii=False, indent=2))


@app.command()
def build(username: str, out: Path = typer.Option(Path("out"), help="پوشه‌ی خروجی")) -> None:
    """فرمان نمایشی: «کاتالوگ بساز» — crawl + extract + products.json + گزارش فروشنده."""
    asyncio.run(_build(username, out))


async def _build(username: str, out: Path) -> None:
    import time

    from sqlalchemy import select

    from peyja.db import ensure_schema, get_session_factory
    from peyja.extract.pipeline import ExtractionPipeline
    from peyja.ingest.crawler import Crawler
    from peyja.models import Offer, Page, Product, Variant
    from peyja.provider.boxapi_data import BoxApiDataProvider

    await ensure_schema()
    t0 = time.perf_counter()
    factory = get_session_factory()
    provider = BoxApiDataProvider()
    out.mkdir(parents=True, exist_ok=True)

    async with factory() as session:
        crawler = Crawler(session, provider)
        crawl_stats = await crawler.crawl_page(username)
        pipeline = ExtractionPipeline(session)
        extract_stats = await pipeline.run_for_page(
            (await session.execute(select(Page).where(Page.username == username))).scalar_one().id
        )
        await session.commit()

        # خروجی products.json
        products = (await session.execute(select(Product))).scalars().all()
        catalog = []
        for p in products:
            variants = (
                (await session.execute(select(Variant).where(Variant.product_id == p.id)))
                .scalars()
                .all()
            )
            var_out = []
            for v in variants:
                offers = (
                    (await session.execute(select(Offer).where(Offer.variant_id == v.id)))
                    .scalars()
                    .all()
                )
                var_out.append(
                    {
                        "sku": v.sku,
                        "options": v.options,
                        "offers": [
                            {
                                "price_toman": o.price_minor,
                                "stock_state": o.stock_state,
                                "stale": o.stale,
                            }
                            for o in offers
                        ],
                    }
                )
            catalog.append(
                {
                    "title_fa": p.title_fa,
                    "category": p.category,
                    "attributes": p.attributes,
                    "status": p.status,
                    "variants": var_out,
                }
            )

    elapsed = time.perf_counter() - t0
    (out / "products.json").write_text(
        json.dumps(catalog, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    report = _seller_report(username, crawl_stats, extract_stats, catalog, elapsed)
    (out / "seller-report.md").write_text(report, encoding="utf-8")
    typer.echo(
        f"✅ کاتالوگ ساخته شد: {out}/products.json و {out}/seller-report.md ({elapsed:.1f}s)"
    )


def _seller_report(username: str, crawl: dict, extract: dict, catalog: list, elapsed: float) -> str:
    confirmed = sum(1 for p in catalog if p["status"] == "confirmed")
    low = extract.get("low_confidence", 0)
    lines = [
        f"# گزارش فروشنده — @{username}",
        "",
        f"**زمان ساخت:** {elapsed:.1f} ثانیه",
        f"**رسانه‌های دریافتی:** {crawl.get('fetched', 0)}",
        f"**محصولات استخراج‌شده:** {len(catalog)} (تأییدشده: {confirmed} | در انتظار تأیید: {low})",
        "",
        "## فهرست محصولات",
        "",
    ]
    for p in catalog:
        price = ""
        on_request = False
        for v in p["variants"]:
            for o in v["offers"]:
                if o["price_toman"]:
                    price = f"{o['price_toman']:,} تومان"
                if o.get("stock_state") == "on_request":
                    on_request = True
        label = price or ("قیمت در دایرکت" if on_request else "—")
        lines.append(f"- **{p['title_fa']}** ({p.get('category') or '—'}) — {label}")
    lines += ["", "> تولیدشده با Peyja — Data Spine v0.1"]
    return "\n".join(lines)


@app.command("extract-text")
def extract_text(
    input: Path = typer.Argument(..., help='فایل JSON کپشن‌ها: [{"id":..., "caption":...}]'),
    out: Path = typer.Option(Path("out/predictions.json"), help="خروجی پیش‌بینی‌ها"),
) -> None:
    """استخراج آفلاین از کپشن‌های متنی — «عدد سرنوشت» بدون نیاز به Data API."""
    import json

    from peyja.extract.pipeline import ExtractionPipeline

    samples = json.loads(input.read_text(encoding="utf-8"))
    preds = []
    for s in samples:
        r = ExtractionPipeline.extract_signals_fa(s.get("caption") or "")
        preds.append({"id": s["id"], **r})
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(preds, ensure_ascii=False, indent=2), encoding="utf-8")
    typer.echo(f"✅ {len(preds)} نمونه پردازش شد → {out}")


@app.command()
def doctor() -> None:
    """بررسی سلامت اتصال‌ها — API رسمی، توکن دیتا، وب‌هوک، دیتابیس."""
    asyncio.run(_doctor())


async def _doctor() -> None:
    from peyja.config import get_settings
    from peyja.db import get_engine
    from peyja.provider.boxapi_official import BoxApiOfficialProvider

    settings = get_settings()
    checks: list[tuple[str, bool, str]] = []

    checks.append(
        (
            "BOXAPI_API_KEY تنظیم شده",
            bool(settings.boxapi_api_key),
            settings.boxapi_api_key[:8] + "..." if settings.boxapi_api_key else "خالی",
        )
    )
    checks.append(
        (
            "WEBHOOK_SECRET تنظیم شده",
            bool(settings.boxapi_webhook_secret),
            settings.boxapi_webhook_secret[:10] + "..."
            if settings.boxapi_webhook_secret
            else "خالی",
        )
    )

    provider = BoxApiOfficialProvider()
    try:
        accounts = await provider.get_accounts()
        names = ", ".join(f"@{a.get('username')}" for a in accounts) or "بدون اکانت"
        checks.append(("API رسمی (service/accounts)", True, names))
    except Exception as e:
        checks.append(("API رسمی (service/accounts)", False, str(e)[:80]))
    finally:
        await provider.aclose()

    try:
        from sqlalchemy import text

        engine = get_engine()
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        checks.append(("دیتابیس", True, settings.effective_database_url.split("://")[0]))
    except Exception as e:
        checks.append(("دیتابیس", False, str(e)[:80]))

    checks.append(
        (
            "توکن Data API",
            bool(settings.boxapi_data_token),
            settings.boxapi_data_token[:8] + "..."
            if settings.boxapi_data_token
            else "خالی (برای کرال عمومی لازم است)",
        )
    )

    # ── لایه‌ی وب: آیا وب‌سرور درخواست را به اپ می‌رساند؟ ──
    # هشدار مهم: نام‌های مجازی (vhost) در cPanel معمولاً روی IP عمومی سرور تعریف می‌شوند؛
    # آزمون روی 127.0.0.1 ممکن است به vhost پیش‌فرض بخورد و «منفی کاذب» بدهد.
    # برای همین هم loopback و هم IP عمومی سرور را با هدر Host آزمایش می‌کنیم.
    import socket as _socket

    import httpx as _httpx

    web_host = (settings.app_url or settings.boxapi_domain or "").split("//")[-1].split("/")[0]
    try:
        with _socket.socket(_socket.AF_INET, _socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            server_ip = s.getsockname()[0]
    except Exception:
        server_ip = ""

    targets = [("loopback", "127.0.0.1")]
    if server_ip:
        targets.append((f"IP سرور {server_ip}", server_ip))

    for label, addr in targets:
        try:
            async with _httpx.AsyncClient(verify=False, timeout=8) as c:
                r = await c.get(
                    f"http://{addr}/health",
                    headers={"Host": web_host} if web_host else None,
                )
            if r.status_code == 200 and "peyja" in r.text:
                checks.append((f"وب محلی · {label}", True, "درخواست به اپ رسید ✓"))
            else:
                checks.append(
                    (
                        f"وب محلی · {label}",
                        False,
                        f"HTTP {r.status_code} — وب‌سرور به اپ نمی‌رساند (Application URL / .htaccess / docroot)",
                    )
                )
        except Exception as e:
            checks.append((f"وب محلی · {label}", False, f"عدم اتصال: {str(e)[:60]}"))

    # ── انحراف ساعت سرور: برای تأیید HMAC وب‌هوک (پنجره‌ی زمانی) حیاتی است ──
    import time as _time
    from email.utils import parsedate_to_datetime as _pdate

    try:
        async with _httpx.AsyncClient(timeout=8, follow_redirects=True) as c:
            r = await c.head("https://boxapi.ir")
        remote_ts = _pdate(r.headers["Date"]).timestamp()
        skew = _time.time() - remote_ts
        if abs(skew) <= 300:
            checks.append(("ساعت سرور", True, f"انحراف {skew:.0f} ثانیه — در محدوده‌ی امن"))
        else:
            checks.append(
                (
                    "ساعت سرور",
                    False,
                    f"انحراف {skew / 86400:+.1f} روز! در .env مقدار WEBHOOK_TOLERANCE_SECONDS "
                    f"را ≥ {int(abs(skew)) + 300} بگذارید (راهنما: docs/DEPLOY-cpanel.md)",
                )
            )
    except Exception as e:
        checks.append(("ساعت سرور", False, f"عدم امکان سنجش: {str(e)[:60]}"))

    width = max(len(c[0]) for c in checks)
    for name, ok, detail in checks:
        mark = "✅" if ok else "❌"
        typer.echo(f"{mark} {name:<{width}}  {detail}")
    if not all(c[1] for c in checks[:3]):
        raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
