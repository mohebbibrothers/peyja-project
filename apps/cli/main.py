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

        from peyja.db import ensure_schema

        await ensure_schema()  # دیتابیسِ خالی نباید هرگز «سبز» گزارش شود
        engine = get_engine()
        detail = settings.effective_database_url.split("://")[0]
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
            if settings.effective_database_url.startswith("sqlite"):
                tables = (
                    await conn.execute(
                        text("SELECT count(*) FROM sqlite_master WHERE type='table'")
                    )
                ).scalar() or 0
                detail = f"{detail} · جداول آماده ({tables} جدول)"
        checks.append(("دیتابیس", True, detail))
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

    # ── DNS دامنه: آیا نام عمومی به IP همین سرور اشاره می‌کند؟ ──
    if web_host and server_ip:
        try:
            addrs = sorted({str(item[4][0]) for item in _socket.getaddrinfo(web_host, None)})
            ok = server_ip in addrs
            checks.append(
                (
                    f"DNS · {web_host}",
                    ok,
                    f"→ {', '.join(addrs)} ✓"
                    if ok
                    else f"→ {', '.join(addrs) or 'بدون رکورد'} — باید {server_ip} باشد (Zone Editor → A record)",
                )
            )
        except OSError:
            checks.append(
                (
                    f"DNS · {web_host}",
                    False,
                    f"NXDOMAIN — در Zone Editor رکورد A برای {web_host} ← {server_ip} ثبت کنید",
                )
            )

    # ── انحراف ساعت سرور: برای تأیید HMAC وب‌هوک (پنجره‌ی زمانی) حیاتی است ──
    import time as _time
    from email.utils import parsedate_to_datetime as _pdate

    try:
        remote_ts: float | None = None
        for url in ("https://boxapi.ir", "https://www.google.com", "http://www.google.com"):
            try:
                async with _httpx.AsyncClient(timeout=12, follow_redirects=True) as c:
                    r = await c.get(url)
                date_hdr = r.headers.get("Date", "")
                if date_hdr:
                    remote_ts = _pdate(date_hdr).timestamp()
                    break
            except Exception:
                continue
        if remote_ts is None:
            checks.append(("ساعت سرور", False, "عدم امکان سنجش (هیچ سرور زمانی پاسخ نداد)"))
        else:
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
        checks.append(("ساعت سرور", False, f"عدم امکان سنجش: {str(e) or type(e).__name__}"))

    width = max(len(c[0]) for c in checks)
    for name, ok, detail in checks:
        mark = "✅" if ok else "❌"
        typer.echo(f"{mark} {name:<{width}}  {detail}")
    if not all(c[1] for c in checks[:3]):
        raise typer.Exit(code=1)


@app.command("selftest-webhook")
def selftest_webhook(
    url: str = typer.Option(
        "http://127.0.0.1/webhooks/boxapi",
        "--url",
        help="آدرس وب‌هوک — برای آزمون عمومی بدون DNS: http://<IP>/webhooks/boxapi",
    ),
    host: str = typer.Option(
        "", "--host", help="هدر Host برای آزمون با IP (مثلاً kaveh185.n-cpanel.xyz)"
    ),
    sender: str = typer.Option("selftest-user", "--sender", help="شناسه‌ی فرستنده‌ی شبیه‌سازی‌شده"),
) -> None:
    """آزمون سرتاسری حلقه‌ی وب‌هوک: امضای HMAC + POST واقعی + گزارش ثبت در دیتابیس."""
    asyncio.run(_selftest_webhook(url, host, sender))


async def _selftest_webhook(url: str, host: str, sender: str) -> None:
    import time as _t

    import httpx
    from sqlalchemy import select

    from peyja.config import get_settings
    from peyja.db import get_session_factory
    from peyja.ingest.webhook import sign_payload
    from peyja.models import Message, WebhookEvent

    settings = get_settings()
    if not settings.boxapi_webhook_secret:
        typer.echo("❌ WEBHOOK_SECRET در .env تنظیم نشده")
        raise typer.Exit(code=1)

    now_ms = int(_t.time() * 1000)
    event_id = f"selftest-{now_ms}"
    # شکل واقعی طبق داک رسمی BoxAPI: پیام‌ها داخل آرایه‌ی data.messaging[]؛
    # امضا با پیشوند sha256= — نسخه‌ی قبلی شکل تختِ دست‌ساز می‌فرستاد که فقط
    # خودش را تأیید می‌کرد (به همین دلیل باگ انولوپ در selftest گم شد)
    body = {
        "event_id": event_id,
        "event_type": "messaging",
        "account_id": "selftest-account",
        "data": {
            "time": now_ms,
            "id": "17841429538161969",
            "messaging": [
                {
                    "sender": {"id": sender},
                    "recipient": {"id": "17841429538161969"},
                    "timestamp": now_ms,
                    "message": {"mid": event_id, "text": "پیام آزمایشی حلقه‌ی پیجا"},
                }
            ],
        },
    }
    raw = json.dumps(body, ensure_ascii=False).encode()
    ts, sig = sign_payload(settings.boxapi_webhook_secret, raw)

    headers = {
        "Content-Type": "application/json",
        "X-BoxApi-Signature": f"sha256={sig}",
        "X-BoxApi-Timestamp": ts,
        "X-BoxApi-Event-Id": event_id,
    }
    if host:
        headers["Host"] = host

    typer.echo(f"→ POST {url}" + (f" (Host: {host})" if host else ""))
    try:
        async with httpx.AsyncClient(timeout=15, verify=False) as c:
            r = await c.post(url, content=raw, headers=headers)
        typer.echo(f"پاسخ وب‌هوک: HTTP {r.status_code}  {r.text[:200]}")
    except Exception as e:
        typer.echo(f"❌ عدم اتصال: {str(e) or type(e).__name__}")
        raise typer.Exit(code=1) from e

    if r.status_code != 200:
        raise typer.Exit(code=1)

    factory = get_session_factory()
    async with factory() as session:
        ev = (
            await session.execute(select(WebhookEvent).where(WebhookEvent.event_id == event_id))
        ).scalar_one_or_none()
        msg = (
            await session.execute(select(Message).where(Message.mid == event_id))
        ).scalar_one_or_none()

    if ev:
        typer.echo(f"✅ رویداد در دیتابیس ثبت شد: {ev.event_type} @ {ev.received_at}")
    else:
        typer.echo("⚠️ رویداد در دیتابیس پیدا نشد (پاسخ از مسیر دیگری آمده؟)")
    if msg:
        typer.echo(f"✅ پیام ورودی ثبت و پنجره‌ی ۲۴ ساعته باز شد: «{msg.text}»")
    else:
        typer.echo("⚠️ پیام ورودی ثبت نشد")


@app.command()
def events(limit: int = typer.Option(5, "--limit", "-n", help="تعداد ردیف‌ها")) -> None:
    """آخرین رویدادهای وب‌هوک و پیام‌ها از دیتابیس — حقیقت مطلقِ «چه چیزی رسید»."""
    asyncio.run(_events(limit))


async def _events(limit: int) -> None:
    from sqlalchemy import select

    from peyja.db import ensure_schema, get_session_factory
    from peyja.models import Conversation, Message, WebhookEvent

    await ensure_schema()
    factory = get_session_factory()
    async with factory() as session:
        evs = (
            (
                await session.execute(
                    select(WebhookEvent).order_by(WebhookEvent.received_at.desc()).limit(limit)
                )
            )
            .scalars()
            .all()
        )
        convs = (
            (
                await session.execute(
                    select(Conversation).order_by(Conversation.id.desc()).limit(limit)
                )
            )
            .scalars()
            .all()
        )
        msgs = (
            (
                await session.execute(
                    select(Message).order_by(Message.sent_at.desc()).limit(limit * 2)
                )
            )
            .scalars()
            .all()
        )

    typer.echo(f"— آخرین {len(evs)} رویداد وب‌هوک —")
    for e in evs:
        typer.echo(
            f"  {e.received_at}  {e.event_type:<12} id={e.event_id}  "
            f"account={e.account_id or '-'}  processed={'✓' if e.processed_at else '✗'}"
        )
    if not evs:
        typer.echo("  (هیچ رویدادی ثبت نشده — یعنی وب‌هوکی نرسیده)")
    typer.echo(f"— آخرین {len(convs)} مکالمه —")
    for c in convs:
        typer.echo(
            f"  user={c.ig_user_id}  account={c.account_id}  state={c.state}  window={c.window_expires_at}"
        )
    typer.echo(f"— آخرین {len(msgs)} پیام —")
    for m in msgs:
        typer.echo(f"  [{m.direction}] {m.sent_at}  mid={m.mid}  «{(m.text or '')[:60]}»")


@app.command("send-message")
def send_message_cmd(
    to: str = typer.Option(..., "--to", help="شناسه‌ی اینستاگرامی گیرنده (ig user id)"),
    text: str = typer.Option(..., "--text", help="متن پیام"),
    account_id: str = typer.Option("", "--account-id", help="UUID پیج — خالی = اولین اکانت"),
) -> None:
    """ارسال مستقیم تستی — مسیر «فرستادن» را بدون وب‌هوک محک می‌زند (ابزار دیباگ)."""
    asyncio.run(_send_message_cmd(to, text, account_id))


async def _send_message_cmd(to: str, text: str, account_id: str) -> None:
    from peyja.provider.boxapi_official import BoxApiOfficialProvider, OfficialApiError

    provider = BoxApiOfficialProvider()
    try:
        if not account_id:
            accounts = await provider.get_accounts()
            if not accounts:
                typer.echo("❌ هیچ اکانتی به این کلید API وصل نیست")
                raise typer.Exit(code=1)
            first = accounts[0]
            account_id = str(first.get("id") or first.get("account_id") or "")
            typer.echo(f"→ account_id (خودکار): {account_id}  ({first.get('username', '?')})")
        typer.echo(f"→ POST send_message  to={to}  «{text}»")
        result = await provider.send_message(account_id, to, text)
        typer.echo(f"✅ پذیرفته و در صف رفت: {json.dumps(result, ensure_ascii=False)}")
    except OfficialApiError as e:
        typer.echo(f"❌ BoxAPI رد کرد: {e}")
        raise typer.Exit(code=1) from e
    finally:
        await provider.aclose()


if __name__ == "__main__":
    app()
