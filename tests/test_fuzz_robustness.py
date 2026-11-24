"""فازینگِ سبک (قانون ۵ — منتقدِ بی‌رحم): موتور نباید روی هیچ ورودیِ غیرمنتظره‌ای بلرزد.

ورودی‌های تصادفیِ قطعی (seed ثابت) از واژه‌ها/ایموجی/ارقام/جداکننده‌ها ساخته می‌شوند؛
هرگونه استثنا = شکست. خروجی باید همیشه در قراردادِ schema بماند.
"""

from __future__ import annotations

import random

from peyja.extract.pipeline import ExtractionPipeline

_WORDS = [
    "کرم",
    "ضد",
    "افتاب",
    "آبرسان",
    "مام",
    "اسپری",
    "داو",
    "قیمت",
    "تومان",
    "موجود",
    "دایرکت",
    "شماره",
    "میل",
    "ریمل",
    "اسنس",
    "شیگلم",
    "پالت",
    "سایه",
    "بادی",
    "اسپلش",
    "خوشبو",
    "کننده",
    "قبل",
    "تخفیف",
    "توافقی",
    "۴/۸۰",
    "550",
    "۲,۵۰۰,۰۰۰",
    "کیکو",
    "Lilyeve",
    "bb",
    "کد",
    "؟",
    "…",
    "|",
    "؛",
    "•",
    "۱.",
    "2)",
    "\n",
    "\u200c",
]
_EMOJI = ["💸", "🩵", "🤍", "🍄", "🌸", "✅", "🎀", "😂", "🫧", "🛍️", "✨", "🎁"]


def test_fuzz_extraction_never_crashes() -> None:
    rng = random.Random(20261201)  # قطعی و تکرارپذیر
    for i in range(400):
        parts = []
        for _ in range(rng.randint(1, 25)):
            parts.append(rng.choice(_WORDS + _EMOJI))
            if rng.random() < 0.3:
                parts.append(" ")
        caption = "".join(parts)
        r = ExtractionPipeline.extract_signals_fa(caption)
        # قراردادِ خروجی نباید بشکند
        assert r["category"] in (
            "skincare",
            "makeup",
            "perfume",
            "haircare",
            "hygiene",
            "deo_fragrance",
            "other",
            None,
        ), f"#{i} category={r['category']!r}"
        assert r["price_toman"] is None or 1_000 <= r["price_toman"] <= 5_000_000_000
        assert r["old_price_toman"] is None or r["old_price_toman"] >= 1_000
        assert 0.0 <= r["confidence"] <= 1.0
        # چندمحصوله هم نباید بلرزد
        multi = ExtractionPipeline.extract_multi_fa(caption)
        assert isinstance(multi, list)
        for p in multi:
            assert p["category"] in (
                "skincare",
                "makeup",
                "perfume",
                "haircare",
                "hygiene",
                "deo_fragrance",
                "other",
                None,
            )
        assert ExtractionPipeline.is_multi_caption(caption) in (True, False)
