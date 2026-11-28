"""Pipeline استخراج — از raw_items به محصول/واریانت/قیمت + Evidence.

معماری قله‌ی تکنولوژی (۲۰۲۶):
- مسیر سریع regex (رایگان، قطعی) ← مسیر VLM فقط برای نمونه‌های کم‌اعتماد
- خروجی ساخت‌یافته با SafeProductExtraction (مسیر فرار صریح + اعتبارسنجی Pydantic)
- retry با بازخورد خطای اعتبارسنجی، حداکثر ۳ بار، temperature=0
- مسیریابی با بازه‌های اطمینان: ≥0.7 ذخیره | 0.4–0.7 صف انسانی | <0.4 رد
- «استخراج‌شده ≠ تأییدشده» — هر ادعا Evidence دارد
"""

from __future__ import annotations

import base64
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from peyja.config import get_settings
from peyja.extract.caption import analyze_caption
from peyja.extract.catalog import (
    canonical_brand,
    find_brand,
    find_latin_series,
    find_product_category,
    find_product_name,
    split_product_spans,
)
from peyja.extract.price_parser import PriceCandidate
from peyja.extract.schema import SafeProductExtraction, route_by_confidence
from peyja.models import (
    CaptionVersion,
    Evidence,
    MediaFile,
    Offer,
    Product,
    RawItem,
    Variant,
    utcnow,
)
from peyja.provider.llm import AiProvider, parse_json_loose

PIPELINE_VERSION = "spine-v0.4"

VLM_SYSTEM_PROMPT = (
    "## نقش (Role)\n"
    "تو «استخراج‌گرِ داده‌ی متخصصِ بازارِ لوازم آرایشی‌بهداشتی ایران» هستی؛ "
    "سال‌ها کپشنِ فروشندگانِ اینستاگرام را خوانده‌ای و با قیمت‌گذاریِ تومانی، شیدها، "
    "هشتگ‌های فروشگاهی، واژگانِ بومی («توافقی»، «دایرکت»، «۵۵۰💸») و دام‌های متنی آشنایی کامل داری.\n\n"
    "## پروتکلِ استخراج (گام‌به‌گام — دقیقاً به همین ترتیب اجرا کن)\n"
    "۱. **اصلِ دسته‌یابی در فروشگاه‌های اینستاگرام ایران:**\n"
    "   بیشتر پست‌های یک فروشگاه آرایشی‌بهداشتی — از جمله ریلزهای شوخ («مامانمم خریدههه😂»، «POV»، «بفرست براش»)، "
    "پست‌های مراقبت/روتین پوستی («پوست زیباتر»، `#روتین_پوستی`, `#ضد_جوش`, `#مراقبت_پوست`)، "
    "مسابقه/قرعه‌کشی محصول («قرعه‌کشی سایه و رژگونه»)، نظرسنجی برند («سنتلا/فارماسی/اسنس»)، "
    "پست‌های «همه محصولات موجود» یا «پک آرایشی» — **همگی دسته‌ی مشخص فروشگاهی دارند (`extraction_successful=true`)**؛ "
    "حتی اگر قیمت یا نامِ دقیقِ محصول در کپشن نیامده باشد (چون محصول در تصویر/ویدیوست).\n"
    "   - وقتی هشتگ‌های چند دسته کنار هم آمده‌اند، بر اساس **اکثریت شمارش** دسته‌ی غالب را انتخاب کن "
    "(مثلاً ۲ هشتگ پوستی در برابر ۱ آرایشی ← `skincare`، نه صرفاً اولین هشتگ!).\n"
    "   - **فقط و فقط** در این ۲ حالت `category: null` و `extraction_successful=false` بگذار:\n"
    "     (الف) طنز یا متن غیرمرتبط با آرایشی/پوستی بدون محصول و بدون دسته‌ی فروش («پیازای خیانت‌کار 😂🧅»، «😌😂 #فان #چالش») "
    "یا صرفاً درخواست لایک/کامنتِ پیج («فقط یه ❤️ بزار واسم #حمایت»).\n"
    "     (ب) معرفیِ کلیِ کل پیج با **تساویِ دو دسته‌ی «آرایشی» و «پوستی»** در متن ("
    "«این فقط بخشی از محصولات پیجمه، محصولات آرایشی و پوستی اصل...» بدون نام محصول خاص و بدون اکثریت یک دسته).\n"
    "۲. **قیمت فعلی:** فقط عددی که واقعاً قیمت است. «حجم/وزن/ساعت/تعداد/SPF/شماره» هرگز قیمت نیستند.\n"
    "۳. **قیمت قبل از تخفیف (old):** فقط با نشانه‌ی صریحِ «از X به Y»، «قیمت قبل» یا خط‌خوردگی.\n"
    "۴. **دسته:** دقیقاً یکی از ۷ دسته‌ی مجاز:\n"
    "   - `skincare`: مراقبت پوست (ضدآفتاب، سرم، آبرسان، تونر، دورچشم، فیس‌واش، اسکراب، روتین پوستی، اسکین‌کر، ضدلک/ضدجوش)\n"
    "   - `makeup`: آرایشی (ریمل، رژلب، بالم لب/لیپ بالم، لیپ‌گلاس، کرم‌پودر، BB/CC کرم، سایه، رژگونه، کانسیلر، کانتور، پک/باکس آرایشی)\n"
    "   - `deo_fragrance`: دئو و خوشبوکننده‌ی بدن (بادی اسپلش، اسپری بدن/اسپریای داو، مام، دئودورانت، ضدتعریق، لوسیون بدن)\n"
    "   - `perfume`: عطر و ادکلن (عطر، ادکلن، ادوپرفیوم)\n"
    "   - `haircare`: مراقبت مو (شامپو سر، نرم‌کننده، ماسک/روغن/سرم مو، پوست سر)\n"
    "   - `hygiene`: بهداشتیِ غیردئو (شامپو بدن، ضدعفونی، دستمال، پد بهداشتی)\n"
    "   - `other`: فقط محصول مشخصی بیرون از ۶ دسته‌ی بالا.\n"
    "۵. **نامِ محصول، برند و مشخصات:**\n"
    '   - `brand` را همیشه جداگانه پر کن و به نامِ محصول نچسبان — حتی اگر جمع فارسی باشد («اسپریای داو»/«اسپری داو» ⇒ `product_name="اسپری بدن"`, `brand="داو"`) یا فقط در هشتگ بیاید (`#اسپری_داو`, `#نامبوزین`).\n'
    "   - عبارتِ کامل و کاتالوگیِ محصول را حفظ کن («رژلب مات»، «مام قارچی»، «شامپو بدون سولفات»، «سرم ضد ریزش»).\n"
    "   - **حفظ نام مدل/سری لاتین:** اگر نامِ مدل/سریِ لاتین کنار برند در متن است (مثل `Rose Done` در «با Rose Done فارماسی...») "
    "حتماً آن نام لاتین را در `product_name` بیاور (مثل `Rose Done` یا `پالت سایه Rose Done`) و هرگز حذفش نکن!\n"
    "   - اگر نامِ محصول فقط در هشتگ آمده (`#دورچشم`, `#ابرسان`) همان نامِ کاتالوگی را در `product_name` بگذار و جمله‌ی توضیحی مثل «مقایسه اصل و فیک» را به‌عنوان `product_name` ننویس.\n"
    '   - از هشتگ‌ها هم برند، نامِ محصول و رنگ را استخراج کن (`#اسنسصورتی` ⇒ `colors=["صورتی"]`).\n'
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
    "ورودی: «با Rose Done فارماسی یک میکاپ صورتی بساز ✨ #آرایش #فارماسی»\n"
    'خروجی: {"extraction_successful": true, "title_fa": "Rose Done فارماسی", "product_name": "محصول Rose Done", '
    '"category": "makeup", "brand": "فارماسی", "price_toman": null, '
    '"old_price_toman": null, "colors": ["صورتی"], "shades": [], "sizes": [], '
    '"volume_ml": null, "spf": null, "stock_state": "unknown", "products": [], "confidence": 0.85}\n'
    "ورودی: «مامانمم خریدههه😂 همه محصولات موجود ✅ #لوازم_آرایشی #آرایش»\n"
    'خروجی: {"extraction_successful": true, "title_fa": "محصولات آرایشی موجود", "product_name": null, '
    '"category": "makeup", "brand": null, "price_toman": null, '
    '"old_price_toman": null, "colors": [], "shades": [], "sizes": [], '
    '"volume_ml": null, "spf": null, "stock_state": "in_stock", "products": [], "confidence": 0.75}\n'
    "ورودی: «برای داشتن یک روتین پوستی خوب مراقبت مداوم لازمه ✨ مشاوره دایرکت #روتین_پوستی #مراقبت_پوست #ضد_جوش»\n"
    'خروجی: {"extraction_successful": true, "title_fa": "محصولات روتین پوستی", "product_name": null, '
    '"category": "skincare", "brand": null, "price_toman": null, '
    '"old_price_toman": null, "colors": [], "shades": [], "sizes": [], '
    '"volume_ml": null, "spf": null, "stock_state": "unknown", "products": [], "confidence": 0.75}\n'
    "ورودی: «سه محصول: ۱.دور چشم نامبوزین ۲.اسنس نامبوزین ۳.ابرسان فارم استی | برای قیمت کلمه پوست کامنت کن»\n"
    'خروجی: {"extraction_successful": true, "title_fa": "سه محصول نجات‌بخش", "product_name": "دور چشم", '
    '"category": "skincare", "brand": "نامبوزین", '
    '"products": [{"name_fa": "دور چشم", "category": "skincare", "brand": "نامبوزین"}, '
    '{"name_fa": "اسنس", "category": "skincare", "brand": "نامبوزین"}, '
    '{"name_fa": "ابرسان", "category": "skincare", "brand": "فارم استی"}], '
    '"price_toman": null, "stock_state": "on_request", "confidence": 0.85}\n'
    "ورودی: «این فقط بخشی از محصولات پیجمه👌 دنبال محصولات آرایشی و پوستی اصل هستی سر بزن #آرایشی #بهداشتی»\n"
    'خروجی: {"extraction_successful": false, "failure_reason": "معرفی کلی پیج مختلط با تساوی آرایشی و پوستی", '
    '"category": null, "product_name": null, "brand": null, "price_toman": null, '
    '"colors": [], "shades": [], "sizes": [], "products": [], "confidence": 0.0}\n'
    "ورودی: «پیازای خیانت‌کار همیشه باید بد بو بمونن 😂🧅 #ترند #آرایش»\n"
    'خروجی: {"extraction_successful": false, "failure_reason": "پست طنز بدون محصول یا دسته‌ی فروش", '
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

        # ── ترکیب سیگنال‌ها: regex پرقدرت ← VLM معنایی (+ تصویر در دسترس) ─────
        vlm_raw: SafeProductExtraction | None = None
        vlm: SafeProductExtraction | None = None
        vlm_calls = 0
        need_vlm = self.ai is not None and (
            regex_price is None or regex_price.confidence < self.settings.extract_confidence_tau
        )
        if need_vlm:
            vlm_calls = 1
            image_paths = await self._local_image_paths(item.id)
            vlm_raw = await self._vlm_extract_raw(caption_text, image_paths=image_paths)
            if vlm_raw is not None and vlm_raw.extraction_successful:
                vlm = vlm_raw

        offline_cat = _guess_category(
            caption_text + " " + " ".join(signals.hashtags)
        ) or find_product_category(caption_text)

        # امتناعِ آگاهانه‌ی VLM (قانون هم‌سنگ با hybrid_fuse_dict)
        if (
            vlm_raw is not None
            and not vlm_raw.extraction_successful
            and regex_price is None
            and not should_override_vlm_abstention(caption_text, offline_cat)
        ):
            return {
                "processed": 1,
                "products": 0,
                "offers": 0,
                "vlm_calls": vlm_calls,
                "stored": 0,
                "human_review": 0,
                "rejected": 1,
            }

        price_toman, method, confidence = _merge_price(regex_price, vlm, signals.prices.on_request)

        title = (vlm.title_fa if vlm and vlm.title_fa else None) or _derive_title(caption_text)
        category = choose_hybrid_category(
            caption_text,
            vlm.category if vlm else None,
            offline_cat,
            vlm_has_specific=bool(
                vlm
                and (
                    vlm.products
                    or vlm.brand
                    or find_product_name(vlm.product_name or "") is not None
                )
            ),
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
        single_name = prefer_product_name(
            vlm.product_name if vlm else None, find_product_name(caption_text), caption_text
        )
        multi_offline = ExtractionPipeline.extract_multi_fa(caption_text)
        if vlm and vlm.products:
            specs: list[dict[str, Any]] = [
                {
                    "name": p.name_fa,
                    "title": p.name_fa or _derive_title(caption_text),
                    "category": p.category or category,
                    "brand": canonical_brand(p.brand) or p.brand or brand,
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
            route_conf = float(spec["confidence"])
            if (
                spec["price"] is None
                and route_conf < self.settings.extract_review_floor
                and spec["category"]
                and (spec["brand"] or find_product_name(spec["name"] or ""))
            ):
                route_conf = max(route_conf, 0.55)
            route = route_by_confidence(route_conf, self.settings.extract_review_floor)
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

    # ── استخراج ساخت‌یافته‌ی VLM با retry هوشمند (+ چندوجهی تصویر در دسترس) ───
    async def _local_image_paths(self, raw_item_id: int) -> list[Path]:
        rows = (
            (
                await self.session.execute(
                    select(MediaFile).where(MediaFile.raw_item_id == raw_item_id)
                )
            )
            .scalars()
            .all()
        )
        out: list[Path] = []
        for mf in rows:
            p = Path(mf.local_path)
            if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"} and p.is_file():
                try:
                    if p.stat().st_size <= 4 * 1024 * 1024:
                        out.append(p)
                except OSError:
                    continue
            if len(out) >= 2:
                break
        return out

    async def _vlm_extract_raw(
        self, caption_text: str, image_paths: list[Path] | None = None
    ) -> SafeProductExtraction | None:
        """پاسخ کامل VLM (متن + تصویر محلی در دسترس) — خطای فنی یا شکست پارس → None."""
        assert self.ai is not None
        schema = SafeProductExtraction.model_json_schema()
        user_prompt = (
            f"اسکیمای JSON:\n{json.dumps(schema, ensure_ascii=False, separators=(',', ':'))}\n\n"
            f"کپشن:\n{caption_text or '(بدون کپشن)'}"
        )
        user_content: str | list[dict[str, Any]] = user_prompt
        if image_paths:
            parts: list[dict[str, Any]] = [{"type": "text", "text": user_prompt}]
            for img in image_paths:
                ext = img.suffix.lstrip(".").lower() or "jpg"
                mime = {"jpg": "jpeg", "jpeg": "jpeg", "png": "png", "webp": "webp"}.get(
                    ext, "jpeg"
                )
                b64 = base64.b64encode(img.read_bytes()).decode()
                parts.append(
                    {"type": "image_url", "image_url": {"url": f"data:image/{mime};base64,{b64}"}}
                )
            user_content = parts

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": VLM_SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ]
        used_vision = bool(image_paths)
        for _attempt in range(3):
            try:
                text = await self.ai.chat(messages, temperature=0)
            except Exception:
                # اگر مدل vision نباشد و تصویر رد شود، بلافاصله روی متن خالص fallback می‌کنیم
                if used_vision:
                    used_vision = False
                    messages = [
                        {"role": "system", "content": VLM_SYSTEM_PROMPT},
                        {"role": "user", "content": user_prompt},
                    ]
                    continue
                return None
            try:
                return SafeProductExtraction.model_validate(_defensive_parse(text))
            except (ValidationError, ValueError) as e:
                messages.append(
                    {
                        "role": "user",
                        "content": VLM_RETRY_PROMPT.format(reason=str(e)[:500]),
                    }
                )
        return None

    async def _vlm_extract_safe(
        self, caption_text: str, image_paths: list[Path] | None = None
    ) -> SafeProductExtraction | None:
        """مغزِ هوشمند (Qwen محلی/BoxAPI) — با مسیرِ فرار: هر شکست یا امتناع → None ← regex ادامه می‌دهد."""
        res = await self._vlm_extract_raw(caption_text, image_paths=image_paths)
        return res if (res is not None and res.extraction_successful) else None


# ── کمکی‌ها ─────────────────────────────────────────────────────────────────────

_SHOP_ORDER_CUE_RE = re.compile(
    r"(?:همه\s+محصولات\s+موجود|محصولات\s+موجود|موجود\s+هست|ثبت\s+سفارش|سفارش\s+دایرکت|قیمت\s+و\s+موجودی)"
)


def has_explicit_shop_cue(caption: str) -> bool:
    """آیا نشانه‌ی فروش/محصولِ قطعی در متن وجود دارد؟ (برند، واژه‌ی کالا یا دستورِ موجودی/سفارش)."""
    if find_brand(caption) or find_product_name(caption):
        return True
    folded = _fold_fa((caption or "").replace("\u200c", " "))
    return bool(_SHOP_ORDER_CUE_RE.search(folded))


def count_category_signals(text: str) -> tuple[Counter[str], int]:
    """شمارش غیرهمپوشانِ سیگنال‌های دسته و تعداد اصطلاحات مشخصِ کالا (ضدِ دوبارشماریِ «آرایش/آرایشی»)."""
    folded = _fold_fa((text or "").replace("\u200c", " "))
    all_terms = sorted(
        [(k, v, True) for k, v in CATEGORY_MAP.items()]
        + [(k, v, False) for k, v in CATEGORY_FAMILY.items()],
        key=lambda x: -len(x[0]),
    )
    occupied = [False] * len(folded)
    counts: Counter[str] = Counter()
    spec_n = 0
    for term, cat, is_spec in all_terms:
        ft = _fold_fa(term)
        for m in re.finditer(re.escape(ft), folded):
            start, end = m.span()
            if any(occupied[start:end]):
                continue
            if len(ft) <= 3:
                before = folded[start - 1] if start > 0 else " "
                after = folded[end:] if end < len(folded) else ""
                if _LETTER_RE.match(before):
                    continue
                if (
                    after
                    and _LETTER_RE.match(after)
                    and not (after[0] == "ه" and (len(after) == 1 or after[1] in "ای "))
                    and not (
                        after[0] == "ی" and (len(after) == 1 or not _LETTER_RE.match(after[1]))
                    )
                ):
                    continue
            for idx in range(start, end):
                occupied[idx] = True
            counts[cat] += 1
            if is_spec:
                spec_n += 1
    return counts, spec_n


def body_is_balanced_mixed(caption: str) -> bool:
    """آیا بدنه‌ی اصلی (پیش از هشتگ) هر دو دسته‌ی آرایشی و پوستی را هم‌وزن و بی‌نام‌محصول دارد؟"""
    body = re.sub(r"#\S+", " ", caption or "")
    bc, bspec = count_category_signals(body)
    return (
        bspec == 0
        and not find_brand(body)
        and bc.get("makeup", 0) > 0
        and bc.get("skincare", 0) > 0
        and bc.get("makeup", 0) == bc.get("skincare", 0)
    )


def should_override_vlm_abstention(caption: str, offline_category: str | None) -> bool:
    """چه زمانی امتناع VLM را به‌نفع کاتالوگ/اکثریت سیگنال کنار می‌گذاریم؟

    - اگر بدنه معرفی متعادلِ پیجِ مختلط است (بدون برند و بدون نام کالا) → امتناع VLM محترم است.
    - اگر دسته‌ی آفلاین دارد و (نشانه‌ی صریح فروش/محصول/برند یا ≥۱ اصطلاح خاص یا ≥۲ سیگنالِ دسته) موجود است → نجات بده.
    - اگر فقط ۱ هشتگ پراکنده روی پست طنز/حمایتی آمده → امتناع VLM محترم است.
    """
    if not offline_category:
        return False
    counts, spec_n = count_category_signals(caption)
    if body_is_balanced_mixed(caption) and spec_n == 0 and not find_brand(caption):
        return False
    return has_explicit_shop_cue(caption) or spec_n >= 1 or counts.get(offline_category, 0) >= 2


def choose_hybrid_category(
    caption: str,
    vlm_category: str | None,
    offline_category: str | None,
    *,
    vlm_has_specific: bool,
) -> str | None:
    """داوری نهایی دسته در پایپ‌لاین ترکیبی: VLM معنایی vs اکثریت غیرهمپوشان روی هشتگ‌ها."""
    if not vlm_category:
        return offline_category
    if offline_category and vlm_category != offline_category and not vlm_has_specific:
        counts, _ = count_category_signals(caption)
        if counts.get(offline_category, 0) > counts.get(vlm_category, 0):
            return offline_category
    return vlm_category


def prefer_product_name(
    vlm_name: str | None, offline_name: str | None, caption: str = ""
) -> str | None:
    """نگهداشتِ نام کاتالوگی و سریِ لاتینِ محصول (ضد حذفِ نام‌هایی مثل Rose Done یا جایگزینی کلی)."""
    series = find_latin_series(caption)
    if not vlm_name:
        return offline_name or (f"محصول {series}" if series else None)
    if (
        offline_name
        and find_product_name(vlm_name) is None
        and not re.search(r"[A-Za-z0-9]{2,}", vlm_name)
    ):
        base = offline_name
    else:
        base = vlm_name
    if series and series.lower() not in base.lower():
        return f"{base} {series}"
    return base


def hybrid_fuse_dict(
    caption: str,
    offline_pred: dict[str, Any],
    vlm_pred: dict[str, Any] | None,
) -> dict[str, Any]:
    """ترکیبِ تولیدیِ Regex قطعی + VLM معنایی (منبع واحدِ truth بین pipeline و بنچ‌مارک).

    - شکستِ فنیِ VLM → برگشت کامل به آفلاین (ایزولاسیون خرابی).
    - امتناعِ آگاهانه‌ی VLM: با `should_override_vlm_abstention` سنجیده می‌شود
      (پست‌های طنز/حمایتی/معرفیِ متعادل پیج پاک می‌شوند، ولی ریلز/پست واقعی با ≥۲ سیگنالِ
      غالب یا نشانه‌ی فروش نجات پیدا می‌کند).
    - موفقیتِ VLM: قیمتِ پرتوانِ regex + داوری اکثریتِ دسته برای مواردِ غیرخاص + حفظ نام سری لاتین.
    """
    if vlm_pred is None or vlm_pred.get("failed") or vlm_pred.get("category") == "__VLM_FAILED__":
        return offline_pred

    if vlm_pred.get("abstained"):
        if should_override_vlm_abstention(caption, offline_pred.get("category")):
            return offline_pred
        return vlm_pred

    o_price = offline_pred.get("price_toman")
    o_conf = float(offline_pred.get("confidence") or 0.0)
    price = o_price if (o_price and o_conf >= 0.8) else (vlm_pred.get("price_toman") or o_price)
    stock = (
        offline_pred.get("stock_state")
        if offline_pred.get("stock_state") == "on_request"
        else (
            "in_stock"
            if price
            else (vlm_pred.get("stock_state") or offline_pred.get("stock_state") or "unknown")
        )
    )
    vlm_has_spec = bool(
        vlm_pred.get("products")
        or vlm_pred.get("brand")
        or find_product_name(vlm_pred.get("product_name") or "") is not None
    )
    category = choose_hybrid_category(
        caption,
        vlm_pred.get("category"),
        offline_pred.get("category"),
        vlm_has_specific=vlm_has_spec,
    )
    return {
        "id": vlm_pred.get("id") or offline_pred.get("id"),
        "price_toman": price,
        "old_price_toman": offline_pred.get("old_price_toman") or vlm_pred.get("old_price_toman"),
        "category": category,
        "product_name": prefer_product_name(
            vlm_pred.get("product_name"), offline_pred.get("product_name"), caption
        ),
        "brand": canonical_brand(vlm_pred.get("brand"))
        or vlm_pred.get("brand")
        or offline_pred.get("brand"),
        "colors": list(
            dict.fromkeys([*(vlm_pred.get("colors") or []), *(offline_pred.get("colors") or [])])
        ),
        "shades": list(
            dict.fromkeys([*(vlm_pred.get("shades") or []), *(offline_pred.get("shades") or [])])
        ),
        "sizes": list(
            dict.fromkeys([*(vlm_pred.get("sizes") or []), *(offline_pred.get("sizes") or [])])
        ),
        "stock_state": stock,
        "confidence": max(float(vlm_pred.get("confidence") or 0.0), o_conf),
        "method": "hybrid",
        "abstained": False,
        "products": vlm_pred.get("products") or offline_pred.get("products") or [],
    }


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
