"""رگرسیون اصلاح‌های چرخه‌ی gold-100 (بچ ۱) — درس‌های «عدد سرنوشت».

خطاهای baseline که این تست‌ها قفلشان می‌کنند:
1. «حجم ۲۵۰ mil» / «حجم ۴۰۰» به‌جای قیمت خوانده می‌شدند (false positive).
2. «قیمت توافقی» با حدسِ بدون واحد باخته بود.
3. املای «ابرسان/ضد افتاب» از «آبرسان/ضد آفتاب» جدا بود (دسته‌یابی).
4. «پوست سر» بر «سرم» باخته بود (haircare vs skincare).
5. «اسپری بدن» عطر حساب می‌شد؛ «bb کرم» skincare؛ «میکاپ/لیپ‌گلاس» ناشناخته.
6. سایز/حجم فقط با پیشوند «سایز» درمی‌آمد («شماره ۱۲»، «۹۰ میل» گم می‌شد).
"""

from peyja.extract.caption import analyze_caption
from peyja.extract.pipeline import ExtractionPipeline, _guess_category


def test_volume_is_not_price() -> None:
    s = analyze_caption("امپول کلاژن فارم استی ابرسان چند کاره با حجم250mil")
    assert s.prices.current is None  # «250mil» حجم است نه قیمت
    assert "250" in s.sizes


def test_size_words_are_not_price() -> None:
    r = ExtractionPipeline.extract_signals_fa("شامپو بدون سولفات حجم ۴۰۰ | قیمت توافقی")
    assert r["price_toman"] is None
    assert r["stock_state"] == "on_request"
    assert "400" in r["sizes"]


def test_on_request_beats_bare_guess() -> None:
    r = ExtractionPipeline.extract_signals_fa("کرم پودر ۴۸۰ | قیمت توافقی")
    # «۴۸۰» بدون واحد حدس است؛ «توافقی» مقدم
    assert r["price_toman"] is None
    assert r["method"] == "on_request"


def test_real_price_still_wins() -> None:
    r = ExtractionPipeline.extract_signals_fa("ریمل اسنس موجود شد ۵۵۰💸")
    assert r["price_toman"] == 550_000


def test_folded_category_matching() -> None:
    assert _guess_category("ضد افتاب ایزیدین و ابرسان") == "skincare"  # بدون آ
    assert _guess_category("محصولات مراقبتی و پوستی") == "skincare"
    assert _guess_category("با میکاپ صورتی لطیف") == "makeup"
    assert _guess_category("اسکین کر حرفه‌ای") == "skincare"


def test_longest_key_wins() -> None:
    assert _guess_category("سرم ضدریزش و تقویت کننده پوست سر") == "haircare"
    assert _guess_category("BB کرم بگیری یا CC کرم؟") == "makeup"
    assert _guess_category("کرم پودر مات") == "makeup"  # «کرم پودر» بر «کرم»


def test_body_spray_is_deo_not_perfume() -> None:
    # «خوشبو کننده» = دسته‌ی دقیق‌ترِ کاربر (نکته‌ی ۴) — نه بهداشتیِ خام، نه عطر
    assert _guess_category("اسپری داو ضدتعریق") == "deo_fragrance"
    assert _guess_category("مام قارچی و لوسیون بدن") == "deo_fragrance"
    assert _guess_category("ادوپرفیوم ۹۰ میل") == "perfume"  # عطر واقعی هنوز perfume
    assert (
        _guess_category("بادی اسپلش بث اند بادی") == "deo_fragrance"
    )  # تصحیحِ بازبینی (خطای تأییدِ دور ۱)


def test_short_keys_need_word_boundary() -> None:
    # «مداومه» حاوی «داو» نیست؛ «علاقه» حاوی «لاک» نیست؛ «سرمایه» حاوی «سرم» نیست
    assert _guess_category("مراقبت درست و مداومه برای پوست زیبا") == "skincare"
    assert _guess_category("علاقه‌مند به یادگیری روتین پوستی") == "skincare"
    assert _guess_category("سرمایه‌گذاری روی پوستت") == "skincare"
    assert _guess_category("داو مام قارچی") == "deo_fragrance"  # مرزِ واژه ✓
    assert _guess_category("کرمهای ضد آفتاب") == "skincare"  # پسوند «های» مجاز


def test_size_and_color_extraction_broadened() -> None:
    s = analyze_caption("رژلب شماره ۱۲ | پالت در تناژ صورتی و نود و burgundy")
    assert "12" in s.shades  # «شماره ۱۲» = شید — نه رنگ، نه سایز
    assert "12" not in s.sizes
    assert "صورتی" in s.colors
    assert "نود" in s.colors
    assert "burgundy" in s.colors
    s2 = analyze_caption("ادوپرفیوم ۹۰ میل")
    assert "90" in s2.sizes


def test_shade_is_first_class_not_color_not_size() -> None:
    s = analyze_caption("لیپ گلاس 44کیکو پرطرفدارترین رنگشه")
    assert "44" in s.shades
    s2 = analyze_caption("کیکو ۱۷")
    assert "17" in s2.shades
    # دام‌ها: این‌ها شید نیستند
    assert analyze_caption("ماندگاری ۲۴ ساعت").shades == []
    assert analyze_caption("پالت ۱۲ رنگ سایه").shades == []
    s3 = analyze_caption("ادوپرفیوم ۹۰ میل")
    assert "90" not in s3.shades
    # بدون لنگرِ آرایشی، عددِ برهنه شید نیست
    assert analyze_caption("کرم ضد آفتاب SPF50 مناسب پوست چرب ۲۵").shades == []
