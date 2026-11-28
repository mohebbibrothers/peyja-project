"""جمع‌آورِ خودکارِ بچ ۲ (`split=test` holdout — قانون ۵).

این ماژول پس از فعال‌بودن سهمیه‌ی Data API:
1. ابتدا سهمیه‌ی `/api/my` را چک می‌کند تا درخواست بی‌خود نسوزد.
2. از هشتگ‌های هدف (`#قیمت`, `#موجودی`, `#تخفیف`, `#لوازم_آرایشی`) پیج‌های تازه کشف می‌کند.
3. فروشندگانِ `dev` (`farmashop_aras`) را کنار می‌گذارد و سقف ۳۰٪ برای هر فروشنده را رعایت می‌کند.
4. خروجی را به‌صورت کاملاً **کور** (بدون برچسبِ موتور، `split="test"`) + برگه‌ی تأیید HTML ذخیره می‌کند.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from peyja.evals import render_verification_html
from peyja.provider.boxapi_data import BoxApiDataProvider

_DEFAULT_HASHTAGS = ("قیمت", "موجودی", "تخفیف", "لوازم_آرایشی")
_PRICE_HINT_RE = re.compile(
    r"(?:\d{2,3}(?:[,٬،]\d{3})|\d+/\d{2}|تومان|تومن|هزار|میلیون|دایرکت|توافقی)"
)


def _extract_caption(item: dict[str, Any]) -> str:
    cap = item.get("caption")
    if isinstance(cap, dict):
        return str(cap.get("text") or "").strip()
    return str(cap or "").strip()


def _extract_username(item: dict[str, Any]) -> str:
    user = item.get("user") or {}
    return str(user.get("username") or item.get("username") or "").strip().lower()


def build_holdout_from_raw(
    raw_items: list[dict[str, Any]],
    *,
    gold_path: Path,
    out_candidates_path: Path,
    out_sheet_path: Path,
    target: int = 25,
    max_per_seller: int = 7,
    exclude_usernames: set[str] | None = None,
    calls_used: int = 0,
) -> dict[str, Any]:
    """انتخاب نمونه‌های کورِ بچ ۲ (`split=test`) با تنوع فروشنده از هر منبع (Data API یا فایل)."""
    existing = json.loads(gold_path.read_text(encoding="utf-8")) if gold_path.exists() else []
    seen_captions = {str(s.get("caption") or "").strip() for s in existing}
    excluded = {u.lower() for u in (exclude_usernames or {"farmashop_aras"})}
    next_idx = len(existing) + 1

    raw_pool: list[dict[str, Any]] = []
    for it in raw_items:
        cap = _extract_caption(it)
        uname = _extract_username(it)
        if not cap or len(cap) < 20 or uname in excluded or not uname:
            continue
        if cap in seen_captions:
            continue
        raw_pool.append(
            {
                "username": uname,
                "ig_media_id": str(it.get("pk") or it.get("id") or it.get("ig_media_id") or ""),
                "caption": cap,
                "image_path": str(it.get("image_path") or "") or None,
                "has_price_hint": bool(_PRICE_HINT_RE.search(cap)),
                "tag": str(it.get("_tag") or it.get("tag") or "imported"),
            }
        )

    raw_pool.sort(key=lambda r: (not r["has_price_hint"], r["username"]))

    selected: list[dict[str, Any]] = []
    seller_counts: Counter[str] = Counter()
    for row in raw_pool:
        if len(selected) >= target:
            break
        cap = row["caption"]
        uname = row["username"]
        if cap in seen_captions or seller_counts[uname] >= max_per_seller:
            continue
        seen_captions.add(cap)
        seller_counts[uname] += 1
        entry: dict[str, Any] = {
            "id": f"gold-{next_idx:03d}",
            "source_username": uname,
            "ig_media_id": row["ig_media_id"],
            "split": "test",
            "caption": cap,
            # قانون ۵ (§۲.۴): برچسب‌زنی کور — هیچ خروجی موتوری نوشته نمی‌شود
            "price_toman": None,
            "old_price_toman": None,
            "category": None,
            "brand": None,
            "colors": [],
            "shades": [],
            "sizes": [],
            "volume_ml": None,
            "spf": None,
            "stock_state": "unknown",
            "product_name": None,
            "products": [],
            "multi_product": False,
            "notes": f"بچ ۲ (#{row['tag']}) — در انتظار برچسب‌زنی کور",
        }
        if row.get("image_path"):
            entry["image_path"] = row["image_path"]
        selected.append(entry)
        next_idx += 1

    out_candidates_path.parent.mkdir(parents=True, exist_ok=True)
    out_candidates_path.write_text(
        json.dumps(selected, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    out_sheet_path.write_text(render_verification_html(selected), encoding="utf-8")

    return {
        "ok": True,
        "collected": len(selected),
        "distinct_sellers": len(seller_counts),
        "sellers": dict(seller_counts),
        "calls_used": calls_used,
        "candidates_path": str(out_candidates_path),
        "sheet_path": str(out_sheet_path),
    }


async def discover_holdout_batch2(
    provider: BoxApiDataProvider,
    *,
    gold_path: Path,
    out_candidates_path: Path,
    out_sheet_path: Path,
    hashtags: tuple[str, ...] = _DEFAULT_HASHTAGS,
    target: int = 25,
    max_per_seller: int = 7,
    exclude_usernames: set[str] | None = None,
) -> dict[str, Any]:
    """جمع‌آوریِ کورِ نمونه‌های `split=test` برای بچ ۲ از فروشندگان دیده‌نشده."""
    status = await provider.account_status()
    left = status.get("request_left")
    if left is not None and int(left) <= 0:
        return {
            "ok": False,
            "error": (
                f"سهمیه‌ی Data API تمام شده است (username={status.get('username')}, "
                f"used={status.get('request_used')}/{status.get('request_limit')}, "
                f"left={left}, expire={status.get('plan_expiration')})"
            ),
            "quota": status,
        }

    raw_items: list[dict[str, Any]] = []
    calls_used = 0
    for tag in hashtags:
        items = await provider.hashtag_media(tag)
        calls_used += 1
        for it in items:
            raw_items.append({**it, "_tag": tag})

    return build_holdout_from_raw(
        raw_items,
        gold_path=gold_path,
        out_candidates_path=out_candidates_path,
        out_sheet_path=out_sheet_path,
        target=target,
        max_per_seller=max_per_seller,
        exclude_usernames=exclude_usernames,
        calls_used=calls_used,
    )
