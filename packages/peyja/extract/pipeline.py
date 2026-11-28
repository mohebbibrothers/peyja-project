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
    canonical_brand,
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
from peyja.provider.llm import AiProvider, parse_json_loose

PIPELINE_VERSION = "spine-v0.2"

VLM_SYSTEM_PROMPT = (
    "## نقش (Role)\n"
    "تو «استخراج‌گرِ داده‌ی متخصصِ بازارِ لوازم آرایشی‌بهداشتی ایران» هستی؛ "
    "سال‌ها کپشنِ فروشندگانِ اینستاگرام را خوانده‌ای و با قیمت‌گذاریِ تومانی، شیدها، "
    "هشتگ‌های فروشگاهی، واژگانِ بومی («توافقی»، «دایرکت»، «۵۵۰💸») و دام‌های متنی آشنایی کامل داری.\n\n"
    "## پروتکلِ استخراج (گام‌به‌گام — دقیقاً به همین ترتیب اجرا کن)\n"
    "۱. **اصلِ دسته‌یابی در اینستاگرام:** در ریلز و پست‌های فروشگاه‌های آرایشی، گاهی فقط یک جمله‌ی کوتاه، "
    "قرعه‌کشی، مقایسه‌ی اصل/فیک، یا هشتگ آمده و قیمت/مدل در ویدیوست. با وجود این:\n"
    "   - تا وقتی پست به محصول، برند، ویژگی یا دسته‌ی غالبی از محصولات آرایشی/پوستی/مراقبتی مربوط است "
    "(در متن یا هشتگ‌ها مثل `#محصولات_پوستی`، `#میکاپ`، `#سایه_چشم`، `#اسپری_داو`، `#پک_آرایشی`)، "
    "**حتماً** `extraction_successful=true` و `category`ِ غالب را استخراج کن.\n"
    "   - **فقط و فقط** وقتی `category: null` (یا `extraction_successful=false`) بگذار که پست:\n"
    "     (الف) صرفاً طنز/چالشِ خالصِ بی‌ربط به محصول باشد («پیازای خیانت‌کار...»، «😌😂 #فان #چالش»)، یا\n"
    "     (ب) صرفاً درخواست لایک/کامنت حمایتی باشد («فقط یه ❤️ بزار واسم تا دیده بشم #حمایت»)، یا\n"
    "     (ج) معرفیِ کلیِ کل پیجِ مختلط بدون هیچ دسته‌ی غالب یا با تساوی دسته‌ها باشد («این فقط بخشی از محصولات پیجمه، آرایشی و پوستی اصل...»).\n"
    "۲. **قیمت فعلی:** فقط عددی که واقعاً قیمت است. «حجم/وزن/ساعت/تعداد/SPF/شماره» هرگز قیمت نیستند.\n"
    "۳. **قیمت قبل از تخفیف (old):** فقط با نشانه‌ی صریحِ «از X به Y»، «قیمت قبل» یا خط‌خوردگی.\n"
    "۴. **دسته:** دقیقاً یکی از ۷ دسته‌ی مجاز:\n"
    "   - `skincare`: مراقبت پوست (ضدآفتاب، سرم، آبرسان، تونر، دورچشم، فیس‌واش، اسکراب، روتین پوستی، ضدلک/ضدجوش)\n"
    "   - `makeup`: آرایشی (ریمل، رژلب، بالم لب/لیپ بالم، لیپ‌گلاس، کرم‌پودر، BB/CC کرم، سایه، رژگونه، کانسیلر، کانتور، پک/باکس آرایشی)\n"
    "   - `deo_fragrance`: دئو و خوشبوکننده‌ی بدن (بادی اسپلش، اسپری بدن/داو، مام، دئودورانت، ضدتعریق، لوسیون بدن)\n"
    "   - `perfume`: عطر و ادکلن (عطر، ادکلن، ادوپرفیوم)\n"
    "   - `haircare`: مراقبت مو (شامپو سر، نرم‌کننده، ماسک/روغن/سرم مو، پوست سر)\n"
    "   - `hygiene`: بهداشتیِ غیردئو (شامپو بدن، ضدعفونی، دستمال، پد بهداشتی)\n"
    "   - `other`: فقط محصول مشخصی بیرون از ۶ دسته‌ی بالا.\n"
    "۵. **نامِ محصول، برند و مشخصات:**\n"
    '   - `brand` را جداگانه پر کن و با نامِ محصول نچسبان («اسپری داو» ⇒ `product_name="اسپری بدن"`, `brand="داو"`).\n'
    "   - عبارتِ کامل و کاتالوگیِ محصول را حفظ کن («رژلب مات»، «مام قارچی»، «شامپو بدون سولفات»، «سرم ضد ریزش»).\n"
    '   - از هشتگ‌ها هم برند، نامِ محصول و رنگ را استخراج کن (`#نامبوزین`, `#اسپری_داو`, `#دورچشم`, `#اسنسصورتی` ⇒ `colors=["صورتی"]`).\n'
    "   - رنگ/تناژ (`colors` مثل «صورتی»، «نود» حتی در «میکاپ صورتی»)، شماره‌ی شید (`shades` مثل «شماره ۱۲» — نه رنگ نه سایز)، "
    'سایز/حجم (`sizes` و اگر `volume_ml` عددی داشت مثل ۵۰ میل، `"50"` را در `sizes` هم بیاور)، `spf`.\n'
    "۶. **وضعیت موجودی:** `in_stock` (موجود/قیمت‌دار/«همه محصولات موجود») | `out_of_stock` (ناموجود/تمام) | "
    "`on_request` (استعلام قیمت در دایرکت/کامنت: «قیمت دایرکت»، «کلمه X کامنت کن») | `unknown`.\n"
    "۷. **چندمحصوله:** اگر پست چند محصول دارد، «همه» را در آرایه‌ی `products` بیاور "
    "(هرکدام با `name_fa`, `category`, `brand` و مشخصاتِ خودش) و در سطحِ بالا هم دسته‌ی غالب (`category`)، "
    "`brand` و `product_name`ِ نخستین/غالب‌ترین محصول را پر کن؛ تک‌محصوله = `products` خالی.\n\n"
    "## قواعدِ قیمتِ ایرانی (دانشِ بحرانی)\n"
    "- «X/YY» = X میلیون و Y صدمِ میلیون ⇒ «۴/۸۰» = 4800000\n"
    "- عددِ ۳رقمیِ بدونِ واحد = هزارتایی ⇒ «۵۵۰» یا «۵۵۰💸» = 550000\n"
    "- «از ۹۰۰ به ۷۵۰» ⇒ old=900000 و price=750000\n"
    "- «توافقی/دایرکت برای قیمت/کلمهٔ X کامنت کن» ⇒ price=null و stock_state=on_request\n\n"
    "## دام‌ها (این‌ها را هرگز اشتباه نگیر)\n"
    '- «حجم 250mil» ⇒ حجم است (volume_ml=250, sizes=["250"])، قیمت نیست.\n'
    "- «۲۴ ساعت ماندگاری» ⇒ عددِ زمانی است، قیمت نیست و شید هم نیست.\n"
    "- «شماره ۱۲» یا «لیپ گلاس ۴۴ کیکو» ⇒ شید است (shades)، نه قیمت و نه سایز.\n"
    "- «پالت ۱۲ رنگ» ⇒ تعدادِ رنگ است، نه شید و نه قیمت.\n"
    "- «قیمت مناسب/خوب» بدونِ عدد ⇒ قیمت وجود ندارد (حدس نزن!).\n"
    "- «بادی اسپلش» و «اسپری بدن/داو» = `deo_fragrance`؛ «بالم لب/لیپ بالم» = `makeup`.\n\n"
    "## نمونه‌ها (Few-shot)\n"
    "ورودی: «ریمل اسنس موجود شد ۵۵۰💸 #اسنسصورتی»\n"
    'خروجی: {"extraction_successful": true, "title_fa": "ریمل اسنس", "product_name": "ریمل", '
    '"category": "makeup", "brand": "اسنس", "price_toman": 550000, '
    '"old_price_toman": null, "colors": ["صورتی"], "shades": [], "sizes": [], '
    '"volume_ml": null, "spf": null, "stock_state": "in_stock", "products": [], "confidence": 0.9}\n'
    "ورودی: «کرم ضد آفتاب سان‌سل SPF50 حجم ۵۰ میل | قیمت ۴/۸۰»\n"
    'خروجی: {"extraction_successful": true, "title_fa": "کرم ضد آفتاب سان‌سل", "product_name": "کرم ضد آفتاب", '
    '"category": "skincare", "brand": "سان‌سل", "price_toman": 4800000, '
    '"old_price_toman": null, "colors": [], "shades": [], "sizes": ["50"], '
    '"volume_ml": 50, "spf": 50, "stock_state": "in_stock", "products": [], "confidence": 0.9}\n'
    "ورودی: «رژلب مات شماره ۱۲ | قیمت توافقی»\n"
    'خروجی: {"extraction_successful": true, "title_fa": "رژلب مات", '
    '"product_name": "رژلب مات", '
    '"category": "makeup", "brand": null, "price_toman": null, '
    '"old_price_toman": null, "colors": [], "shades": ["12"], "sizes": [], '
    '"volume_ml": null, "spf": null, "stock_state": "on_request", "products": [], "confidence": 0.85}\n'
    "ورودی: «بادی اسپلش بث اند بادی | سفارش از دایرکت #اسپری_داو»\n"
    'خروجی: {"extraction_successful": true, "title_fa": "بادی اسپلش بث اند بادی", "product_name": "بادی اسپلش", '
    '"category": "deo_fragrance", "brand": "بث اند بادی", "price_toman": null, '
    '"old_price_toman": null, "colors": [], "shades": [], "sizes": [], '
    '"volume_ml": null, "spf": null, "stock_state": "unknown", "products": [], "confidence": 0.85}\n'
    "ورودی: «روتین پوستیت چیه؟! محصولات موجود✅ #محصولات_پوستی #مراقبت_پوست»\n"
    'خروجی: {"extraction_successful": true, "title_fa": "روتین پوستی", "product_name": null, '
    '"category": "skincare", "brand": null, "price_toman": null, '
    '"old_price_toman": null, "colors": [], "shades": [], "sizes": [], '
    '"volume_ml": null, "spf": null, "stock_state": "in_stock", "products": [], "confidence": 0.75}\n'
    "ورودی: «سه محصول: ۱.دور چشم نامبوزین ۲.اسنس نامبوزین ۳.ابرسان فارم استی | برای قیمت کلمه پوست کامنت کن»\n"
    'خروجی: {"extraction_successful": true, "title_fa": "سه محصول نجات‌بخش", "product_name": "دور چشم", '
    '"category": "skincare", "brand": "نامبوزین", '
    '"products": [{"name_fa": "دور چشم", "category": "skincare", "brand": "نامبوزین"}, '
    '{"name_fa": "اسنس", "category": "skincare", "brand": "نامبوزین"}, '
    '{"name_fa": "ابرسان", "category": "skincare", "brand": "فارم استی"}], '
    '"price_toman": null, "stock_state": "on_request", "confidence": 0.85}\n'
    "ورودی: «پیازای خیانت‌کار همیشه باید بد بو بمونن 😂🧅 #ترند #آرایش»\n"
    'خروجی: {"extraction_successful": false, "failure_reason": "پست طنز/چالش بدون محصول یا دسته‌ی فروش", '
    '"category": null, "product_name": null, "brand": null, "price_toman": null, '
    '"colors": [], "shades": [], "sizes": [], "products": [], "confidence": 0.0}\n\n'
    "## قراردادِ خروجی (Output Contract)\n"
    "- فقط و فقط یک شیءِ JSON مطابق اسکیمای داده‌شده؛ بدون هیچ متن، حاشیه، `<think>` یا حصارِ کد.\n"
    "- قیمت‌ها = عددِ صحیحِ تومانِ کامل (مثلا 4800000)؛ لیست‌ها خالی ولی هرگز null.\n"
    "- «حدسِ قیمت» ممنوع؛ اگر فقط قیمت نامشخص است ولی دسته یا محصول هست، `price_toman=null` و `extraction_successful=true`.\n"
    "- پیش از پاسخ، خروجی‌ات را بی‌صدا با «دام‌ها» بالا بسنج (Self-check) و سپس فقط JSON نهایی را بده."
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
        category = (
            (vlm.category if vlm and vlm.category else None)
            or _guess_category(caption_text + " " + " ".join(signals.hashtags))
            or find_product_category(caption_text)
        )
        brand = canonical_brand(vlm.brand if vlm else None) or find_brand(caption_text)
        colors = list(dict.fromkeys([*(vlm.colors if vlm else []), *signals.colors]))
        shades = list(dict.fromkeys([*(vlm.shades if vlm else []), *signals.shades]))
        sizes = list(dict.fromkeys([*(vlm.sizes if vlm else []), *signals.sizes]))
        if vlm and vlm.volume_ml is not None:
            v_str = (
                str(int(vlm.volume_ml)) if float(vlm.volume_ml).is_integer() else str(vlm.volume_ml)
            )
            if v_str not in sizes:
                sizes.append(v_str)
        stock_state = (
            "on_request"
            if signals.prices.on_request
            else ("in_stock" if price_toman else (vlm.stock_state if vlm else "unknown"))
        )

        # ── فهرستِ محصولات: چندمحصوله‌ی VLM ← یا چندمحصوله‌ی آفلاین ← یا تک‌محصوله ──
        single_name = (vlm.product_name if vlm and vlm.product_name else None) or find_product_name(
            caption_text
        )
        multi_offline = ExtractionPipeline.extract_multi_fa(caption_text)
        if vlm and vlm.products:
            specs: list[dict[str, Any]] = [
                {
                    "name": p.name_fa,
                    "title": p.name_fa or _derive_title(caption_text),
                    "category": p.category or category,
                    "brand": p.brand or brand,
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
        elif len(multi_offline) > 1:
            specs = [
                {
                    "name": m["product_name"],
                    "title": m["product_name"] or _derive_title(m["span"]),
                    "category": m["category"] or category,
                    "brand": m["brand"] or brand,
                    "colors": m["colors"],
                    "shades": m["shades"],
                    "sizes": m["sizes"],
                    "volume_ml": None,
                    "spf": None,
                    "price": m["price_toman"],
                    "old_price": m["old_price_toman"],
                    "old_conf": 0.8,
                    "stock": m["stock_state"] if m["stock_state"] != "unknown" else stock_state,
                    "method": m["method"],
                    "confidence": max(float(m["confidence"]), confidence * 0.5),
                }
                for m in multi_offline
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
            "processed": 1,
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
            f"اسکیمای JSON:\n{json.dumps(schema, ensure_ascii=False, separators=(',', ':'))}\n\n"
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
    """parse مقاوم: حذف `<think>`، حصارهای کد، متن اضافه، JSON شکسته."""
    return parse_json_loose(text)


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
