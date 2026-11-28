"""تست‌های آفلاین کرالر سبک اینستاگرام (`ig_lite`) — GraphQL 2026، `data-sjs`، REST و بچ ۲."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx

from peyja.ingest.ig_lite import (
    IgLiteCrawler,
    collect_media_nodes_from_tree,
    extract_tokens_from_html,
    ingest_ig_raw_items,
    normalize_ig_web_item,
    safe_json_loads,
)


def test_normalize_ig_v1_and_graphql_shapes() -> None:
    """قالب پاسخ وب اینستاگرام (v1، GraphQL ۲۰۲۶ `edges[].node` و GraphQL قدیمی) باید یکدست شود."""
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


def test_safe_json_tokens_and_tree_walker() -> None:
    """حذف پیشوند `for (;;);`، کشف `fb_dtsg`/`lsd`/`doc_id` از HTML و استخراج گره‌های پست."""
    assert safe_json_loads('for (;;);{"ok":true}') == {"ok": True}
    assert safe_json_loads("<html>not json</html>") is None

    html = (
        '"DTSGInitialData",[],{"token":"DTSG_XYZ123"},'
        '"LSD",[],{"token":"LSD_999"},'
        '"csrf_token":"CSRF_ABC",'
        '"PolarisProfilePostsQuery","id":"39902305416081839"'
    )
    toks = extract_tokens_from_html(html)
    assert toks["fb_dtsg"] == "DTSG_XYZ123"
    assert toks["lsd"] == "LSD_999"
    assert toks["csrftoken"] == "CSRF_ABC"
    assert ("PolarisProfilePostsQuery", "39902305416081839") in toks["doc_ids"]

    payload = {
        "data": {
            "xdt_api__v1__feed__user_timeline_graphql_connection": {
                "edges": [
                    {
                        "node": {
                            "pk": "701",
                            "code": "C701",
                            "caption": {"text": "ریمل ایزادورا ۹۹۰ تومان"},
                            "user": {"pk": "9", "username": "rozhashop.ir"},
                            "image_versions2": {"candidates": [{"url": "https://cdn/701.jpg"}]},
                            "carousel_media": [
                                {"pk": "701_child", "image_versions2": {"candidates": []}}
                            ],
                        }
                    }
                ]
            }
        }
    }
    nodes = collect_media_nodes_from_tree(payload)
    assert len(nodes) == 1
    assert nodes[0]["pk"] == "701"


def test_ig_lite_crawler_graphql_and_html_sjs_2026() -> None:
    """مسیر واقعی ۲۰۲۶: برداشت توکن از HTML + `POST /graphql/query` با `for (;;);` و کش کردن مسیر برنده."""
    calls: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        calls.append((request.method, path))
        if path == "/dokhtaronee_shop/":
            # صفحه‌ی HTML حاوی fb_dtsg/lsd/doc_id بدون پست توکار تا مسیر GraphQL تست شود
            html = (
                '<html><script>"DTSGInitialData",[],{"token":"DTSG_LIVE"},'
                '"LSD",[],{"token":"LSD_LIVE"},'
                '"PolarisProfilePostsQuery","id":"39902305416081839"</script></html>'
            )
            return httpx.Response(200, text=html)
        if path == "/graphql/query":
            body_text = request.content.decode("utf-8")
            assert "fb_dtsg=DTSG_LIVE" in body_text
            if "dokhtaronee_shop" in body_text:
                gql = {
                    "data": {
                        "xdt_api__v1__feed__user_timeline_graphql_connection": {
                            "edges": [
                                {
                                    "node": {
                                        "pk": "111",
                                        "code": "D111",
                                        "caption": {"text": "تینت لب شیگلم ۴۲۰ هزار تومان"},
                                        "user": {"pk": "1", "username": "dokhtaronee_shop"},
                                        "image_versions2": {
                                            "candidates": [{"url": "https://img/d111.jpg"}]
                                        },
                                    }
                                }
                            ]
                        }
                    }
                }
                return httpx.Response(200, text="for (;;);" + json.dumps(gql, ensure_ascii=False))
            if "isatiis_cosmetics" in body_text:
                gql2 = {
                    "data": {
                        "xdt_api__v1__feed__user_timeline_graphql_connection": {
                            "edges": [
                                {
                                    "node": {
                                        "pk": "222",
                                        "code": "I222",
                                        "caption": {"text": "کرم آبرسان کلینیک ۱/۸۵۰ تومان"},
                                        "user": {"pk": "2", "username": "isatiis_cosmetics"},
                                        "image_versions2": {
                                            "candidates": [{"url": "https://img/i222.jpg"}]
                                        },
                                    }
                                }
                            ]
                        }
                    }
                }
                return httpx.Response(200, text="for (;;);" + json.dumps(gql2, ensure_ascii=False))
        return httpx.Response(404, text="not found")

    async def _run() -> dict[str, Any]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            crawler = IgLiteCrawler(sessionid="test_sid", client=client, delay_range=(0.0, 0.0))
            return await crawler.crawl_shops(["dokhtaronee_shop", "isatiis_cosmetics"], per_shop=5)

    res = asyncio.run(_run())
    assert res["ok"] is True
    assert res["count"] == 2
    assert res["sellers"] == {"dokhtaronee_shop": 1, "isatiis_cosmetics": 1}
    # فروشگاه دوم باید مستقیماً از _winning_gql برود و دیگر HTML نگیرد
    assert ("GET", "/isatiis_cosmetics/") not in calls


def test_ig_lite_crawler_data_sjs_rest_fallback_and_429_stop() -> None:
    """پشتیبانی از `data-sjs` توکار در HTML، پشتیبانِ REST و توقف محترمانه در ۴۲۹."""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/shop_sjs/":
            embedded = {
                "require": [
                    [
                        "ScheduledServerJS",
                        "handle",
                        None,
                        [
                            {
                                "__bbox": {
                                    "xdt_api__v1__feed__user_timeline_graphql_connection": {
                                        "edges": [
                                            {
                                                "node": {
                                                    "pk": "101",
                                                    "code": "S101",
                                                    "caption": {
                                                        "text": "ریمل اسنس اصل ۵۵۰ تومان #آرایشی"
                                                    },
                                                    "user": {"pk": "1", "username": "shop_sjs"},
                                                    "image_versions2": {
                                                        "candidates": [{"url": "https://img/1.jpg"}]
                                                    },
                                                }
                                            }
                                        ]
                                    }
                                }
                            }
                        ],
                    ]
                ]
            }
            html = (
                '<html><script type="application/json" data-sjs>'
                + json.dumps(embedded, ensure_ascii=False)
                + "</script></html>"
            )
            return httpx.Response(200, text=html)
        if path == "/shop_two/":
            return httpx.Response(404, text="no html")
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
        if path == "/shop_three/":
            return httpx.Response(429, text="rate limit")
        return httpx.Response(404, text="not found")

    async def _run() -> dict[str, Any]:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            crawler = IgLiteCrawler(sessionid="test_sid", client=client, delay_range=(0.0, 0.0))
            return await crawler.crawl_shops(
                ["farmashop_aras", "shop_sjs", "shop_two", "shop_three"], per_shop=5
            )

    res = asyncio.run(_run())
    assert res["ok"] is True
    assert res["count"] == 2
    assert "farmashop_aras" not in res["sellers"]
    assert res["sellers"] == {"shop_sjs": 1, "shop_two": 1}
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
            "user": {"pk": 12, "username": "rozhia.shop"},
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
