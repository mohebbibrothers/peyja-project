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


def test_seller_review_echo_learning_and_relevance(tmp_path: Path, monkeypatch: Any) -> None:
    """اثبات کامل قانون صفر در MVP: تأیید فروشنده، رتبه‌بندی پرس‌وجو و یادگیری قیمت از echo DM."""
    import peyja.config as cfg
    import peyja.db as dbmod
    from peyja.extract.pipeline import ExtractionPipeline
    from peyja.loop.dispatcher import (
        ClassifiedEvent,
        classify_events,
        learn_from_seller_echo,
        record_inbound,
    )
    from peyja.models import Evidence, Page, RawItem

    monkeypatch.setenv("DB_PATH", str(tmp_path / "echo.db"))
    cfg.get_settings.cache_clear()
    dbmod._engine = None
    dbmod._session_factory = None
    try:

        async def _setup() -> dict[str, int]:
            await dbmod.ensure_schema()
            factory = dbmod.get_session_factory()
            async with factory() as session:
                page = Page(username="demo_shop", ig_user_id="999", source="data")
                session.add(page)
                await session.flush()

                session.add_all(
                    [
                        RawItem(
                            page_id=page.id,
                            kind="media",
                            ig_id="p1",
                            raw={"caption": "ریمل اسنس موجود شد"},  # candidate بدون قیمت
                        ),
                        RawItem(
                            page_id=page.id,
                            kind="media",
                            ig_id="p2",
                            raw={
                                "caption": "کرم آبرسان نیتروژنا ۹۵۰ هزار تومان"
                            },  # جدیدتر ولی غیرمرتبط
                        ),
                        RawItem(
                            page_id=page.id,
                            kind="media",
                            ig_id="p3",
                            raw={"caption": "قرعه کشی رژگونه استیکی ۵۵۰ هزار تومان"},
                        ),
                    ]
                )
                await session.flush()
                await ExtractionPipeline(session, ai=None).run_for_page(page.id)

                # شبیه‌سازی گفتگو: خریدار می‌پرسد ← فروشنده در echo قیمت می‌دهد
                await record_inbound(
                    session,
                    ClassifiedEvent(
                        kind="inbound_message",
                        account_id="acc-1",
                        ig_user_id="buyer-9",
                        text="سلام ریمل اسنس موجود دارین؟",
                        mid="in-1",
                    ),
                )
                echo_ev = ClassifiedEvent(
                    kind="echo_message",
                    account_id="acc-1",
                    ig_user_id="buyer-9",
                    text="بله ۵۲۰ هزار تومان",
                    mid="out-1",
                )
                await record_inbound(session, echo_ev)
                learned = await learn_from_seller_echo(session, echo_ev)
                await session.commit()
                assert learned is not None and learned["price_toman"] == 520_000
                return learned

        learned = asyncio.run(_setup())
        client = TestClient(app)

        # ریمل اسنس باید با قیمت یادگرفته‌شده و وضعیت تأییدشده اول باشد
        ask = client.get("/api/catalog/ask", params={"q": "قیمت ریمل اسنس چنده؟"}).json()
        assert ask["matched"] is True
        assert "520,000" in ask["reply_text"]
        assert ask["items"][0]["id"] == learned["product_id"]
        assert ask["items"][0]["verified"] is True

        # مرزبندی توکن: «استی» هرگز نباید به «استیکی» بخورد
        no_false = client.get("/api/catalog/ask", params={"q": "فارم استی دارین؟"}).json()
        assert no_false["matched"] is False

        # تأیید/تغییر قیمت از طریق کنسول فروشنده (POST /api/catalog/{id}/review)
        pid = learned["product_id"]
        rev = client.post(
            f"/api/catalog/{pid}/review",
            json={"action": "confirm", "price_toman": 540_000, "reviewer": "owner"},
        )
        assert rev.status_code == 200
        assert rev.json()["item"]["price_toman"] == 540_000
        assert rev.json()["item"]["verified"] is True

        # محصول ردشده دیگر در پاسخ DM ظاهر نمی‌شود
        rej = client.post(f"/api/catalog/{pid}/review", json={"action": "reject"})
        assert rej.status_code == 200 and rej.json()["item"]["status"] == "rejected"
        ask_after_rej = client.get("/api/catalog/ask", params={"q": "قیمت ریمل اسنس چنده؟"}).json()
        assert ask_after_rej["matched"] is False

        missing = client.post("/api/catalog/999999/review", json={"action": "reject"})
        assert missing.status_code == 404

        async def _check_evidence() -> list[Evidence]:
            factory = dbmod.get_session_factory()
            from sqlalchemy import select

            async with factory() as session:
                return (
                    (await session.execute(select(Evidence).where(Evidence.product_id == pid)))
                    .scalars()
                    .all()
                )

        evs = asyncio.run(_check_evidence())
        assert any(e.method == "echo_dm" and e.source_type == "seller_echo" for e in evs)
        assert any(e.method == "human_review" and e.verified_by == "owner" for e in evs)

        # پارس کامل رویداد کامنت
        cev = classify_events(
            {
                "event_type": "comments",
                "account_id": "acc-1",
                "data": {"id": "c-42", "text": "قیمت؟", "from": {"id": "u-7", "username": "ali"}},
            }
        )[0]
        assert cev.kind == "comment" and cev.ig_user_id == "u-7" and cev.mid == "c-42"
    finally:
        cfg.get_settings.cache_clear()
        dbmod._engine = None
        dbmod._session_factory = None


def test_multimodal_pipeline_sends_image_and_falls_back_gracefully(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """پشتیبانی چندوجهی تصویرِ آرشیوشده در ExtractionPipeline + fallback خودکار به متن خالص."""
    import peyja.config as cfg
    import peyja.db as dbmod
    from peyja.extract.pipeline import ExtractionPipeline
    from peyja.models import MediaFile, Page, RawItem

    monkeypatch.setenv("DB_PATH", str(tmp_path / "mm.db"))
    cfg.get_settings.cache_clear()
    dbmod._engine = None
    dbmod._session_factory = None

    img_path = tmp_path / "sample.jpg"
    img_path.write_bytes(b"\xff\xd8\xff\xe0test-jpeg-payload")

    class _MockVisionFallbackAi:
        def __init__(self) -> None:
            self.calls: list[Any] = []

        async def chat(self, messages: list[dict[str, Any]], **kwargs: Any) -> str:
            content = messages[-1]["content"]
            self.calls.append(content)
            # تماس اول شامل image_url است ← شبیه‌سازی رد مدل متنی تا fallback آزموده شود
            if isinstance(content, list):
                raise RuntimeError("vision not supported on mock")
            return json.dumps(
                {
                    "extraction_successful": True,
                    "title_fa": "سرم ویتامین سی",
                    "product_name": "سرم",
                    "category": "skincare",
                    "brand": "گارنیر",
                    "price_toman": 490000,
                    "stock_state": "in_stock",
                    "confidence": 0.9,
                },
                ensure_ascii=False,
            )

        async def extract_from_image(self, image_path: Any, prompt: str, **kwargs: Any) -> str:
            return "{}"

    try:

        async def _run() -> tuple[dict[str, int], list[Any]]:
            await dbmod.ensure_schema()
            factory = dbmod.get_session_factory()
            ai = _MockVisionFallbackAi()
            async with factory() as session:
                page = Page(username="mm_shop", ig_user_id="1", source="data")
                session.add(page)
                await session.flush()
                raw = RawItem(
                    page_id=page.id,
                    kind="media",
                    ig_id="mm-1",
                    raw={"caption": "همه محصولات موجود در پیج ✅"},
                )
                session.add(raw)
                await session.flush()
                session.add(
                    MediaFile(
                        raw_item_id=raw.id,
                        url_original="https://example.com/1.jpg",
                        sha256="abc",
                        local_path=str(img_path),
                    )
                )
                await session.flush()
                stats = await ExtractionPipeline(session, ai=ai).run_for_page(page.id)
                return stats, ai.calls

        stats, calls = asyncio.run(_run())
        assert stats["products"] == 1
        assert isinstance(calls[0], list) and any(p.get("type") == "image_url" for p in calls[0])
        assert isinstance(calls[1], str)  # fallback به متن خالص بدون از دست دادن نمونه
    finally:
        cfg.get_settings.cache_clear()
        dbmod._engine = None
        dbmod._session_factory = None


def test_import_holdout_cli_e2e(tmp_path: Path) -> None:
    """اجرای فرمان import-holdout برای تولید برگه‌ی کور بچ ۲ بدون Data API."""
    from typer.testing import CliRunner

    from apps.cli.main import app as cli_app

    src = tmp_path / "raw_posts.json"
    src.write_text(
        json.dumps(
            [
                {"username": "farmashop_aras", "caption": "کپشن تکراری dev با ۵۵۰ هزار تومان"},
                {
                    "username": "new_beauty_1",
                    "caption": "ضدآفتاب ایزیدین فیوژن واتر ۱/۲۵ میلیون تومان",
                },
                {"username": "new_beauty_2", "caption": "ریمل اسنس صورتی اصل قیمت ۴۸۰ هزار تومان"},
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    gold = tmp_path / "gold.json"
    gold.write_text("[]", encoding="utf-8")
    out_cand = tmp_path / "cand.json"
    out_sheet = tmp_path / "sheet.html"

    res = CliRunner().invoke(
        cli_app,
        [
            "import-holdout",
            str(src),
            "--gold",
            str(gold),
            "--out",
            str(out_cand),
            "--sheet",
            str(out_sheet),
        ],
    )
    assert res.exit_code == 0, res.output
    rows = json.loads(out_cand.read_text(encoding="utf-8"))
    assert len(rows) == 2
    assert all(r["split"] == "test" and r["category"] is None for r in rows)
    assert out_sheet.exists()


def test_echo_anti_loop_comment_private_reply_cards_and_official_posts(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """ضدِ حلقه‌ی بازخورد روی echo، private_reply روی کامنت، کارت‌های قانون صفر و جذب پست‌های رسمی."""
    import time

    from sqlalchemy import select

    import peyja.config as cfg
    import peyja.db as dbmod
    from peyja.extract.pipeline import ExtractionPipeline
    from peyja.ingest.webhook import sign_payload
    from peyja.loop.dispatcher import ClassifiedEvent, learn_from_seller_echo, record_inbound
    from peyja.loop.reply import build_catalog_auto_reply, build_catalog_cards
    from peyja.models import ExtractionRun, Page, Product, RawItem

    monkeypatch.setenv("DB_PATH", str(tmp_path / "loop.db"))
    monkeypatch.setenv("BOXAPI_WEBHOOK_SECRET", "whsec_round5")
    monkeypatch.setenv("CATALOG_AUTO_REPLY", "false")
    cfg.get_settings.cache_clear()
    dbmod._engine = None
    dbmod._session_factory = None
    try:

        async def _verify_loop() -> None:
            await dbmod.ensure_schema()
            factory = dbmod.get_session_factory()
            async with factory() as session:
                page = Page(
                    username="official_shop",
                    ig_user_id="777",
                    source="official",
                    account_id="acc-7",
                )
                session.add(page)
                await session.flush()
                session.add(
                    RawItem(
                        page_id=page.id,
                        kind="media",
                        ig_id="op-1",
                        raw={"caption": "سرم ویتامین سی گارنیر موجوده"},
                    )
                )
                await session.flush()

                s1 = await ExtractionPipeline(session, ai=None).run_for_page(page.id)
                assert s1["products"] == 1
                # اجرای مجددِ بدون reprocess نباید تکراری بسازد
                s2 = await ExtractionPipeline(session, ai=None).run_for_page(page.id)
                assert s2["skipped"] == 1 and s2["products"] == 0

                # ۱) پیامِ خودکار ربات به شکل echo برگردد → هرگز Offer تکراری نسازد
                bot_echo = ClassifiedEvent(
                    kind="echo_message",
                    account_id="acc-7",
                    ig_user_id="u-1",
                    text="سلام 🌸 درباره‌ی «سرم»: قیمت تأییدشده‌ی ثبت‌شده 520,000 تومان است.",
                    mid="bot-echo-1",
                )
                assert await learn_from_seller_echo(session, bot_echo) is None

                # ۲) echo برهنه بدون زمینه‌ی قبلی و نام کالا → به کالای بی‌ربط نچسبد
                bare_echo = ClassifiedEvent(
                    kind="echo_message",
                    account_id="acc-7",
                    ig_user_id="u-nobody",
                    text="بله ۵۲۰ هزار تومان",
                    mid="bare-echo-1",
                )
                assert await learn_from_seller_echo(session, bare_echo) is None

                # ۳) echo واقعیِ فروشنده + idempotency روی mid
                await record_inbound(
                    session,
                    ClassifiedEvent(
                        kind="inbound_message",
                        account_id="acc-7",
                        ig_user_id="buyer-1",
                        text="قیمت سرم گارنیر چنده؟",
                        mid="in-7",
                    ),
                )
                seller_echo = ClassifiedEvent(
                    kind="echo_message",
                    account_id="acc-7",
                    ig_user_id="buyer-1",
                    text="بله ۶۴۰ هزار تومان",
                    mid="seller-echo-1",
                )
                learned1 = await learn_from_seller_echo(session, seller_echo)
                learned2 = await learn_from_seller_echo(session, seller_echo)  # تکراری با همین mid
                assert learned1 is not None and learned1["price_toman"] == 640_000
                assert learned2 is None

                # ۴) reprocess نباید محصول تأییدشده‌ی seller_echo را حذف کند
                await ExtractionPipeline(session, ai=None).run_for_page(page.id, reprocess=True)
                prods = (await session.execute(select(Product))).scalars().all()
                assert len(prods) == 1 and prods[0].status == "confirmed"
                runs = (await session.execute(select(ExtractionRun))).scalars().all()
                assert len(runs) >= 1 and runs[-1].pipeline_version.startswith("spine-")

                # ۴.۵) اگر health() مغز محلی down باشد، pipeline فوراً به آفلاین برمی‌گردد (صفر فراخوانیِ آویزان)
                class _UnhealthyAi:
                    calls = 0

                    async def health(self) -> dict[str, Any]:
                        return {"ok": False, "error": "down"}

                    async def chat(self, *a: Any, **k: Any) -> str:
                        self.calls += 1
                        return "{}"

                    async def extract_from_image(self, *a: Any, **k: Any) -> str:
                        return ""

                dead_ai = _UnhealthyAi()
                await ExtractionPipeline(session, ai=dead_ai).run_for_page(page.id, reprocess=True)
                assert dead_ai.calls == 0

                # ۵) پاسخ private_reply روی کامنت و کارت‌های قانون صفر
                cev = ClassifiedEvent(
                    kind="comment",
                    account_id="acc-7",
                    ig_user_id="commenter-1",
                    text="قیمت سرم گارنیر؟",
                    mid="cmt-55",
                )
                creply, draft = await build_catalog_auto_reply(
                    session, cev, fallback_text="", use_catalog=True
                )
                assert creply is not None and creply.comment_id == "cmt-55"
                assert draft and "640,000" in creply.message

                cards = build_catalog_cards(
                    [
                        {
                            "title_fa": "کالای تأییدشده",
                            "brand": "گارنیر",
                            "verified": True,
                            "price_toman": 640_000,
                        },
                        {
                            "title_fa": "کالای در صف",
                            "verified": False,
                            "price_toman": 990_000,
                            "stock_state": "unknown",
                        },
                    ]
                )
                assert "640,000" in cards[0]["subtitle"]
                assert "990,000" not in cards[1]["subtitle"]  # قانون صفر روی کارت محصول
                await session.commit()

        asyncio.run(_verify_loop())

        # ۶) وب‌هوک action.list_posts به صورت سرتاسری پست‌های رسمی را در کاتالوگ جذب می‌کند
        client = TestClient(app)
        now_ms = int(time.time() * 1000)
        body = {
            "event_id": f"lp-{now_ms}",
            "event_type": "action.list_posts",
            "account_id": "acc-7",
            "data": {
                "posts": [
                    {
                        "id": "ig-post-99",
                        "caption": "پلت سایه شاین شیگلم اصل قیمت ۷۸۰ هزار تومان",
                    }
                ]
            },
        }
        raw = json.dumps(body, ensure_ascii=False).encode()
        ts, sig = sign_payload("whsec_round5", raw)
        resp = client.post(
            "/webhooks/boxapi",
            content=raw,
            headers={
                "Content-Type": "application/json",
                "X-BoxApi-Signature": f"sha256={sig}",
                "X-BoxApi-Timestamp": ts,
                "X-BoxApi-Event-Id": body["event_id"],
            },
        )
        assert resp.status_code == 200
        ask = client.get("/api/catalog/ask", params={"q": "سایه شیگلم"}).json()
        assert ask["matched"] is True and ask["items"][0]["price_toman"] == 780_000
    finally:
        cfg.get_settings.cache_clear()
        dbmod._engine = None
        dbmod._session_factory = None
