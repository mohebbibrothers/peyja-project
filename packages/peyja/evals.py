"""ارزیابی extraction روی gold-100 — «عدد سرنوشت» (قلب Phase 1).

منطق نمره‌دهی + اعتبارسنجی مجموعه‌داده‌ی طلایی + برگه‌ی تأیید انسانی.
CLIها (evals/score.py ، peyja gold-score/gold-sheet) فقط لایه‌ی نازک‌اند؛
منطق واقعی اینجاست تا تست‌پذیر و قابل‌استفاده‌ی مجدد باشد.

تصمیمات طراحی نمره‌دهی (و چرایی‌شان):
- قیمت: «تساوی دقیق» + «خطای ≤۲٪» — ۲٪ تحملِ تفاوت‌های واقعی بازار (تخفیف لحظه‌ای/سایز).
  null==null قبول است: «قیمت ندارد» درست تشخیص دادن هم مهارت است (ضدِ حدس‌زدن).
- دسته: تطبیق دقیق رشته — واژگان بسته است پس exact-match منصفانه است.
- ویژگی (رنگ+سایز): همپوشانی ≥۵۰٪ = قبول (کپشن‌های چندویژگی). نمونه‌های بدون
  ویژگیِ طلایی در مخرج «دقت ویژگی» نمی‌آیند (فقط coverage جداگانه گزارش می‌شود).
- گیت‌های Phase 1: قیمت (≤۲٪) ≥ ۸۵٪ | دسته ≥ ۷۵٪ | ویژگی ≥ ۷۵٪.
"""

from __future__ import annotations

from typing import Any

CATEGORY_VOCAB = {"skincare", "makeup", "perfume", "haircare", "hygiene", "other"}
STOCK_VOCAB = {"in_stock", "out_of_stock", "on_request", "unknown"}
GATE_PRICE = 0.85
GATE_CATEGORY = 0.75
GATE_ATTRIBUTE = 0.75


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


def _score_subset(items: list[dict[str, Any]], preds: dict[str, dict[str, Any]]) -> dict[str, Any]:
    n = len(items)
    price_em = price_tol = cat_ok = attr_ok = 0
    n_attr = 0
    errors: list[dict[str, Any]] = []

    for sample in items:
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
        if g_attrs:
            n_attr += 1
            if len(g_attrs & p_attrs) / len(g_attrs) >= 0.5:
                attr_ok += 1
            else:
                errors.append(
                    {
                        "id": key,
                        "type": "attribute",
                        "gold": sorted(g_attrs),
                        "pred": sorted(p_attrs),
                    }
                )

    return {
        "n": n,
        "price_exact_match": round(price_em / n, 4) if n else 0,
        "price_within_2pct": round(price_tol / n, 4) if n else 0,
        "category_accuracy": round(cat_ok / n, 4) if n else 0,
        "attribute_accuracy": round(attr_ok / n_attr, 4) if n_attr else 0,
        "attribute_labeled": n_attr,
        "gate_price_pass": (price_tol / n >= GATE_PRICE) if n else False,
        "gate_category_pass": (cat_ok / n >= GATE_CATEGORY) if n else False,
        "gate_attribute_pass": (attr_ok / n_attr >= GATE_ATTRIBUTE) if n_attr else True,
        "errors": errors[:50],
    }


def score(gold: list[dict[str, Any]], preds: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """نمره‌ی کل + تفکیک مصنوعی/واقعی (اگر نمونه‌های synthetic علامت‌خورده باشند)."""
    result = _score_subset(gold, preds)
    synthetic = [s for s in gold if s.get("synthetic")]
    real = [s for s in gold if not s.get("synthetic")]
    if synthetic and real:
        result["real_only"] = _score_subset(real, preds)
        result["synthetic_only"] = _score_subset(synthetic, preds)
    return result


def validate_gold(samples: list[dict[str, Any]]) -> list[str]:
    """اعتبارسنجی خودِ مجموعه‌ی طلایی — پیش از هر نمره‌ای باید سالم باشد."""
    issues: list[str] = []
    seen: set[str] = set()
    for s in samples:
        sid = str(s.get("id") or "?")
        if not s.get("id"):
            issues.append("نمونه‌ای بدون id وجود دارد")
            continue
        if sid in seen:
            issues.append(f"{sid}: id تکراری")
        seen.add(sid)
        if not (s.get("caption") or "").strip():
            issues.append(f"{sid}: کپشن خالی")
        for f in ("price_toman", "old_price_toman"):
            v = s.get(f)
            if v is not None and (not isinstance(v, int) or not (1_000 <= v <= 5_000_000_000)):
                issues.append(f"{sid}: {f}={v} خارج از بازه‌ی معقول")
        if (
            s.get("price_toman") is not None
            and s.get("old_price_toman") is not None
            and s["old_price_toman"] < s["price_toman"]
        ):
            issues.append(f"{sid}: قیمت قدیم کمتر از قیمت فعلی است")
        cat = s.get("category")
        if cat is not None and cat not in CATEGORY_VOCAB:
            issues.append(f"{sid}: دسته‌ی ناشناخته «{cat}»")
        st = s.get("stock_state")
        if st is not None and st not in STOCK_VOCAB:
            issues.append(f"{sid}: وضعیت موجودی ناشناخته «{st}»")
    return issues


def render_verification_sheet(samples: list[dict[str, Any]]) -> str:
    """برگه‌ی تأیید انسانی — «یک نفر چک کند» (قانون gold-100)."""
    lines = [
        "# برگه‌ی تأیید انسانی gold-100",
        "",
        "> هر ردیف را با کپشن اصلی مقایسه کنید؛ اگر برچسب درست است ☑ بزنید،",
        "> اگر غلط است همان‌جا اصلاح کنید (طبق قاعده: «هر ایرانی چطور می‌خواند»).",
        "> نمونه‌های `synthetic` فقط پوشش فرم‌های سخت‌اند (قاعده‌ی بازی را نگاه نکنید!).",
        "",
        "| # | کپشن (خلاصه) | قیمت (تومان) | دسته | برند | رنگ/سایز | موجودی | تأیید |",
        "|---|--------------|--------------|------|------|----------|--------|------|",
    ]
    for s in samples:
        cap = (s.get("caption") or "").replace("\n", " ").replace("|", "/")
        if len(cap) > 90:
            cap = cap[:90] + "…"
        attrs = "،".join(list(s.get("colors", [])) + list(s.get("sizes", []))) or "—"
        syn = " *(synthetic)*" if s.get("synthetic") else ""
        lines.append(
            f"| {s['id']}{syn} | {cap} | {s.get('price_toman') or '—'} | "
            f"{s.get('category') or '—'} | {s.get('brand') or '—'} | {attrs} | "
            f"{s.get('stock_state') or '—'} | ☐ |"
        )
    lines.append("")
    return "\n".join(lines)
