"""سپرِ «خطای انسانی» — تضادهای برچسبی باید لو بروند، نه اینکه بی‌صدا رد شوند.

درسِ «بادی اسپلش = بهداشتی» (تأییدِ اشتباهِ انسانی): اگر خانواده‌ای از محصولات
برچسب‌های متناقض بگیرد، validate_gold باید سرخ شود. قانون ۵ + نکته‌ی خطای انسانی.
"""

from __future__ import annotations

from peyja.evals import consistency_report, validate_gold


def test_family_contradiction_is_flagged() -> None:
    """«بادی اسپلش» بهداشتی ولی «اسپری بدن» دئو = تضادِ خانواده — باید فریاد بخورد."""
    samples = [
        {"id": "x1", "caption": "بادی اسپلش بث اند بادی", "category": "hygiene"},
        {"id": "x2", "caption": "اسپری داو ضدتعریق", "category": "deo_fragrance"},
    ]
    rep = consistency_report(samples)
    assert rep["family_conflicts"] or rep["term_conflicts"]
    issues = validate_gold(samples)
    assert any("تضاد" in i for i in issues)


def test_consistent_family_is_clean() -> None:
    samples = [
        {"id": "y1", "caption": "بادی اسپلش بث اند بادی", "category": "deo_fragrance"},
        {"id": "y2", "caption": "مام قارچی داو", "category": "deo_fragrance"},
        {"id": "y3", "caption": "ادوپرفیوم تام فورد", "category": "perfume"},
    ]
    rep = consistency_report(samples)
    assert rep["term_conflicts"] == []
    assert rep["family_conflicts"] == []
    assert validate_gold(samples) == []


def test_term_level_contradiction_is_flagged() -> None:
    """یک واژه‌ی واحد با دو دسته‌ی متفاوت = خطای برچسب‌زنی (انسانی یا ماشینی)."""
    samples = [
        {"id": "z1", "caption": "لوسیون بدن ویکتوریا سیکرت", "category": "deo_fragrance"},
        {"id": "z2", "caption": "لوسیون بدن وازلین", "category": "makeup"},
    ]
    rep = consistency_report(samples)
    assert rep["term_conflicts"], "تضادِ واژه‌ای «لوسیون بدن» باید دیده شود"
