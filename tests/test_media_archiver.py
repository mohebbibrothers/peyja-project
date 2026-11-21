"""تست‌های آرشیو رسانه — دانلود، هش، حذف تکراری (بدون شبکه‌ی واقعی)."""

import asyncio
import hashlib

import httpx
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from peyja.ingest.media import MediaArchiver, media_urls_from_raw
from peyja.models import Base, RawItem

FAKE_IMG = b"\xff\xd8\xff\xe0fake-jpeg-bytes"


def _make_session():
    engine = create_async_engine("sqlite+aiosqlite://")

    async def setup():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        return async_sessionmaker(engine, expire_on_commit=False)()

    return engine, asyncio.run(setup())


def test_archive_and_dedupe(tmp_path):
    async def run():
        engine, session = await _make_session_async()
        raw = RawItem(page_id=1, kind="media", ig_id="m1", raw={})
        # page_id بدون وجود Page — فقط create_all جداول؛ FK در sqlite اجرا نمی‌شود
        session.add(raw)
        await session.flush()

        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return httpx.Response(200, content=FAKE_IMG)

        client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        archiver = MediaArchiver(session, client=client, media_dir=tmp_path)

        saved1 = await archiver.archive_urls(
            raw, ["https://cdn.example/a.jpg", "https://cdn.example/a.jpg"]
        )
        saved2 = await archiver.archive_urls(raw, ["https://cdn.example/a.jpg"])
        await archiver.aclose()
        await session.close()
        await engine.dispose()
        return saved1, saved2, calls

    async def _make_session_async():
        engine = create_async_engine("sqlite+aiosqlite://")
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        return engine, async_sessionmaker(engine, expire_on_commit=False)()

    saved1, saved2, calls = asyncio.run(run())
    sha = hashlib.sha256(FAKE_IMG).hexdigest()
    assert len(saved1) == 1  # URL تکراری در یک فراخوانی فقط یک بار
    assert saved1[0].sha256 == sha
    assert saved2[0].sha256 == sha  # هش تکراری → همان رکورد قبلی
    assert calls["n"] == 1  # فقط یک دانلود واقعی
    assert (tmp_path / sha[:2] / f"{sha}.jpg").exists()


def test_media_urls_from_raw_carousel():
    raw = {
        "image_versions2": {
            "candidates": [{"url": "https://cdn/x1.jpg"}, {"url": "https://cdn/x0.jpg"}]
        },
        "carousel_media": [
            {"image_versions2": {"candidates": [{"url": "https://cdn/c1.jpg"}]}},
        ],
    }
    urls = media_urls_from_raw(raw)
    assert urls == ["https://cdn/x1.jpg", "https://cdn/x0.jpg", "https://cdn/c1.jpg"]
