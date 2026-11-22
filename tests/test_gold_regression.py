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
    preds = {
        s["id"]: ExtractionPipeline.extract_signals_fa(s.get("caption") or "") for s in samples
    }
    return score(samples, preds)


def test_gold_gates_pass() -> None:
    r = _run_gold()
    assert r["gate_price_pass"], f"گیت قیمت افت کرد: {r['price_within_2pct']}"
    assert r["gate_category_pass"], f"گیت دسته افت کرد: {r['category_accuracy']}"
    assert r["gate_attribute_pass"], f"گیت ویژگی افت کرد: {r['attribute_accuracy']}"


def test_gold_score_floors_locked() -> None:
    """کفِ سخت‌تر از گیت — اعدادِ ثبت‌شده‌ی بچ ۱؛ پسرفتِ بی‌صدا ممنوع."""
    r = _run_gold()
    assert r["price_within_2pct"] >= 0.99, f"قیمت: {r['price_within_2pct']}"
    assert r["category_accuracy"] >= 0.90, f"دسته: {r['category_accuracy']}"
    assert r["attribute_accuracy"] >= 0.85, f"ویژگی: {r['attribute_accuracy']}"


def test_gold_dataset_is_valid() -> None:
    from peyja.evals import validate_gold

    samples = json.loads(GOLD_PATH.read_text(encoding="utf-8"))
    assert validate_gold(samples) == []
    assert len(samples) >= 60
