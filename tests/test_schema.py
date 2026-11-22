"""تست‌های قرارداد ساخت‌یافته‌ی استخراج — «مسیر فرار» و قواعد کسب‌وکار."""

import pytest
from pydantic import ValidationError

from peyja.extract.schema import SafeProductExtraction, route_by_confidence


def test_failure_mode_requires_reason():
    with pytest.raises(ValidationError):
        SafeProductExtraction(extraction_successful=False)  # بدون failure_reason


def test_failure_mode_valid():
    r = SafeProductExtraction(
        extraction_successful=False, failure_reason="کپشن خالی است"
    )
    assert not r.extraction_successful


def test_price_bounds_enforced():
    with pytest.raises(ValidationError):
        SafeProductExtraction(price_toman=10)  # زیر حداقل
    with pytest.raises(ValidationError):
        SafeProductExtraction(price_toman=10_000_000_000)  # بالای حداکثر


def test_old_price_cannot_be_lower():
    with pytest.raises(ValidationError):
        SafeProductExtraction(price_toman=500_000, old_price_toman=400_000)


def test_confidence_bounds():
    with pytest.raises(ValidationError):
        SafeProductExtraction(confidence=1.5)
    with pytest.raises(ValidationError):
        SafeProductExtraction(confidence=-0.1)


def test_successful_extraction_happy_path():
    r = SafeProductExtraction(
        title_fa="کرم ضد آفتاب",
        category="skincare",
        price_toman=4800000,
        confidence=0.9,
    )
    assert r.extraction_successful
    assert r.price_toman == 4800000


def test_confidence_routing_bands():
    assert route_by_confidence(0.9) == "store"
    assert route_by_confidence(0.7) == "store"
    assert route_by_confidence(0.55) == "review"
    assert route_by_confidence(0.2) == "reject"
