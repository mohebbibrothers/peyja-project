"""تست‌های آفلاین کرالر سبک اینستاگرام (`ig_lite`) و ساخت بچ ۲ بدون نشت برچسب."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx

from peyja.ingest.ig_lite import (
    IgLiteCrawler,
    ingest_ig_raw_items,
    normalize_ig_web_item,
)


def test_normalize_ig_v1_and_graphql_shapes() -> None:
    """هر دو قالب پاسخ وب اینستاگرام (v1 items و GraphQL edges) باید به یک قالب واحد دربیایند."""
    v1_item = {
        "pk": "399001",
        "id": "399001_55",
        "code": "ABC123",
        "taken_at": 1782000000,
        "like_count": 42,
        "comment_count": 5,
        "caption": {"text": "سرم ضدلک گارنیر موجود شد ۶۹۰ تومان #سرم"},
        "user": {"pk": 55, "username": "Dokhtaronee_Shop", "full_name": "دخترونه"},
        "image_versions2": {"candidates": [{"url": "https://cdn.example.com/1.jpg"}]},
    }
    n1 = normalize_ig_web_item(v1_item)
    assert n1 is not None
    assert n1["pk"] == "399001"
    assert n1["user"]["username"] == "dokhtaronee_shop"
    assert n1["caption"]["text"].startswith("سرم ضدلک")
    assert n1["image_versions2"]["candidates"][0]["url"] == "https://cdn.example.com/1.jpg"

    gql_node = {
        "id": "888777",
        "shortcode": "XYZ999",
        "taken_at_timestamp": 1782000500,
        "display_url": "https://cdn.example.com/2.jpg",
        "edge_media_to_caption": {"edges": [{"node": {"text": "ادکلن تام فورد ۵/۵۰۰ تومان #عطر"}}]},
        "edge_liked_by": {"count": 19},
        "edge_media_to_comment": {"count": 3},
        "owner": {"id": "77", "username": "meisamatr"},
    }
    n2 = normalize_ig_web_item(gql_node)
    assert n2 is not None
    assert n2["pk"] == "888777"
    assert n2["code"] == "XYZ999"
    assert n2["user"]["username"] == "meisamatr"
    assert n2["like_count"] == 19
    assert n2["comment_count"] == 3
    assert n2["caption"]["text"].startswith("ادکلن تام فورد")


def test_ig_lite_crawler_fallback_and_rate_limit_graceful_stop() -> None:
    """پشتیبانی از fallback به web_profile_info و حفظ داده‌ی قبلی هنگام دریافت 429."""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if "/api/v1/feed/user/shop_one/username/" in path:
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "pk": "101",
                            "code": "C1",
                            "caption": {"text": "ریمل اسنس اصل ۵۵۰ تومان #آرایشی"},
                            "user": {"pk": 1, "username": "shop_one"},
                            "image_versions2": {"candidates": [{"url": "https://img/1.jpg"}]},
                        }
                    ]
                },
            )
        if "/api/v1/feed/user/shop_two/username/" in path:
            return httpx.Response(404, json={"status": "fail"})
        if (
            "/api/v1/users/web_profile_info/" in path
            and request.url.params.get("username") == "shop_two"
        ):
            return httpx.Response(
                200,
                json={
                    "data": {
                        "user": {
                            "id": "2",
                            "username": "shop_two",
                            "edge_owner_to_timeline_media": {
                                "edges": [
                                    {
                                        "node": {
                                            "id": "202",
                                            "shortcode": "C2",
                                            "display_url": "https://img/2.jpg",
                                            "edge_media_to_caption": {
                                                "edges": [
                                                    {"node": {"text": "کرم ضد آفتاب ایزیدین ۴۸۰"}}
                                                ]
                                            },
                                        }
                                    }
                                ]
                            },
                        }
                    }
                },
            )
        if "/api/v1/feed/user/shop_three/username/" in path:
            return httpx.Response(429, json={"message": "rate_limit"})
        raise AssertionError(f"Unexpected request: {request.url}")

    async def _run() -> dict[str, Any]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            crawler = IgLiteCrawler(sessionid="test_sid", client=client, delay_range=(0.0, 0.0))
            return await crawler.crawl_shops(
                ["farmashop_aras", "shop_one", "shop_two", "shop_three"], per_shop=5
            )

    res = asyncio.run(_run())
    assert res["ok"] is True
    assert res["count"] == 2
    assert "farmashop_aras" not in res["sellers"]
    assert res["sellers"] == {"shop_one": 1, "shop_two": 1}
    assert "429" in (res["stopped_reason"] or "")


def test_ingest_ig_raw_items_downloads_media_and_builds_blind_holdout(
    tmp_path: Path, monkeypatch: Any
) -> None:
    """ورود داده‌ی خام اینستاگرام باید تصویر را ذخیره کند و برگه‌ی کور split=test بسازد."""
    import peyja.config as cfg
    import peyja.db as dbmod

    monkeypatch.setenv("DB_PATH", str(tmp_path / "ig_lite.db"))
    monkeypatch.setenv("MEDIA_DIR", str(tmp_path / "media"))
    cfg.get_settings.cache_clear()
    dbmod._engine = None
    dbmod._session_factory = None

    fake_jpg = b"\xff\xd8\xff\xe0" + b"JPEGDATA" * 8

    def media_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=fake_jpg, headers={"Content-Type": "image/jpeg"})

    raw_items = [
        {
            "pk": "501",
            "code": "P501",
            "caption": {"text": "رژگونه مایع شیگلم مدل جدید قیمت ۵۹۰ هزار تومان #آرایشی"},
            "user": {"pk": 11, "username": "rozhashop.ir"},
            "image_versions2": {"candidates": [{"url": "https://cdn.example.com/blush.jpg"}]},
        },
        {
            "pk": "502",
            "code": "P502",
            "caption": {"text": "سرم ویتامین سی روشن کننده پوست قیمت در دایرکت #روتین_پوستی"},
            "user": {"pk": 12, "username": "yona_.cosmetic"},
            "image_versions2": {"candidates": [{"url": "https://cdn.example.com/serum.jpg"}]},
        },
    ]

    gold_path = tmp_path / "samples.json"
    gold_path.write_text("[]", encoding="utf-8")
    cand_path = tmp_path / "batch2-candidates.json"
    sheet_path = tmp_path / "batch2-sheet.html"

    try:

        async def _run() -> dict[str, Any]:
            await dbmod.ensure_schema()
            factory = dbmod.get_session_factory()
            async with (
                httpx.AsyncClient(transport=httpx.MockTransport(media_handler)) as img_client,
                factory() as session,
            ):
                return await ingest_ig_raw_items(
                    session,
                    raw_items,
                    download_media=True,
                    client=img_client,
                    media_dir=tmp_path / "media",
                    build_holdout=True,
                    gold_path=gold_path,
                    out_candidates_path=cand_path,
                    out_sheet_path=sheet_path,
                    holdout_target=10,
                    max_per_seller=5,
                )

        res = asyncio.run(_run())
        assert res["ok"] is True
        assert res["ingested_posts"] == 2
        assert res["media_saved"] == 2
        assert res["holdout"]["collected"] == 2

        cands = json.loads(cand_path.read_text(encoding="utf-8"))
        assert len(cands) == 2
        for c in cands:
            assert c["split"] == "test"
            assert c["category"] is None
            assert c["price_toman"] is None
            assert Path(c["image_path"]).is_file()
        assert sheet_path.is_file()
    finally:
        cfg.get_settings.cache_clear()
        dbmod._engine = None
        dbmod._session_factory = None
