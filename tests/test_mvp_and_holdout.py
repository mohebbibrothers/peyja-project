"""تست‌های سرتاسری لایه‌ی MVP: کاتالوگ ایجنت‌فهم (UCP)، پاسخ مدرک‌محور و کرال بچ ۲ (Holdout)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from apps.api.main import app


def test_catalog_api_ucp_and_storefront_e2e(tmp_path: Path, monkeypatch: Any) -> None:
    """اثبات سرتاسری /api/catalog، /api/catalog/ask، /catalog و /.well-known/ucp با قانون صفر."""
    import peyja.config as cfg
    import peyja.db as dbmod
    from peyja.extract.pipeline import ExtractionPipeline
    from peyja.models import Page, RawItem

    monkeypatch.setenv("DB_PATH", str(tmp_path / "mvp.db"))
    cfg.get_settings.cache_clear()
    dbmod._engine = None
    dbmod._session_factory = None
    try:

        async def _seed() -> None:
            await dbmod.ensure_schema()
            factory = dbmod.get_session_factory()
            async with factory() as session:
                page = Page(username="demo_shop", ig_user_id="999", source="data")
                session.add(page)
                await session.flush()

                # ۱) محصول تأییدشده با قیمت قطعی (confidence >= 0.75)
                r1 = RawItem(
                    page_id=page.id,
                    kind="media",
                    ig_id="m1",
                    raw={"caption": "ریمل اسنس اصل موجود شد ۵۵۰ هزار تومان #اسنسصورتی"},
                )
                # ۲) محصول در صف بررسی بدون قیمت قطعی (confidence < 0.75)
                r2 = RawItem(
                    page_id=page.id,
                    kind="media",
                    ig_id="m2",
                    raw={"caption": "سرم ویتامین سی گارنیر موجوده #سرم"},
                )
                session.add_all([r1, r2])
                await session.flush()

                pipe = ExtractionPipeline(session, ai=None)
                await pipe.run_for_page(page.id)
                await session.commit()

        asyncio.run(_seed())
        client = TestClient(app)

        ucp = client.get("/.well-known/ucp")
        assert ucp.status_code == 200
        assert ucp.json()["endpoints"]["catalog_json"] == "/api/catalog"

        all_cat = client.get("/api/catalog").json()
        assert all_cat["count"] == 2

        confirmed = client.get("/api/catalog", params={"confirmed_only": True}).json()
        assert confirmed["count"] == 1
        assert confirmed["items"][0]["verified"] is True
        assert confirmed["items"][0]["price_toman"] == 550_000

        # پرسش برای محصول تاییدشده ← قیمت دقیق با مدرک
        ask_ok = client.get("/api/catalog/ask", params={"q": "قیمت ریمل اسنس چنده؟"}).json()
        assert ask_ok["matched"] is True
        assert "550,000" in ask_ok["reply_text"]

        # پرسش برای محصول در صف بررسی ← قانون صفر: ادعای قیمت قطعی نمی‌کند
        ask_rev = client.get("/api/catalog/ask", params={"q": "سرم گارنیر دارین؟"}).json()
        assert ask_rev["matched"] is True
        assert "انتظار تأیید نهایی" in ask_rev["reply_text"]

        page_html = client.get("/catalog")
        assert page_html.status_code == 200
        assert 'dir="rtl"' in page_html.text
        assert "550,000 تومان" in page_html.text
    finally:
        cfg.get_settings.cache_clear()
        dbmod._engine = None
        dbmod._session_factory = None


def test_build_catalog_auto_reply_respects_law_zero(tmp_path: Path, monkeypatch: Any) -> None:
    """پیش‌نویس کاتالوگ (A1) و قانون صفر در پاسخ خودکار."""
    import peyja.config as cfg
    import peyja.db as dbmod
    from peyja.extract.pipeline import ExtractionPipeline
    from peyja.loop.dispatcher import ClassifiedEvent
    from peyja.loop.reply import build_catalog_auto_reply
    from peyja.models import Page, RawItem

    monkeypatch.setenv("DB_PATH", str(tmp_path / "reply.db"))
    cfg.get_settings.cache_clear()
    dbmod._engine = None
    dbmod._session_factory = None
    try:

        async def _run() -> tuple[Any, str | None, Any]:
            await dbmod.ensure_schema()
            factory = dbmod.get_session_factory()
            async with factory() as session:
                page = Page(username="demo_shop", ig_user_id="999", source="data")
                session.add(page)
                await session.flush()
                session.add(
                    RawItem(
                        page_id=page.id,
                        kind="media",
                        ig_id="m1",
                        raw={"caption": "رژلب مات شماره ۱۲ قیمت ۴۸۰ هزار تومان"},
                    )
                )
                await session.flush()
                await ExtractionPipeline(session, ai=None).run_for_page(page.id)
                await session.commit()

                ev = ClassifiedEvent(
                    kind="inbound_message",
                    account_id="acc-1",
                    ig_user_id="buyer-1",
                    text="رژلب مات چنده؟",
                )
                # حالت shadow (use_catalog=False): پیام استاتیک برمی‌گردد ولی پیش‌نویس ساخته می‌شود
                r_shadow, draft = await build_catalog_auto_reply(
                    session, ev, fallback_text="ثبت شد", use_catalog=False
                )
                # حالت فعال (use_catalog=True): پاسخ مدرک‌محور جایگزین می‌شود
                r_live, _ = await build_catalog_auto_reply(
                    session, ev, fallback_text="ثبت شد", use_catalog=True
                )
                return r_shadow, draft, r_live

        r_shadow, draft, r_live = asyncio.run(_run())
        assert r_shadow is not None and r_shadow.message == "ثبت شد"
        assert draft is not None and "480,000" in draft
        assert r_live is not None and "480,000" in r_live.message
    finally:
        cfg.get_settings.cache_clear()
        dbmod._engine = None
        dbmod._session_factory = None


def test_crawl_holdout_checks_quota_and_enforces_diversity(tmp_path: Path) -> None:
    """جمع‌آوری بچ ۲: توقف فوری در سهمیه صفر + فیلتر dev + برچسب‌زنی کور + تنوع فروشنده."""
    from peyja.ingest.holdout import discover_holdout_batch2

    gold_file = tmp_path / "samples.json"
    gold_file.write_text(
        json.dumps([{"id": "gold-001", "caption": "کپشن قدیمی"}], ensure_ascii=False),
        encoding="utf-8",
    )

    class _FakeDataProvider:
        def __init__(self, left: int) -> None:
            self.left = left
            self.hashtag_calls = 0

        async def account_status(self) -> dict[str, Any]:
            return {
                "username": "09967806959",
                "request_used": 10 - self.left if self.left <= 10 else 0,
                "request_limit": 10 if self.left == 0 else 2000,
                "request_left": self.left,
                "plan_expiration": "2026-12-30",
            }

        async def hashtag_media(self, tag: str) -> list[dict[str, Any]]:
            self.hashtag_calls += 1
            return [
                {
                    "pk": "1",
                    "user": {"username": "farmashop_aras"},  # باید حذف شود (فروشنده‌ی dev)
                    "caption": "کرم آبرسان ۵۵۰ تومان — باید حذف شود چون در dev است",
                },
                {
                    "pk": "2",
                    "user": {"username": "shop_alpha"},
                    "caption": "سرم نیاسینامید اوردینری قیمت ۶۹۰ هزار تومان ارسال فوری",
                },
                {
                    "pk": "3",
                    "user": {"username": "shop_alpha"},
                    "caption": "تونر پیکسی اصل حجم ۲۵۰ میل قیمت ۹۸۰ هزار تومان",
                },
                {
                    "pk": "4",
                    "user": {"username": "shop_beta"},
                    "caption": "بالم لب وازلین اورجینال موجود شد قیمت ۲۹۰ تومان",
                },
                {
                    "pk": "5",
                    "user": {"username": "shop_gamma"},
                    "caption": "ادکلن میس دیور ادوپرفیوم قیمت ۴/۸۰ میلیون تومان",
                },
            ]

    # ۱) سهمیه صفر ⇒ حتی یک فراخوانی hashtag نباید مصرف شود
    p0 = _FakeDataProvider(left=0)
    res0 = asyncio.run(
        discover_holdout_batch2(
            p0,  # type: ignore[arg-type]
            gold_path=gold_file,
            out_candidates_path=tmp_path / "b2-zero.json",
            out_sheet_path=tmp_path / "b2-zero.html",
        )
    )
    assert res0["ok"] is False and p0.hashtag_calls == 0

    # ۲) سهمیه باز ⇒ استخراج کور (`split="test"`) + حذف dev + سقف هر فروشنده
    p_ok = _FakeDataProvider(left=500)
    cand_path = tmp_path / "b2.json"
    sheet_path = tmp_path / "b2.html"
    res1 = asyncio.run(
        discover_holdout_batch2(
            p_ok,  # type: ignore[arg-type]
            gold_path=gold_file,
            out_candidates_path=cand_path,
            out_sheet_path=sheet_path,
            hashtags=("قیمت",),
            target=4,
            max_per_seller=1,
        )
    )
    assert res1["ok"] is True
    assert res1["collected"] == 3  # 1 از هر فروشنده (alpha محدود به 1 شد + farmashop حذف شد)
    assert res1["distinct_sellers"] == 3
    rows = json.loads(cand_path.read_text(encoding="utf-8"))
    assert all(r["split"] == "test" for r in rows)
    assert all(r["price_toman"] is None and r["category"] is None for r in rows)  # برچسب‌زنی کور!
    assert "farmashop_aras" not in {r["source_username"] for r in rows}
    assert sheet_path.exists()
