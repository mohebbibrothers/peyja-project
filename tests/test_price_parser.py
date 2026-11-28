"""تست‌های پارسر قیمت فارسی — دام‌های واقعی بازار ایران."""

from peyja.extract.pipeline import ExtractionPipeline
from peyja.extract.price_parser import parse_price_fa


def test_extract_signals_fa_price_forms():
    r = ExtractionPipeline.extract_signals_fa("کرم ضد آفتاب SPF50 🌞 قیمت ۴/۸۰ تومان")
    assert r["price_toman"] == 4_800_000
    assert r["category"] == "skincare"
    assert r["route"] == "store"


def test_extract_signals_fa_on_request():
    r = ExtractionPipeline.extract_signals_fa("رژلب مات 💋 قیمت توافقی")
    assert r["stock_state"] == "on_request"
    assert r["price_toman"] is None


def test_extract_signals_fa_discount():
    r = ExtractionPipeline.extract_signals_fa("تخفیف ویژه عطر از ۹۰۰ به ۷۵۰")
    assert r["price_toman"] == 750_000
    assert r["old_price_toman"] == 900_000
    assert r["category"] == "perfume"


def test_extract_signals_fa_cosmetics_categories():
    assert ExtractionPipeline.extract_signals_fa("فوندیشن مات")["category"] == "makeup"
    assert ExtractionPipeline.extract_signals_fa("شامپو ضد ریزش")["category"] == "haircare"
    assert ExtractionPipeline.extract_signals_fa("سرم ویتامین C")["category"] == "skincare"


def test_million_slash_two_digits():
    """«۴/۸۰» ⇒ ۴٬۸۰۰٬۰۰۰ — رایج‌ترین فرم بازار."""
    r = parse_price_fa("قیمت ۴/۸۰ تومان")
    assert r.current is not None
    assert r.current.value_toman == 4_800_000


def test_million_slash_three_digits():
    """«۴/۸۰۰» ⇒ ۴٬۸۰۰٬۰۰۰ — سه رقم بعد از ممیز."""
    r = parse_price_fa("قیمت ۴/۸۰۰")
    assert r.current is not None
    assert r.current.value_toman == 4_800_000


def test_million_and_thousand_words():
    """«۴ میلیون و ۸۰۰» ⇒ ۴٬۸۰۰٬۰۰۰."""
    r = parse_price_fa("فقط ۴ میلیون و ۸۰۰")
    assert r.current is not None
    assert r.current.value_toman == 4_800_000


def test_three_digit_bare_is_thousands():
    """«۴۸۰» بدون واحد = ۴۸۰ هزار تومان — عدد سه‌رقمی همیشه هزارتایی است."""
    r = parse_price_fa("قیمت عالی ۴۸۰")
    assert r.current is not None
    assert r.current.value_toman == 480_000


def test_six_digit_is_toman():
    """«۸۵۰۰۰۰» = ۸۵۰٬۰۰۰ تومان."""
    r = parse_price_fa("قیمت ۸۵۰۰۰۰")
    assert r.current is not None
    assert r.current.value_toman == 850_000


def test_thousand_unit():
    """«۴۸۰ هزار تومان» ⇒ ۴۸۰٬۰۰۰."""
    r = parse_price_fa("۴۸۰ هزار تومان")
    assert r.current is not None
    assert r.current.value_toman == 480_000


def test_million_unit_plain():
    """«۴.۸ میلیون تومان» ⇒ ۴٬۸۰۰٬۰۰۰."""
    r = parse_price_fa("۴.۸ میلیون تومان")
    assert r.current is not None
    assert r.current.value_toman == 4_800_000


def test_full_million_with_commas():
    """«۲٬۵۰۰٬۰۰۰ تومان» ⇒ ۲٬۵۰۰٬۰۰۰ (جداکننده‌ی هزارگان فارسی)."""
    r = parse_price_fa("قیمت ۲٬۵۰۰٬۰۰۰ تومان")
    assert r.current is not None
    assert r.current.value_toman == 2_500_000


def test_price_on_request():
    """«توافقی» ⇒ قیمت درخواستی — نه null کور!"""
    r = parse_price_fa("قیمت توافقی")
    assert r.on_request is True


def test_price_on_request_dm():
    """«قیمت در دایرکت» ⇒ on_request."""
    r = parse_price_fa("قیمت در دایرکت")
    assert r.on_request is True


def test_discount_old_price():
    """«تخفیف ۹۰۰ به ۸۵۰» ⇒ old=۹۰۰هزار، current=۸۵۰هزار."""
    r = parse_price_fa("تخفیف ویژه ۹۰۰ به ۸۵۰")
    assert r.old is not None and r.current is not None
    assert r.old.value_toman == 900_000
    assert r.current.value_toman == 850_000


def test_persian_digits_normalized():
    """ارقام فارسی و لاتین یکسان‌اند."""
    fa = parse_price_fa("قیمت ۴/۸۰")
    en = parse_price_fa("قیمت 4/80")
    assert fa.current is not None and en.current is not None
    assert fa.current.value_toman == en.current.value_toman == 4_800_000


def test_size_numbers_not_price():
    """«سایز ۴۲» نباید قیمت تلقی شود (عدد زیر ۱۰۰)."""
    r = parse_price_fa("سایز ۴۲")
    assert r.current is None or (r.current.value_toman or 0) >= 100_000


def test_real_market_non_price_traps_ignored():
    """شماره تماس/واتساپ، کد تخفیف، سال میلادی، وزن (۴.۱ گرم)، حجم با فاصله (۱۰۰ میل) و قسط قیمت نیستند."""
    assert parse_price_fa("تماس ۰۹۰۲۲۹۹۶۹۴۴ یا ۰۲۱۴۴۶۵۴۰۸۲ و واتس‌اپ +971503413732").current is None
    assert parse_price_fa("کد تخفیف : ۲۰۲۴۱۲۰۱").current is None
    assert parse_price_fa("ادکلن طراحی‌شده در سال 2025 میلادی").current is None
    assert parse_price_fa("کرم ۱۰۰ میل آبرسان").current is None
    r_weight = parse_price_fa("رژ لب حجم ۴.۱ گرم ✅قیمت ۲۹۷ تومان")
    assert r_weight.current is not None and r_weight.current.value_toman == 297_000
    r_inst = parse_price_fa("تخفیف ۱/۹۹۵\nفقط ۱/۶۸۰ تومن\nبا دیجی‌پی در ۴ قسط ۴۲۰ تومنی")
    assert r_inst.current is not None and r_inst.current.value_toman == 1_680_000
    assert r_inst.old is not None and r_inst.old.value_toman == 1_995_000
    r_arrow = parse_price_fa("۲/۵۳۰ ← ۱/۸۹۸ تومان")
    assert r_arrow.current is not None and r_arrow.current.value_toman == 1_898_000
    assert r_arrow.old is not None and r_arrow.old.value_toman == 2_530_000
    r_ml = parse_price_fa("خط چشم حجم ۳.۵ میل ✅️قیمت ۲۵۰ تومان")
    assert r_ml.current is not None and r_ml.current.value_toman == 250_000
    assert parse_price_fa("کرم ۳۴۵ دکتر التیا و مدل ۱۴۷ دکتر التیا").current is None


def test_single_product_multi_paragraph_not_exploded():
    """تکرار نام محصول در بندهای توضیحی تک‌کالا نباید قیمت اصلی را گم کند."""
    cap = (
        "سایه چشم دو عددی پاستل مدل Monochrome\n"
        "✅ قیمت ۲۴۰ تومان\n"
        "سایه چشم دو عددی پاستل ماندگاری عالی دارد."
    )
    multi = ExtractionPipeline.extract_multi_fa(cap)
    assert len(multi) == 1
    assert multi[0]["price_toman"] == 240_000
    assert multi[0]["category"] == "makeup"


def test_empty_text():
    r = parse_price_fa("")
    assert r.current is None
