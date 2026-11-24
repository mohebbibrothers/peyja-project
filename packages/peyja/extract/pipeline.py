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
from peyja.extract.catalog import (
    find_brand,
    find_product_category,
    find_product_name,
    split_product_spans,
)
from peyja.extract.price_parser import PriceCandidate
from peyja.extract.schema import SafeProductExtraction, route_by_confidence
from peyja.models import (
    CaptionVersion,
    Evidence,
    Offer,
    Product,
    RawItem,
    Variant,
    utcnow,
)
from peyja.provider.llm import AiProvider

PIPELINE_VERSION = "spine-v0.2"

VLM_SYSTEM_PROMPT = (
    "## نقش (Role)\n"
    "تو «استخراج‌گرِ داده‌ی متخصصِ بازارِ لوازم آرایشی‌بهداشتی ایران» هستی؛ "
    "سال‌ها کپشنِ فروشندگانِ اینستاگرام را خوانده‌ای و با قیمت‌گذاریِ تومانی، شیدها، "
    "واژگانِ بومی («توافقی»، «دایرکت»، «۵۵۰💸») و دام‌های متنی آشنایی کامل داری.\n\n"
    "## پروتکلِ استخراج (گام‌به‌گام — دقیقاً به همین ترتیب اجرا کن)\n"
    "۱. قیمت فعلی: فقط عددی که واقعاً قیمت است. «حجم/وزن/ساعت/تعداد/SPF/شماره» هرگز قیمت نیستند.\n"
    "۲. قیمت قبل از تخفیف (old) فقط با نشانه‌ی صریحِ «از X به Y» یا خط‌خوردگی.\n"
    "۳. دسته: یکی از skincare | makeup | perfume | haircare | hygiene اگر غالب مشخص است.\n"
    "۴. نامِ محصول (عبارتِ کاتالوگی مثل «کرم آبرسان»)، برند، رنگ‌ها/تناژها، "
    "شماره‌ی شید («شماره ۱۲» — نه رنگ نه سایز)، سایز/حجم، SPF.\n"
    "۵. وضعیت موجودی: in_stock | out_of_stock | on_request | unknown.\n"
    "۶. **چندمحصوله:** اگر پست چند محصول دارد، «همه» را در آرایه‌ی products بیاور "
    "(هرکدام با name_fa و مشخصاتِ خودش) و products را خالی نگذار؛ "
    "تک‌محصوله = products خالی و فیلدهای اصلی پر.\n\n"
    "## قواعدِ قیمتِ ایرانی (دانشِ بحرانی)\n"
    "- «X/YY» = X میلیون و Y صدمِ میلیون ⇒ «۴/۸۰» = 4800000\n"
    "- عددِ ۳رقمیِ بدونِ واحد = هزارتایی ⇒ «۵۵۰» یا «۵۵۰💸» = 550000\n"
    "- «از ۹۰۰ به ۷۵۰» ⇒ old=900000 و price=750000\n"
    "- «توافقی/دایرکت/تماس بگیرید» ⇒ price=null و stock_state=on_request\n\n"
    "## دام‌ها (این‌ها را هرگز اشتباه نگیر)\n"
    "- «حجم 250mil» ⇒ حجم است (volume_ml=250)، قیمت نیست.\n"
    "- «۲۴ ساعت ماندگاری» ⇒ عددِ زمانی است، قیمت نیست.\n"
    "- «شماره ۱۲» ⇒ شید است (shades)، نه قیمت و نه سایز.\n"
    "- «پالت ۱۲ رنگ» ⇒ تعدادِ رنگ است، نه شید و نه قیمت.\n"
    "- «قیمت مناسب/خوب» بدونِ عدد ⇒ قیمت وجود ندارد (حدس نزن!).\n\n"
    "## نمونه‌ها (Few-shot)\n"
    "ورودی: «ریمل اسنس موجود شد ۵۵۰💸»\n"
    'خروجی: {"extraction_successful": true, "title_fa": "ریمل اسنس", '
    '"category": "makeup", "brand": "اسنس", "price_toman": 550000, '
    '"old_price_toman": null, "colors": [], "shades": [], "sizes": [], '
    '"volume_ml": null, "spf": null, "stock_state": "in_stock", "confidence": 0.9}\n'
    "ورودی: «کرم ضد آفتاب سان‌سل SPF50 حجم ۵۰ میل | قیمت ۴/۸۰»\n"
    'خروجی: {"extraction_successful": true, "title_fa": "کرم ضد آفتاب سان‌سل", '
    '"category": "skincare", "brand": "سان‌سل", "price_toman": 4800000, '
    '"old_price_toman": null, "colors": [], "shades": [], "sizes": ["50"], '
    '"volume_ml": 50, "spf": 50, "stock_state": "in_stock", "confidence": 0.9}\n'
    "ورودی: «رژلب مات شماره ۱۲ | قیمت توافقی»\n"
    'خروجی: {"extraction_successful": true, "title_fa": "رژلب مات", '
    '"product_name": "رژلب", '
    '"category": "makeup", "brand": null, "price_toman": null, '
    '"old_price_toman": null, "colors": [], "shades": ["12"], "sizes": [], '
    '"volume_ml": null, "spf": null, "stock_state": "on_request", "confidence": 0.85}\n'
    "ورودی: «سه محصول: ۱.دور چشم نامبوزین ۲.اسنس نامبوزین ۳.ابرسان فارم استی»\n"
    'خروجی: {"extraction_successful": true, "title_fa": "سه محصول نجات‌بخش", '
    '"products": [{"name_fa": "دور چشم", "category": "skincare", "brand": "نامبوزین"}, '
    '{"name_fa": "اسنس", "category": "skincare", "brand": "نامبوزین"}, '
    '{"name_fa": "ابرسان", "category": "skincare", "brand": "فارم استی"}], '
    '"price_toman": null, "stock_state": "in_stock", "confidence": 0.85}\n\n'
    "## قراردادِ خروجی (Output Contract)\n"
    "- فقط و فقط یک شیءِ JSON مطابق اسکیمای داده‌شده؛ بدون هیچ متن، توضیح یا حصارِ کد.\n"
    "- قیمت‌ها = عددِ صحیحِ تومانِ کامل (مثلا 4800000)؛ لیست‌ها خالی ولی هرگز null.\n"
    "- اگر اطلاعات کافی نیست extraction_successful=false با دلیلِ کوتاه؛ «حدسِ مطمئن‌نشده» ممنوع.\n"
    "- پیش از پاسخ، خروجی‌ات را با «دام‌ها» بالا دوباره بسنج (Self-check) و سپس JSON نهایی را بده."
)

VLM_RETRY_PROMPT = (
    "خروجی قبلی نامعتبر بود.\n"
    "دلیل: {reason}\n"
    "اصلاح کن: فقط یک JSON سالم مطابق اسکیما بده — بدون متن اضافه، بدون حصار کد، "
    "و فقط مقادیری که از متن مطمئنی (حدس نزن)."
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
    "ژل شستشو": "skincare",  # در نیچ آرایشی‌بهداشتی = شوینده‌ی صورت
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
    "میکاپ": "makeup",
    "لیپ گلاس": "makeup",
    "لیپگلاس": "makeup",
    "bb کرم": "makeup",  # کرم‌های پوشش‌دهنده = آرایشی (نه skincare)
    "cc کرم": "makeup",
    # perfume
    "عطر": "perfume",
    "ادکلن": "perfume",
    "ادوپرفیوم": "perfume",
    # haircare
    "شامپو": "haircare",
    "نرم کننده": "haircare",
    "ماسک مو": "haircare",
    "روغن مو": "haircare",
    "سرم مو": "haircare",
    "پوست سر": "haircare",
    "حالت دهنده": "haircare",
    # hygiene (بهداشتیِ واقعی — طبق تأیید انسانیِ gold-033)
    "ضدعفونی": "hygiene",
    "پد بهداشتی": "hygiene",
    "دستمال": "hygiene",
    "بادی اسپلش": "deo_fragrance",  # تصحیحِ بازبینی: خطایِ تأییدِ انسانیِ دور ۱ (قانون ۵ + نکته‌ی خطای انسانی)
    # deo_fragrance (خوشبوکننده/ضدتعریق — «دسته‌بندیِ دقیق‌تر» به انتخاب کاربر)
    "خوشبو کننده": "deo_fragrance",
    "خوشبوکننده": "deo_fragrance",
    "ضد تعریق": "deo_fragrance",
    "ضدتعریق": "deo_fragrance",
    "دئودورانت": "deo_fragrance",
    "لوسیون بدن": "deo_fragrance",
    "اسپری بدن": "deo_fragrance",
    "اسپری": "deo_fragrance",  # عطرِ واقعی = perfume؛ اسپری بدن/ضدتعریق = deo
    "مام قارچی": "deo_fragrance",
    "مام": "deo_fragrance",  # با مرزِ واژه — «مامان» برنمی‌دارد
    "داو": "deo_fragrance",  # در این نیچ = ضدتعریق/خوشبوکننده
    # makeup — برند/محصول مشخص
    "شیگلم": "makeup",
}

# خانواده‌های عمومیِ دسته — با «اکثریتِ رویارویی» داوری می‌شوند، نه بلندترین واژه
CATEGORY_FAMILY = {
    "پوست": "skincare",
    "پوستی": "skincare",
    "اسکین کر": "skincare",
    "اسکینکر": "skincare",
    "مراقبتی": "skincare",
    "میکاپ": "makeup",
    "آرایش": "makeup",
    "آرایشی": "makeup",
    "بهداشتی": "hygiene",
}


class ExtractionPipeline:
    def __init__(self, session: AsyncSession, ai: AiProvider | None = None):
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
            vlm = await self._vlm_extract_safe(caption_text)

        price_toman, method, confidence = _merge_price(regex_price, vlm, signals.prices.on_request)

        title = (vlm.title_fa if vlm and vlm.title_fa else None) or _derive_title(caption_text)
        category = (vlm.category if vlm and vlm.category else None) or _guess_category(
            caption_text + " " + " ".join(signals.hashtags)
        )
        brand = vlm.brand if vlm else None
        colors = (vlm.colors if vlm and vlm.colors else None) or signals.colors
        shades = (vlm.shades if vlm and vlm.shades else None) or signals.shades
        sizes = (vlm.sizes if vlm and vlm.sizes else None) or signals.sizes
        stock_state = (
            "on_request"
            if signals.prices.on_request
            else ("in_stock" if price_toman else (vlm.stock_state if vlm else "unknown"))
        )

        # ── فهرستِ محصولات: چندمحصوله‌ی VLM (همه‌ی محصولات) یا تک‌محصوله‌ی ادغام‌شده ──
        single_name = find_product_name(caption_text)
        if vlm and vlm.products:
            specs: list[dict[str, Any]] = [
                {
                    "name": p.name_fa,
                    "title": p.name_fa or _derive_title(caption_text),
                    "category": p.category,
                    "brand": p.brand,
                    "colors": p.colors or [],
                    "shades": p.shades or [],
                    "sizes": p.sizes or [],
                    "volume_ml": p.volume_ml,
                    "spf": p.spf,
                    "price": p.price_toman,
                    "old_price": p.old_price_toman,
                    "old_conf": 0.8,
                    "stock": p.stock_state,
                    "method": "vlm",
                    "confidence": max(vlm.confidence, 0.5),
                }
                for p in vlm.products
            ]
        else:
            specs = [
                {
                    "name": single_name,
                    "title": title,
                    "category": category,
                    "brand": brand,
                    "colors": colors,
                    "shades": shades,
                    "sizes": sizes,
                    "volume_ml": vlm.volume_ml if vlm else None,
                    "spf": vlm.spf if vlm else None,
                    "price": price_toman,
                    "old_price": old_price.value_toman if old_price else None,
                    "old_conf": old_price.confidence if old_price else 0.8,
                    "stock": stock_state,
                    "method": method,
                    "confidence": confidence,
                }
            ]

        stats = {
            "products": 0,
            "offers": 0,
            "vlm_calls": vlm_calls,
            "stored": 0,
            "human_review": 0,
            "rejected": 0,
        }
        for spec in specs:
            route = route_by_confidence(spec["confidence"], self.settings.extract_review_floor)
            status = {"store": "confirmed", "review": "candidate", "reject": "rejected"}[route]
            stats[{"store": "stored", "review": "human_review", "reject": "rejected"}[route]] += 1

            product = Product(
                page_id=page_id,
                title_fa=spec["title"],
                category=spec["category"],
                attributes={
                    "product_name": spec["name"],
                    "brand": spec["brand"],
                    "colors": spec["colors"],
                    "shades": spec["shades"],
                    "sizes": spec["sizes"],
                    "volume_ml": spec["volume_ml"],
                    "spf": spec["spf"],
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
                price_minor=spec["price"],
                stock_state=spec["stock"],
                valid_from=utcnow(),
                ttl_hours=24,
                stale=False,
            )
            self.session.add(offer)
            await self.session.flush()
            stats["offers"] += 1

            source_type = "vlm" if spec["method"] == "vlm" else "caption"
            self.session.add(
                Evidence(
                    offer_id=offer.id,
                    product_id=product.id,
                    source_type=source_type,
                    source_id=item.ig_id,
                    method=spec["method"],
                    confidence=spec["confidence"],
                )
            )
            if spec["old_price"]:
                self.session.add(
                    Evidence(
                        offer_id=offer.id,
                        product_id=product.id,
                        source_type="caption",
                        source_id=item.ig_id,
                        method="price_parser:old_price",
                        confidence=spec["old_conf"],
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
            "old_price_toman": (signals.prices.old.value_toman if signals.prices.old else None),
            "category": _guess_category(caption_text + " " + " ".join(signals.hashtags))
            or find_product_category(caption_text),
            "product_name": find_product_name(caption_text) or _derive_title(caption_text),
            "brand": find_brand(caption_text),
            "colors": signals.colors,
            "shades": signals.shades,
            "sizes": signals.sizes,
            "stock_state": (
                "on_request" if signals.prices.on_request else ("in_stock" if price else "unknown")
            ),
            "confidence": conf,
            "method": method,
            "route": route_by_confidence(conf, get_settings().extract_review_floor),
        }

    @staticmethod
    def extract_multi_fa(caption_text: str) -> list[dict[str, Any]]:
        """استخراجِ چندمحصوله — «همه‌ی محصولاتِ یک پست، نه فقط یکی» (نکته‌ی ۱).

        جداکننده‌های صریح (خط/گلوله/شماره/ایموجی) ← تکه‌بندیِ واژه‌ی محصول؛
        تکه‌هایِ بی‌نام/بی‌برند/بی‌قیمت (متنِ توضیحی) دور ریخته می‌شوند.
        """
        spans = split_product_spans(caption_text or "")
        kept = [
            s for s in spans if find_product_name(s) or find_brand(s) or re.search(r"\d{3,}", s)
        ]
        pool = kept or spans
        return [
            {**ExtractionPipeline.extract_signals_fa(span), "span": span[:200]} for span in pool
        ]

    @staticmethod
    def is_multi_caption(caption_text: str) -> bool:
        """آیا کپشن «صراحتا» چند محصولی است؟ («چند تا محصول»، «سه محصول» …)"""
        return bool(
            re.search(
                r"چند\s*تا\s*محصول|چند\s*محصول|سه\s*محصول|دو\s*محصول|چندتا\s*محصول",
                (caption_text or "").replace("\u200c", " "),
            )
        )

    # ── استخراج ساخت‌یافته‌ی VLM با retry هوشمند ───────────────────────────────
    async def _vlm_extract_safe(self, caption_text: str) -> SafeProductExtraction | None:
        """مغزِ هوشمند (Qwen محلی/BoxAPI) — با مسیرِ فرار: هر شکست → None ← regex ادامه می‌دهد."""
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
            try:
                text = await self.ai.chat(messages, temperature=0)
            except Exception:
                return None
            try:
                result = SafeProductExtraction.model_validate(_defensive_parse(text))
            except (ValidationError, ValueError) as e:
                messages.append(
                    {
                        "role": "user",
                        "content": VLM_RETRY_PROMPT.format(reason=str(e)[:500]),
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
        # «توافقی/دایرکت» بر حدسِ بدون واحد مقدم است («حجم ۴۰۰» هرگز قیمت نیست)
        if on_request and regex_price.confidence < 0.9:
            return None, "on_request", 0.9
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


def _fold_fa(text: str) -> str:
    """نرمال‌سازی املایی فارسی برای تطبیق: آ/أ/إ→ا، ي/ى/ئ→ی، ك→ک، ة→ه + lowercase لاتین.

    «_» هشتگ‌ها به فاصله تبدیل می‌شود («بادی_اسپلش» = «بادی اسپلش»).
    """
    return text.replace("_", " ").translate(_FOLD_TABLE).lower()


_FOLD_TABLE = str.maketrans(
    {"آ": "ا", "أ": "ا", "إ": "ا", "ي": "ی", "ى": "ی", "ئ": "ی", "ك": "ک", "ة": "ه"}
)


def _key_hits(folded: str, key: str) -> int:
    """شمارشِ کلید در متن تاشده؛ برای کلیدهای کوتاه (≤۳ حرف) مرزِ واژه الزامی است.

    «مداومه» حاوی «داو» نیست؛ «علاقه» حاوی «لاک» نیست؛ «کرمهای» حاوی «کرم» هست.
    """
    if len(key) > 3:
        return folded.count(key)
    n = 0
    for m in re.finditer(re.escape(key), folded):
        before = folded[m.start() - 1] if m.start() > 0 else " "
        after = folded[m.end() :] if m.end() < len(folded) else ""
        if _LETTER_RE.match(before):
            continue
        if (
            after
            and _LETTER_RE.match(after)
            and not (after[0] == "ه" and (len(after) == 1 or after[1] in "ای "))
            and not (after[0] == "ی" and (len(after) == 1 or not _LETTER_RE.match(after[1])))
        ):
            continue
        n += 1
    return n


_LETTER_RE = re.compile(r"[A-Za-z\u0600-\u06FF]")


def _guess_category(text: str) -> str | None:
    """دسته‌یابی دو لایه: محصول/برند مشخص (بلندترین) ← خانواده‌ی غالب (اکثریت).

    «کرم پودر»/«پوست سر»/«bb کرم» بر «کرم»/«پوست» مقدم‌اند؛ «ابرسان» و «آبرسان»
    یکی‌اند؛ کپشنِ «آرایشی و پوستی»ِ هم‌سطح = بدون دسته‌ی غالب (None).
    """
    folded = _fold_fa(text.replace("\u200c", " "))
    for fa, en in sorted(CATEGORY_MAP.items(), key=lambda kv: -len(kv[0])):
        if _key_hits(folded, _fold_fa(fa)):
            return en
    counts: dict[str, int] = {}
    for fa, en in CATEGORY_FAMILY.items():
        n = folded.count(_fold_fa(fa))
        if n:
            counts[en] = counts.get(en, 0) + n
    if not counts:
        return None
    ranked = sorted(counts.items(), key=lambda kv: -kv[1])
    if len(ranked) >= 2 and ranked[0][1] == ranked[1][1]:
        return None  # خانواده‌های هم‌سطح — «آرایشی و پوستی» — بدون غالب
    return ranked[0][0]
