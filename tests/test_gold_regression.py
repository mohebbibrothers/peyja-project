"""قفلِ کیفیتِ استخراج در CI — «عدد سرنوشت» هرگز نباید پسرفت کند (قانون ۴).

هر اجرای pytest (و هر CI) موتور آفلاین را روی gold-100 می‌دواند و گیت‌ها + کفِ نمره‌ها
را می‌سنجد. اگر قاعده‌ای اضافه/تغییر کرد و نمره ریخت، همین تست سرخ می‌شود.
"""

from __future__ import annotations

import json
from pathlib import Path

from peyja.evals import score
from peyja.extract.pipeline import ExtractionPipeline

GOLD_PATH = Path(__file__).resolve().parents[1] / "evals" / "gold-100" / "samples.json"


def _run_gold() -> dict:
    samples = json.loads(GOLD_PATH.read_text(encoding="utf-8"))
    preds = {}
    for s in samples:
        cap = s.get("caption") or ""
        r = ExtractionPipeline.extract_signals_fa(cap)
        r["products"] = ExtractionPipeline.extract_multi_fa(cap)  # نکته‌ی ۱
        preds[s["id"]] = r
    return score(samples, preds)


def test_gold_gates_pass() -> None:
    r = _run_gold()
    assert r["gate_price_pass"], f"گیت قیمت افت کرد: {r['price_within_2pct']}"
    assert r["gate_category_pass"], f"گیت دسته افت کرد: {r['category_accuracy']}"
    assert r["gate_attribute_pass"], f"گیت ویژگی افت کرد: {r['attribute_accuracy']}"


def test_gold_score_floors_locked() -> None:
    """کفِ سخت‌تر از گیت — اعدادِ ثبت‌شده‌ی بچ ۱ (دور ۲ تأیید انسانی)؛ پسرفتِ بی‌صدا ممنوع."""
    r = _run_gold()
    assert r["price_within_2pct"] >= 0.99, f"قیمت: {r['price_within_2pct']}"
    assert r["category_accuracy"] >= 0.94, f"دسته: {r['category_accuracy']}"
    assert r["attribute_accuracy"] >= 0.85, f"ویژگی: {r['attribute_accuracy']}"
    # نکته‌ی ۱ و ۲ کاربر: بازیابیِ محصولاتِ چندمحصوله + نام/برند — قفلِ کیفیت
    assert r["product_recall"] >= 0.95, f"بازیابی محصول: {r['product_recall']}"
    assert r["product_precision"] >= 0.85, f"دقتِ تکه‌های محصول: {r['product_precision']}"
    assert r["name_accuracy"] >= 0.90, f"نام محصول: {r['name_accuracy']}"
    assert r["brand_accuracy"] >= 0.95, f"برند: {r['brand_accuracy']}"


def test_gold_test_split_gates_when_holdout_exists() -> None:
    """قانون ۵: اگر نمونه‌ی holdout (split=test) باشد، گیت‌ها روی آن هم باید پاس شوند."""
    r = _run_gold()
    t = r.get("test_only")
    if not t:
        return  # هنوز holdout جمع‌نشده (بچ ۲) — تا آن زمان این گیت آماده‌به‌کار است
    assert t["gate_price_pass"], f"گیت قیمتِ holdout: {t['price_within_2pct']}"
    assert t["gate_category_pass"], f"گیت دسته‌ی holdout: {t['category_accuracy']}"
    assert t["gate_attribute_pass"], f"گیت ویژگیِ holdout: {t['attribute_accuracy']}"


def test_gold_dataset_is_valid() -> None:
    from peyja.evals import validate_gold

    samples = json.loads(GOLD_PATH.read_text(encoding="utf-8"))
    assert validate_gold(samples) == []
    assert len(samples) >= 60
