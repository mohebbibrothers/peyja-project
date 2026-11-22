"""ارزیابی extraction روی gold-100 — «عدد سرنوشت».

استفاده:
  python evals/score.py --gold evals/gold-100/samples.json --pred out/products.json

گیت‌های موفقیت Phase 1:
  - قیمت:   ≥ ۸۵٪ (Exact Match + درصد خطای مبلغ)
  - دسته/ویژگی: ≥ ۷۵٪
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def price_exact_match(gold_price: int | None, pred_price: int | None) -> bool:
    if gold_price is None and pred_price is None:
        return True
    return gold_price == pred_price


def price_within_tolerance(
    gold_price: int | None, pred_price: int | None, tol: float = 0.02
) -> bool:
    """خطای کمتر از ۲٪ = قابل‌قبول تجاری (اختلاف تخفیف/سایز)."""
    if gold_price is None or pred_price is None:
        return gold_price == pred_price
    return abs(gold_price - pred_price) / max(gold_price, 1) <= tol


def score(gold: list[dict[str, Any]], preds: dict[str, dict[str, Any]]) -> dict[str, Any]:
    n = len(gold)
    price_em = price_tol = cat_ok = attr_ok = 0
    errors: list[dict[str, Any]] = []

    for sample in gold:
        key = sample["id"]
        pred = preds.get(key, {})
        g_price = sample.get("price_toman")
        p_price = pred.get("price_toman")
        if price_exact_match(g_price, p_price):
            price_em += 1
        if price_within_tolerance(g_price, p_price):
            price_tol += 1
        else:
            errors.append({"id": key, "type": "price", "gold": g_price, "pred": p_price})

        if (sample.get("category") or "") == (pred.get("category") or ""):
            cat_ok += 1
        else:
            errors.append(
                {
                    "id": key,
                    "type": "category",
                    "gold": sample.get("category"),
                    "pred": pred.get("category"),
                }
            )

        g_attrs = set(map(str, sample.get("colors", []) + sample.get("sizes", [])))
        p_attrs = set(map(str, pred.get("colors", []) + pred.get("sizes", [])))
        if g_attrs and len(g_attrs & p_attrs) / len(g_attrs) >= 0.5:
            attr_ok += 1

    return {
        "n": n,
        "price_exact_match": round(price_em / n, 4) if n else 0,
        "price_within_2pct": round(price_tol / n, 4) if n else 0,
        "category_accuracy": round(cat_ok / n, 4) if n else 0,
        "attribute_accuracy": round(attr_ok / n, 4) if n else 0,
        "gate_price_pass": (price_tol / n >= 0.85) if n else False,
        "gate_category_pass": (cat_ok / n >= 0.75) if n else False,
        "errors": errors[:50],
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gold", required=True)
    ap.add_argument("--pred", required=True)
    args = ap.parse_args()
    gold = json.loads(Path(args.gold).read_text(encoding="utf-8"))
    preds = {p["id"]: p for p in json.loads(Path(args.pred).read_text(encoding="utf-8"))}
    result = score(gold, preds)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
