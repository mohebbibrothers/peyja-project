"""کیفیتِ مهندسیِ پرامپتِ VLM — قله‌ی تکنولوژیِ پرامپت‌نویسی (نکته‌ی ۴ کاربر).

پرامپت باید: نقش، پروتکلِ گام‌به‌گام، دانشِ بومیِ قیمت، دام‌ها، Few-shot
(شامل deo_fragrance، چندمحصوله و پستِ بی‌محصول)، قراردادِ خروجی، Self-check و
«حدس‌ممنوعی» را داشته باشد — و همه‌ی نمونه‌ها با SafeProductExtraction اعتبارسنجی شوند.
"""

from peyja.extract.pipeline import VLM_RETRY_PROMPT, VLM_SYSTEM_PROMPT
from peyja.extract.schema import SafeProductExtraction


def test_prompt_has_professional_sections() -> None:
    for marker in (
        "نقش",  # Role framing
        "پروتکل",  # Task decomposition
        "دام‌ها",  # Negative knowledge / pitfalls
        "Few-shot",  # In-context demonstrations
        "قراردادِ خروجی",  # Output contract
        "Self-check",  # Self-verification step
    ):
        assert marker in VLM_SYSTEM_PROMPT, f"بخش «{marker}» در پرامپت نیست"


def test_prompt_encodes_iranian_price_and_category_rules() -> None:
    assert "4800000" in VLM_SYSTEM_PROMPT  # «۴/۸۰»
    assert "550000" in VLM_SYSTEM_PROMPT  # «۵۵۰💸»
    assert "on_request" in VLM_SYSTEM_PROMPT  # «توافقی»
    assert "shades" in VLM_SYSTEM_PROMPT  # شید = فیلد مستقل
    assert "deo_fragrance" in VLM_SYSTEM_PROMPT  # دسته‌ی ۷گانه (بادی اسپلش/اسپری)
    assert "بادی اسپلش" in VLM_SYSTEM_PROMPT


def test_prompt_forbids_guessing_and_extra_text() -> None:
    assert "حدس" in VLM_SYSTEM_PROMPT
    assert "فقط و فقط" in VLM_SYSTEM_PROMPT
    assert "JSON" in VLM_SYSTEM_PROMPT


def test_retry_prompt_carries_reason_and_contract() -> None:
    rendered = VLM_RETRY_PROMPT.format(reason="ValidationError_X42")
    assert "ValidationError_X42" in rendered
    assert "JSON" in rendered
    assert "حدس" in rendered


def test_few_shot_examples_are_valid_schema_json() -> None:
    """همه‌ی نمونه‌های پرامپت باید JSON معتبرِ مطابق SafeProductExtraction باشند."""
    import json
    import re

    blobs = re.findall(r"\{\"extraction_successful\".*?\}(?=\n|$)", VLM_SYSTEM_PROMPT)
    assert len(blobs) >= 5
    positives = 0
    negatives = 0
    for b in blobs:
        data = json.loads(b)
        parsed = SafeProductExtraction.model_validate(data)
        if parsed.extraction_successful:
            positives += 1
        else:
            negatives += 1
    assert positives >= 4
    assert negatives >= 1  # پستِ بدون محصول (C1) باید نمونه‌ی امتناعِ صریح داشته باشد


def test_prompt_addresses_multi_product_and_names() -> None:
    """نکته‌ی ۱ و ۲ کاربر: چندمحصوله + نام محصول باید در پرامپت باشد."""
    assert "products" in VLM_SYSTEM_PROMPT
    assert "چندمحصوله" in VLM_SYSTEM_PROMPT
    assert "product_name" in VLM_SYSTEM_PROMPT


def test_prompt_v2_covers_lip_balm_colors_and_strict_abstention() -> None:
    """اصلاحات مبتنی بر بنچ‌مارک ۶۴تایی: رفع امتناع کاذب، بالم لب، اسپری داو، حجم و رنگ در هشتگ."""
    assert "بالم لب" in VLM_SYSTEM_PROMPT
    assert "اسپری داو" in VLM_SYSTEM_PROMPT
    assert "اسپریای داو" in VLM_SYSTEM_PROMPT
    assert "Rose Done" in VLM_SYSTEM_PROMPT
    assert "مامانمم خریدههه" in VLM_SYSTEM_PROMPT
    assert "volume_ml" in VLM_SYSTEM_PROMPT
    assert "اسنسصورتی" in VLM_SYSTEM_PROMPT
    assert "فقط و فقط" in VLM_SYSTEM_PROMPT and "طنز" in VLM_SYSTEM_PROMPT
    assert "اکثریت شمارش" in VLM_SYSTEM_PROMPT


def test_hybrid_fusion_and_latin_series_preservation() -> None:
    """منطق تلفیق دور ۴: اکثریت غیرهمپوشان، نجات روتین پوستی و حفظ نام سری لاتین."""
    from peyja.extract.catalog import find_latin_series
    from peyja.extract.pipeline import (
        ExtractionPipeline,
        body_is_balanced_mixed,
        count_category_signals,
        hybrid_fuse_dict,
        prefer_product_name,
    )

    assert find_latin_series("با Rose Done فارماسی میکاپ بساز") == "Rose Done"
    assert find_latin_series("کرم ضدآفتاب SPF50") is None

    assert prefer_product_name("پالت سایه", None, "با Rose Done فارماسی") == "پالت سایه Rose Done"
    assert prefer_product_name("محصولات پوستی", "دور چشم", "#دورچشم") == "دور چشم"

    # دو هشتگ مراقبت پوستی vs یک آرایشی → اکثریت غیرهمپوشان بر جهت‌گیری ترتیبی غلبه می‌کند
    cap_adv = "همه محصولات موجود #لوازم_آرایشی #محصولات_مراقبتی #روتین_پوستی #لوازم_بهداشتی"
    counts, _ = count_category_signals(cap_adv)
    assert counts["skincare"] == 2 and counts["makeup"] == 1
    off_adv = ExtractionPipeline.extract_signals_fa(cap_adv)
    fused_adv = hybrid_fuse_dict(
        cap_adv,
        off_adv,
        {"id": "x1", "category": "makeup", "product_name": "محصولات آرایشی", "abstained": False},
    )
    assert fused_adv["category"] == "skincare"

    # پست روتین پوستی با امتناع کاذب VLM نجات می‌یابد؛ معرفی متعادل پیج مختلط محترم می‌ماند
    cap_routine = "پوست زیباتر اتفاقی نیست! مشاوره رایگان #روتین_پوست #پوست_خشک #مراقبت_پوست"
    off_routine = ExtractionPipeline.extract_signals_fa(cap_routine)
    res_routine = hybrid_fuse_dict(cap_routine, off_routine, {"id": "x2", "abstained": True})
    assert res_routine["category"] == "skincare"

    cap_intro = "این فقط بخشی از محصولات پیجمه، محصولات آرایشی و پوستی اصل #آرایشی #بهداشتی"
    assert body_is_balanced_mixed(cap_intro) is True
    off_intro = ExtractionPipeline.extract_signals_fa(cap_intro)
    res_intro = hybrid_fuse_dict(
        cap_intro, off_intro, {"id": "x3", "category": None, "abstained": True}
    )
    assert res_intro["category"] is None and res_intro["abstained"] is True

    # یک هشتگ پراکنده در پست گدایی لایک، امتناع VLM را نمی‌شکند
    cap_beg = "فقط یه ❤️ بزار واسم تا دیده بشم #حمایت #میکاپ"
    off_beg = ExtractionPipeline.extract_signals_fa(cap_beg)
    res_beg = hybrid_fuse_dict(cap_beg, off_beg, {"id": "x4", "category": None, "abstained": True})
    assert res_beg["category"] is None and res_beg["abstained"] is True
