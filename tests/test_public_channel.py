"""تست‌های واحد کرالر کانال‌های عمومی (`public_channel.py`) و سازگاری تنظیمات."""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from peyja.config import Settings
from peyja.ingest.public_channel import PublicChannelCrawler, parse_public_channel_html
from peyja.models import Base, MediaFile, Page, RawItem

_SAMPLE_PAGE_1 = """
<section>
  <link data-before="100" />
  <div class="tgme_widget_message_wrap js-widget_message_wrap">
    <div class="tgme_widget_message" data-post="shop_a/100">
      <a class="tgme_widget_message_photo_wrap" style="background-image:url('https://cdn4.telesco.pe/file/real-photo-1.jpg')"></a>
      <div class="tgme_widget_message_text js-message_text">
        ریمل حجم دهنده شیگلم<i class="emoji" style="background-image:url('//telegram.org/img/emoji/40/F09F9284.png')"></i><br/>قیمت: ۳۸۰,۰۰۰ تومان<br/>سفارش دایرکت
      </div>
      <time datetime="2026-11-26T15:01:02+00:00"></time>
    </div>
  </div>
  <div class="tgme_widget_message_wrap js-widget_message_wrap">
    <div class="tgme_widget_message" data-post="shop_a/101">
      <div class="tgme_widget_message_text js-message_text">سلام</div>
      <time datetime="2026-11-26T16:00:00+00:00"></time>
    </div>
  </div>
</section>
"""

_SAMPLE_PAGE_0 = """
<section>
  <div class="tgme_widget_message_wrap js-widget_message_wrap">
    <div class="tgme_widget_message" data-post="shop_a/99">
      <a class="tgme_widget_message_photo_wrap" style="background-image:url('https://cdn4.telesco.pe/file/real-photo-0.jpg')"></a>
      <div class="tgme_widget_message_text js-message_text">ضد آفتاب سنتلا ۵۰ میل | قیمت ۴۹۰ هزار تومان</div>
      <time datetime="2026-11-25T10:00:00+00:00"></time>
    </div>
  </div>
</section>
"""

# JPEG magic bytes معتبر تا _guess_ext پسوند jpg بدهد
_FAKE_JPEG = b"\xff\xd8\xff\xe0" + b"0" * 32


def test_parse_public_channel_html_extracts_caption_and_ignores_emoji_urls() -> None:
    posts, prev_cursor = parse_public_channel_html(_SAMPLE_PAGE_1, "shop_a")
    assert prev_cursor == "100"
    assert len(posts) == 1  # پست خیلی کوتاه «سلام» رد می‌شود
    post = posts[0]
    assert post["pk"] == "shop_a/100"
    assert post["user"]["username"] == "shop_a"
    assert "قیمت: ۳۸۰,۰۰۰ تومان" in post["caption"]["text"]
    assert "\n" in post["caption"]["text"]
    cands = post["image_versions2"]["candidates"]
    assert len(cands) == 1
    assert cands[0]["url"] == "https://cdn4.telesco.pe/file/real-photo-1.jpg"


def test_public_channel_crawler_paginates_archives_and_is_idempotent(tmp_path: Path) -> None:
    async def _run() -> None:
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        Session = async_sessionmaker(engine, expire_on_commit=False)

        def handler(request: httpx.Request) -> httpx.Response:
            u = str(request.url)
            if u.endswith("/shop_a"):
                return httpx.Response(200, text=_SAMPLE_PAGE_1)
            if u.endswith("/shop_a?before=100"):
                return httpx.Response(200, text=_SAMPLE_PAGE_0)
            if "telesco.pe" in u:
                # بایت متفاوت برای هر تصویر تا sha256 یکتا شود
                suffix = b"A" if u.endswith("1.jpg") else b"B"
                return httpx.Response(200, content=_FAKE_JPEG + suffix)
            return httpx.Response(404)

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client, Session() as session:
            crawler = PublicChannelCrawler(
                session,
                client=client,
                request_delay=0.0,
                media_dir=tmp_path / "media",
            )
            res1 = await crawler.crawl_channel("shop_a", max_posts=5, download_media=True)
            assert res1["fetched"] == 2
            assert res1["media_downloaded"] == 2
            assert res1["pages_fetched"] == 2

            # تکرار مجدد نباید رکورد یا فایل تکراری بسازد
            res2 = await crawler.crawl_channel("shop_a", max_posts=5, download_media=True)
            assert res2["fetched"] == 2

            page_cnt = (await session.execute(select(func.count(Page.id)))).scalar_one()
            raw_cnt = (await session.execute(select(func.count(RawItem.id)))).scalar_one()
            mf_cnt = (await session.execute(select(func.count(MediaFile.id)))).scalar_one()
            assert page_cnt == 1
            assert raw_cnt == 2
            assert mf_cnt == 2
        await engine.dispose()

    asyncio.run(_run())


def test_webhook_secret_accepts_both_env_names(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BOXAPI_WEBHOOK_SECRET", raising=False)
    monkeypatch.setenv("WEBHOOK_SECRET", "whsec_alias_ok")
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.boxapi_webhook_secret == "whsec_alias_ok"
    assert s.webhook_secret_key == "alias_ok"
