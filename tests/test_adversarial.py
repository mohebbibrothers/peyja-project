"""مجموعه‌ی پرتگاه‌ها (adversarial) — رگرسیونِ مواردِ سختِ قیمت/شید/دسته/چندمحصوله.

هر دست‌کاریِ آینده‌ی موتورِ آفلاین باید این مجموعه را همچنان سبز نگه دارد (قانون ۴).
"""

from __future__ import annotations

import json
from pathlib import Path

from peyja.extract.pipeline import ExtractionPipeline

ADV_PATH = Path(__file__).resolve().parents[1] / "evals" / "gold-100" / "adversarial.json"


def _extract(caption: str) -> dict:
    r = ExtractionPipeline.extract_signals_fa(caption)
    r["products"] = ExtractionPipeline.extract_multi_fa(caption)
    r["is_multi_product"] = len(r["products"]) > 1 or ExtractionPipeline.is_multi_caption(caption)
    return r


def test_adversarial_suite() -> None:
    cases = json.loads(ADV_PATH.read_text(encoding="utf-8"))
    assert len(cases) >= 18
    fails: list[str] = []
    for case in cases:
        r = _extract(case["caption"])
        for key, want in case["expected"].items():
            if key == "min_products":
                if len(r["products"]) < want:
                    fails.append(f"{case['id']}: products={len(r['products'])} < {want}")
            elif key == "is_multi_product":
                if r.get(key) != want:
                    fails.append(f"{case['id']}: is_multi={r.get(key)} want={want}")
            else:
                if r.get(key) != want:
                    fails.append(f"{case['id']}: {key} want={want!r} got={r.get(key)!r}")
    assert not fails, "\n".join(fails)


def test_adversarial_covers_lexicon_without_dead_keys() -> None:
    """قانون ۵ بند ۴: هر کلیدِ CATEGORY_MAP باید در dev یا مجموعه پرتگاه (adversarial) بسوزد."""
    from peyja.evals import lexicon_audit

    gold_path = Path(__file__).resolve().parents[1] / "evals" / "gold-100" / "samples.json"
    gold = json.loads(gold_path.read_text(encoding="utf-8"))
    adv = json.loads(ADV_PATH.read_text(encoding="utf-8"))
    audit = lexicon_audit(gold + adv)
    assert audit["dead_keys"] == [], f"کلیدهای مرده در واژگان دسته: {audit['dead_keys']}"
