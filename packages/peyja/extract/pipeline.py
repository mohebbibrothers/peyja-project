"""Pipeline استخراج — از raw_items به محصول/واریانت/قیمت + Evidence.

معماری قله‌ی تکنولوژی (۲۰۲۶):
- مسیر سریع regex (رایگان، قطعی) ← مسیر VLM فقط برای نمونه‌های کم‌اعتماد
- خروجی ساخت‌یافته با SafeProductExtraction (مسیر فرار صریح + اعتبارسنجی Pydantic)
- retry با بازخورد خطای اعتبارسنجی، حداکثر ۳ بار، temperature=0
- مسیریابی با بازه‌های اطمینان: ≥0.7 ذخیره | 0.4–0.7 صف انسانی | <0.4 رد
- «استخراج‌شده ≠ تأییدشده» — هر ادعا Evidence دارد
"""

from __future__ import annotations

import json
import re
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from peyja.config import get_settings
from peyja.extract.caption import analyze_caption
from peyja.extract.price_parser import PriceCandidate
from peyja.extract.schema import SafeProductExtraction, route_by_confidence
from peyja.models import CaptionVersion, Evidence, Offer, Product, RawItem, Variant, utcnow
from peyja.provider.boxapi_ai import BoxApiAiProvider

PIPELINE_VERSION = "spine-v0.2"

VLM_SYSTEM_PROMPT = (
    "تو استخراج‌گر داده از کپشن/تصویر فروشندگان ایرانی لوازم آرایشی بهداشتی هستی. "
    "فقط و فقط JSON مطابق اسکیمای داده‌شده خروجی بده. "
    "اگر اطلاعات کافی نیست extraction_successful را false بگذار و هرگز حدس نزن. "
    "قیمت‌ها را به تومان کامل عددی بنویس (مثلا 4800000)."
)

# دسته‌بندی لوازم آرایشی بهداشتی (نیچ جدید پیجا)
CATEGORY_MAP: dict[str, str] = {
    # skincare
    "ضد آفتاب": "skincare",
    "ضدآفتاب": "skincare",
    "کرم": "skincare",
    "سرم": "skincare",
    "تونر": "skincare",
    "میسلار": "skincare",
    "مرطوب": "skincare",
    "آبرسان": "skincare",
    "فیس واش": "skincare",
    "شستشوی صورت": "skincare",
    "ضد چروک": "skincare",
    "ضدجوش": "skincare",
    "ضدلک": "skincare",
    "روشن کننده": "skincare",
    "ماسک صورت": "skincare",
    "اسکراب": "skincare",
    # makeup
    "رژلب": "makeup",
    "رژ لب": "makeup",
    "فوندیشن": "makeup",
    "کرم پودر": "makeup",
    "ریمل": "makeup",
    "خط چشم": "makeup",
    "خطچشم": "makeup",
    "پنکیک": "makeup",
    "کانسیلر": "makeup",
    "هایلایتر": "makeup",
    "سایه": "makeup",
    "لاک": "makeup",
    "برنزه": "makeup",
    "پرایمر": "makeup",
    "بالم لب": "makeup",
    # perfume
    "عطر": "perfume",
    "ادکلن": "perfume",
    "ادوپرفیوم": "perfume",
    "اسپری": "perfume",
    # haircare
    "شامپو": "haircare",
    "نرم کننده": "haircare",
    "ماسک مو": "haircare",
    "روغن مو": "haircare",
    "سرم مو": "haircare",
    "حالت دهنده": "haircare",
    # hygiene
    "ضدعفونی": "hygiene",
    "ژل شستشو": "hygiene",
    "دئودورانت": "hygiene",
    "بهداشتی": "hygiene",
    "پد بهداشتی": "hygiene",
    "دستمال": "hygiene",
}


class ExtractionPipeline:
    def __init__(self, session: AsyncSession, ai: BoxApiAiProvider | None = None):
        self.session = session
        self.ai = ai
        self.settings = get_settings()

    async def run_for_page(self, page_id: int) -> dict[str, Any]:
        result = await self.session.execute(
            select(RawItem).where(RawItem.page_id == page_id, RawItem.kind == "media")
        )
        raw_items = result.scalars().all()
        stats: dict[str, Any] = {
            "processed": 0,
            "products": 0,
            "offers": 0,
            "stored": 0,
            "human_review": 0,
            "rejected": 0,
            "vlm_calls": 0,
        }
        for item in raw_items:
            out = await self.process_raw_item(item, page_id)
            for k in stats:
                stats[k] += out.get(k, 0)
        return stats

    async def process_raw_item(self, item: RawItem, page_id: int) -> dict[str, int]:
        media = item.raw
        caption_text = media.get("caption")
        if isinstance(caption_text, dict):
            caption_text = caption_text.get("text")
        caption_text = caption_text or ""

        self.session.add(CaptionVersion(raw_item_id=item.id, caption_text=caption_text))
        signals = analyze_caption(caption_text)
        regex_price = signals.prices.current
        old_price = signals.prices.old

        # ── ترکیب سیگنال‌ها: regex پرقدرت ← VLM برای ضعیف‌ها ─────────────────
        vlm: SafeProductExtraction | None = None
        vlm_calls = 0
        need_vlm = self.ai is not None and (
            regex_price is None or regex_price.confidence < self.settings.extract_confidence_tau
        )
        if need_vlm:
            vlm_calls = 1
            vlm = await self._vlm_extract_safe(item, caption_text)

        price_toman, method, confidence = _merge_price(regex_price, vlm, signals.prices.on_request)

        title = (vlm.title_fa if vlm and vlm.title_fa else None) or _derive_title(caption_text)
        category = (vlm.category if vlm and vlm.category else None) or _guess_category(
            caption_text + " " + " ".join(signals.hashtags)
        )
        brand = vlm.brand if vlm else None
        colors = (vlm.colors if vlm and vlm.colors else None) or signals.colors
        sizes = (vlm.sizes if vlm and vlm.sizes else None) or signals.sizes
        stock_state = (
            "on_request"
            if signals.prices.on_request
            else ("in_stock" if price_toman else (vlm.stock_state if vlm else "unknown"))
        )

        route = route_by_confidence(confidence, self.settings.extract_review_floor)
        status = {"store": "confirmed", "review": "candidate", "reject": "rejected"}[route]

        stats = {
            "products": 0,
            "offers": 0,
            "vlm_calls": vlm_calls,
            "stored": 0,
            "human_review": 0,
            "rejected": 0,
        }
        stats[{"store": "stored", "review": "human_review", "reject": "rejected"}[route]] += 1

        product = Product(
            page_id=page_id,
            title_fa=title,
            category=category,
            attributes={
                "brand": brand,
                "colors": colors,
                "sizes": sizes,
                "volume_ml": vlm.volume_ml if vlm else None,
                "spf": vlm.spf if vlm else None,
            },
            status=status,
        )
        self.session.add(product)
        await self.session.flush()
        stats["products"] += 1

        variant = Variant(product_id=product.id, options=None)
        self.session.add(variant)
        await self.session.flush()

        offer = Offer(
            variant_id=variant.id,
            price_minor=price_toman,
            stock_state=stock_state,
            valid_from=utcnow(),
            ttl_hours=24,
            stale=False,
        )
        self.session.add(offer)
        await self.session.flush()
        stats["offers"] += 1

        source_type = "vlm" if method == "vlm" else "caption"
        self.session.add(
            Evidence(
                offer_id=offer.id,
                product_id=product.id,
                source_type=source_type,
                source_id=item.ig_id,
                method=method,
                confidence=confidence,
            )
        )
        if old_price and old_price.value_toman:
            self.session.add(
                Evidence(
                    offer_id=offer.id,
                    product_id=product.id,
                    source_type="caption",
                    source_id=item.ig_id,
                    method="price_parser:old_price",
                    confidence=old_price.confidence,
                )
            )
        return stats

    # ── کمکی‌ها ──────────────────────────────────────────────────────────────

    @staticmethod
    def extract_signals_fa(caption_text: str) -> dict[str, Any]:
        """استخراج خالص متنی (بدون DB/VLM/API) — موتور gold-100 آفلاین."""
        signals = analyze_caption(caption_text)
        price, method, conf = _merge_price(signals.prices.current, None, signals.prices.on_request)
        return {
            "price_toman": price,
            "old_price_toman": signals.prices.old.value_toman if signals.prices.old else None,
            "category": _guess_category(caption_text + " " + " ".join(signals.hashtags)),
            "colors": signals.colors,
            "sizes": signals.sizes,
            "stock_state": "on_request"
            if signals.prices.on_request
            else ("in_stock" if price else "unknown"),
            "confidence": conf,
            "method": method,
            "route": route_by_confidence(conf, get_settings().extract_review_floor),
        }

    # ── استخراج ساخت‌یافته‌ی VLM با retry هوشمند ───────────────────────────────
    async def _vlm_extract_safe(
        self, item: RawItem, caption_text: str
    ) -> SafeProductExtraction | None:
        assert self.ai is not None
        schema = SafeProductExtraction.model_json_schema()
        user_prompt = (
            f"اسکیمای JSON:\n{json.dumps(schema, ensure_ascii=False)}\n\n"
            f"کپشن:\n{caption_text or '(بدون کپشن)'}"
        )
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": VLM_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]
        for _attempt in range(3):
            text = await self.ai.chat(messages, temperature=0)
            try:
                result = SafeProductExtraction.model_validate(_defensive_parse(text))
            except (ValidationError, ValueError) as e:
                messages.append(
                    {
                        "role": "user",
                        "content": f"خروجی قبلی نامعتبر بود:\n{e}\nاصلاح کن و فقط JSON بده.",
                    }
                )
                continue
            return result if result.extraction_successful else None
        return None


# ── کمکی‌ها ─────────────────────────────────────────────────────────────────────


def _defensive_parse(text: str) -> dict[str, Any]:
    """parse مقاوم: حصارهای کد، متن اضافه، JSON شکسته."""
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\n?", "", t)
        t = re.sub(r"\n?```$", "", t)
    start, end = t.find("{"), t.rfind("}")
    if start != -1 and end != -1:
        t = t[start : end + 1]
    return json.loads(t)


def _merge_price(
    regex_price: PriceCandidate | None,
    vlm: SafeProductExtraction | None,
    on_request: bool,
) -> tuple[int | None, str, float]:
    """ترکیب قیمت regex و VLM — regex پرقدرت اولویت دارد، seller_echo همیشه برنده."""
    if regex_price is not None and regex_price.confidence >= 0.8 and regex_price.value_toman:
        return regex_price.value_toman, "caption", regex_price.confidence
    if vlm and vlm.price_toman:
        return vlm.price_toman, "vlm", max(vlm.confidence, 0.5)
    if regex_price is not None and regex_price.value_toman:
        return regex_price.value_toman, "caption", regex_price.confidence
    if on_request:
        return None, "on_request", 0.9
    return None, "none", 0.0


def _derive_title(caption: str, max_len: int = 80) -> str:
    first_line = (caption.split("\n")[0] or "").strip()
    first_line = re.sub(r"[#@].*?$", "", first_line).strip()
    return (first_line or "محصول بدون عنوان")[:max_len]


def _guess_category(text: str) -> str | None:
    for fa, en in CATEGORY_MAP.items():
        if fa in text:
            return en
    return None
