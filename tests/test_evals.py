"""تست‌های منطق نمره‌دهی gold-100 (peyja.evals) — قلب «عدد سرنوشت»."""

from typing import Any

from peyja.evals import (
    price_exact_match,
    price_within_tolerance,
    render_verification_sheet,
    score,
    validate_gold,
)


def test_price_exact_match_handles_none() -> None:
    assert price_exact_match(None, None)
    assert price_exact_match(500_000, 500_000)
    assert not price_exact_match(500_000, None)
    assert not price_exact_match(500_000, 600_000)


def test_price_tolerance_2pct() -> None:
    assert price_within_tolerance(1_000_000, 1_019_000)  # ۱.۹٪
    assert not price_within_tolerance(1_000_000, 1_021_000)  # ۲.۱٪
    assert price_within_tolerance(None, None)
    assert not price_within_tolerance(None, 10)


def test_score_gates_and_synthetic_split() -> None:
    gold: list[dict[str, Any]] = [
        {
            "id": "g1",
            "price_toman": 100,
            "category": "makeup",
            "colors": ["قرمز"],
            "sizes": [],
        },
        {"id": "g2", "price_toman": None, "category": None, "colors": [], "sizes": []},
        {
            "id": "g3",
            "price_toman": 100,
            "category": "skincare",
            "colors": [],
            "sizes": [],
            "synthetic": True,
        },
    ]
    preds: dict[str, dict[str, Any]] = {
        "g1": {
            "price_toman": 100,
            "category": "makeup",
            "colors": ["قرمز"],
            "sizes": [],
        },
        "g2": {"price_toman": None, "category": None, "colors": [], "sizes": []},
        "g3": {"price_toman": 101, "category": "skincare", "colors": [], "sizes": []},
    }
    r = score(gold, preds)
    assert r["n"] == 3
    assert r["price_exact_match"] == round(2 / 3, 4)
    assert r["price_within_2pct"] == 1.0  # ۱۰۱/۱۰۰ = ۱٪ ≤ ۲٪
    assert r["category_accuracy"] == 1.0
    assert r["attribute_accuracy"] == 1.0  # فقط g1 برچسب ویژگی دارد
    assert r["attribute_labeled"] == 1
    assert r["gate_price_pass"] and r["gate_category_pass"] and r["gate_attribute_pass"]
    assert r["real_only"]["n"] == 2
    assert r["synthetic_only"]["n"] == 1


def test_score_empty_gold_attrs_not_penalized() -> None:
    gold: list[dict[str, Any]] = [
        {"id": "g1", "price_toman": None, "category": None, "colors": [], "sizes": []}
    ]
    preds: dict[str, dict[str, Any]] = {
        "g1": {"price_toman": None, "category": None, "colors": [], "sizes": []}
    }
    r = score(gold, preds)
    assert r["attribute_labeled"] == 0
    assert r["gate_attribute_pass"] is True  # بدون نمونه‌ی برچسب‌دار = بدون گیت


def test_validate_gold_catches_problems() -> None:
    bad: list[dict[str, Any]] = [
        {"id": "a", "caption": "", "price_toman": 50},  # کپشن خالی + قیمت نامعتبر
        {"id": "a", "caption": "x", "category": "bogus"},  # id تکراری + دسته ناشناخته
        {
            "id": "b",
            "caption": "x",
            "price_toman": 200,
            "old_price_toman": 100,
        },  # old < price
    ]
    issues = validate_gold(bad)
    assert any("تکراری" in i for i in issues)
    assert any("کپشن خالی" in i for i in issues)
    assert any("قیمت قدیم" in i for i in issues)
    assert any("ناشناخته" in i for i in issues)
    assert validate_gold([{"id": "ok", "caption": "سلام", "price_toman": 480_000}]) == []


def test_verification_sheet_renders_rows() -> None:
    sheet = render_verification_sheet(
        [
            {
                "id": "g1",
                "caption": "کرم | ضد آفتاب",
                "price_toman": 480_000,
                "category": "skincare",
            }
        ]
    )
    assert "g1" in sheet
    assert "480000" in sheet
    assert "☐" in sheet
    assert "/" in sheet  # میله‌ی جدول escape شده


def test_product_match_folds_alef_madda_and_brand_aliases() -> None:
    """نرمال‌سازی آ→ا و هم‌ارزی برندهای لاتین/فارسی در نمره‌دهی (خطاهای gold-025 و gold-043)."""
    from peyja.evals import _product_match
    from peyja.extract.catalog import canonical_brand

    assert canonical_brand("Sheglam") == "شیگلم"
    assert canonical_brand("kiko milano") == "کیکو میلانو"
    assert _product_match({"name": None, "brand": "شیگلم"}, {"brand": "Sheglam"})
    assert _product_match({"name": "آبرسان", "brand": None}, {"product_name": "ابرسان"})
