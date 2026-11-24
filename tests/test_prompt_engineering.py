"""کیفیتِ مهندسیِ پرامپتِ VLM — قله‌ی تکنولوژیِ پرامپت‌نویسی (نکته‌ی ۴ کاربر).

پرامپت باید: نقش، پروتکلِ گام‌به‌گام، دانشِ بومیِ قیمت، دام‌ها، Few-shot،
قراردادِ خروجی، Self-check و «حدس‌ممنوعی» را داشته باشد — و retry دلیل‌دار باشد.
"""

from peyja.extract.pipeline import VLM_RETRY_PROMPT, VLM_SYSTEM_PROMPT


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


def test_prompt_encodes_iranian_price_rules() -> None:
    assert "4800000" in VLM_SYSTEM_PROMPT  # «۴/۸۰»
    assert "550000" in VLM_SYSTEM_PROMPT  # «۵۵۰💸»
    assert "on_request" in VLM_SYSTEM_PROMPT  # «توافقی»
    assert "shades" in VLM_SYSTEM_PROMPT  # شید = فیلد مستقل


def test_prompt_forbids_guessing_and_extra_text() -> None:
    assert "حدس" in VLM_SYSTEM_PROMPT
    assert "فقط و فقط" in VLM_SYSTEM_PROMPT
    assert "JSON" in VLM_SYSTEM_PROMPT


def test_retry_prompt_carries_reason_and_contract() -> None:
    rendered = VLM_RETRY_PROMPT.format(reason="ValidationError_X42")
    assert "ValidationError_X42" in rendered
    assert "JSON" in rendered
    assert "حدس" in rendered


def test_few_shot_examples_are_valid_json() -> None:
    """نمونه‌های داخل پرامپت باید JSON واقعی باشند (نه متنِ شکسته)."""
    import json
    import re

    blobs = re.findall(r"\{\"extraction_successful\".*?\}(?=\n|$)", VLM_SYSTEM_PROMPT)
    assert len(blobs) == 4
    for b in blobs:
        data = json.loads(b)
        assert data["extraction_successful"] is True
        assert "price_toman" in data
        assert "shades" in data or "products" in data


def test_prompt_addresses_multi_product_and_names() -> None:
    """نکته‌ی ۱ و ۲ کاربر: چندمحصوله + نام محصول باید در پرامپت باشد."""
    assert "products" in VLM_SYSTEM_PROMPT
    assert "چندمحصوله" in VLM_SYSTEM_PROMPT
    assert "product_name" in VLM_SYSTEM_PROMPT
