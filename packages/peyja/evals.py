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

CATEGORY_VOCAB = {
    "skincare",
    "makeup",
    "perfume",
    "haircare",
    "hygiene",
    "deo_fragrance",
    "other",
}
STOCK_VOCAB = {"in_stock", "out_of_stock", "on_request", "unknown"}
SPLIT_VOCAB = {"dev", "test"}  # قانون ۵: داده‌ی tuning (dev) ≠ داده‌ی ارزیابیِ نهایی (test)

# خانواده‌های معناییِ محصول — سپرِ «خطای انسانی»: همه‌ی اعضا باید یک دسته داشته باشند.
# (همین سازوکار، خطایِ تأییدِ «بادی اسپلش = بهداشتی» را پیش از تصحیحِ شما فریاد می‌زد.)
FAMILY_TERMS: dict[str, list[str]] = {
    "deo_fragrance": [
        "بادی اسپلش",
        "دئودورانت",
        "ضد تعریق",
        "ضدتعریق",
        "مام قارچی",
        "مام",
        "لوسیون بدن",
        "خوشبو کننده",
        "خوشبوکننده",
    ],
    "perfume": ["ادوپرفیوم", "ادکلن", "عطر"],
}
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

        # دسته: تک‌دسته = تساوی؛ چنددسته («اسکین‌کر، آرایش») = عضویت در فهرستِ طلایی
        gold_cats = sample.get("categories") or (
            [sample["category"]] if sample.get("category") else []
        )
        p_cat = pred.get("category") or None
        cat_match = (p_cat in gold_cats) if gold_cats else (p_cat is None)
        if cat_match:
            cat_ok += 1
        else:
            errors.append(
                {
                    "id": key,
                    "type": "category",
                    "gold": gold_cats or None,
                    "pred": pred.get("category"),
                }
            )

        g_attrs = set(
            map(str, sample.get("colors", []) + sample.get("sizes", []) + sample.get("shades", []))
        )
        p_attrs = set(
            map(str, pred.get("colors", []) + pred.get("sizes", []) + pred.get("shades", []))
        )
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


def _tokens(text: str | None) -> set[str]:
    """توکن‌های نرمال‌شده برای مقایسه‌ی نام‌ها (بدون حرفِ ربط و با نرمال‌سازی آ/ي/ك)."""
    from peyja.extract.catalog import fold

    stop = {"و", "با", "یا", "در", "برای", "از"}
    return {w for w in fold(text or "").split() if w and w not in stop}


def _product_match(g: dict[str, Any], p: dict[str, Any]) -> bool:
    """محصولِ طلایی «پیدا شد» اگر: برند یکی/هم‌ارز/دربرگیرنده باشد یا نام‌ها هم‌پوشانی داشته باشند."""
    from peyja.extract.catalog import canonical_brand, fold

    def _comp(s: Any) -> str:
        return "".join(fold(str(s or "")).split()).casefold()

    raw_name = g.get("name")
    g_names: list[Any] = list(raw_name) if isinstance(raw_name, list) else [raw_name]
    if g.get("brand") and p.get("brand"):
        gb = _comp(canonical_brand(str(g["brand"])) or g["brand"])
        pb = _comp(canonical_brand(str(p["brand"])) or p["brand"])
        if gb and pb and (gb == pb or gb in pb or pb in gb):
            return True
    pn_raw = p.get("product_name") or p.get("name_fa") or ""
    for one in g_names:
        gn, pn = _tokens(one), _tokens(pn_raw)
        if gn and pn and len(gn & pn) / len(gn) >= 0.5:
            return True
        # «ضد تعریق» ≈ «ضدتعریق» — مقایسه‌ی فشرده (بدونِ فاصله)
        gc, pc = _comp(one), _comp(pn_raw)
        if gc and pc and (gc in pc or pc in gc):
            return True
    return False


def score_products(items: list[dict[str, Any]], preds: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """نکته‌ی ۱ و ۲: بازیابیِ محصولاتِ چندمحصوله + دقتِ نام/برندِ تک‌محصوله‌ها.

    product_recall = سهمِ محصولاتِ طلایی که موتور پیدا کرد (گم‌شدن = گناهِ بزرگ).
    product_precision = سهمِ تکه‌هایِ پیش‌بینی که واقعاً محصول بودند (تکه‌ی زائد = آفتِ کاتالوگ).
    """
    multi = [s for s in items if s.get("products")]
    found = total = 0
    matched_pred = pred_total = 0
    for s in multi:
        p_prods = (preds.get(s["id"]) or {}).get("products") or []
        g_prods = s["products"]
        for g in g_prods:
            total += 1
            if any(_product_match(g, p) for p in p_prods):
                found += 1
        for p in p_prods:
            pred_total += 1
            if any(_product_match(g, p) for g in g_prods):
                matched_pred += 1

    named = [s for s in items if s.get("product_name")]
    name_hits = sum(
        1
        for s in named
        if _product_match({"name": s["product_name"], "brand": None}, preds.get(s["id"]) or {})
    )
    branded = [s for s in items if s.get("brand")]
    brand_hits = sum(
        1
        for s in branded
        if _product_match({"name": None, "brand": s["brand"]}, preds.get(s["id"]) or {})
    )
    return {
        "product_labeled": total,
        "product_recall": round(found / total, 4) if total else 0,
        "product_precision": round(matched_pred / pred_total, 4) if pred_total else 0,
        "name_labeled": len(named),
        "name_accuracy": round(name_hits / len(named), 4) if named else 0,
        "brand_labeled": len(branded),
        "brand_accuracy": round(brand_hits / len(branded), 4) if branded else 0,
    }


def consistency_report(samples: list[dict[str, Any]]) -> dict[str, Any]:
    """سپرِ «خطای انسانیِ» برچسب‌زنی — تضادهای متقاطع را بدونِ رحم پیدا می‌کند.

    سه سطح:
    1. تضادِ واژه‌ای: یک واژه‌ی محصول در دو نمونه دسته‌های متفاوت گرفته.
    2. تضادِ خانوادگی: اعضای یک خانواده (دئو/عطر …) دسته‌های متناقض دارند —
       دقیقاً همان چیزی که «بادی اسپلش=بهداشتی» را لو می‌داد.
    3. هشدار: واژه‌ی محصول هست ولی دسته‌ی null — «شاید محصولی هست که برچسب نخورده».
    """
    from peyja.extract.catalog import matched_terms

    term_cats: dict[str, set[str]] = {}
    fam_cats: dict[str, set[str]] = {}
    null_with_term: list[str] = []
    for s in samples:
        sid = str(s.get("id") or "?")
        text = (
            (s.get("caption") or "")
            + " "
            + " ".join(str(p.get("name") or "") for p in s.get("products") or [])
        )
        gold_cats = set(s.get("categories") or [])
        if s.get("category"):
            gold_cats.add(str(s["category"]))
        for p in s.get("products") or []:
            if p.get("category"):
                gold_cats.add(str(p["category"]))
        fam_hits = {fam: matched_terms(text, terms) for fam, terms in FAMILY_TERMS.items()}
        for fam, hits in fam_hits.items():
            if not hits:
                continue
            if gold_cats:
                fam_cats.setdefault(fam, set()).update(gold_cats)
            else:
                null_with_term.append(f"{sid}: واژه‌های {hits} از خانواده‌ی {fam} ولی دسته null")
            for h in hits:
                if gold_cats:
                    term_cats.setdefault(h, set()).update(gold_cats)

    term_conflicts = [
        f"تضادِ واژه‌ای «{t}»: دسته‌های {sorted(c)}" for t, c in term_cats.items() if len(c) > 1
    ]
    family_conflicts = [
        f"تضادِ خانواده‌ی {fam}: دسته‌های {sorted(c)}" for fam, c in fam_cats.items() if len(c) > 1
    ]
    return {
        "term_conflicts": sorted(term_conflicts),
        "family_conflicts": sorted(family_conflicts),
        "null_with_term": sorted(null_with_term),
        "families": {fam: sorted(c) for fam, c in fam_cats.items()},
    }


def calibration_report(
    gold: list[dict[str, Any]], preds: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    """کالیبراسیونِ اعتماد: آیا confidence بالا = خطای کمتر؟ (گزارشی — بدون گیت)."""
    bins: dict[str, list[bool]] = {"<۰.۴": [], "۰.۴–۰.۷": [], "≥۰.۷": []}
    for s in gold:
        gold_cats = s.get("categories") or ([s["category"]] if s.get("category") else [])
        if not gold_cats:
            continue
        p = preds.get(s["id"]) or {}
        c = float(p.get("confidence") or 0.0)
        key = "≥۰.۷" if c >= 0.7 else ("۰.۴–۰.۷" if c >= 0.4 else "<۰.۴")
        bins[key].append(p.get("category") in gold_cats)
    return {
        k: {"n": len(v), "accuracy": round(sum(v) / len(v), 3) if v else None}
        for k, v in bins.items()
    }


def lexicon_audit(samples: list[dict[str, Any]]) -> dict[str, Any]:
    """ضدِ بیش‌برازش (قانون ۵): کلیدهایِ مرده/تک‌نمونه‌ایِ واژگانِ دسته را افشا می‌کند."""
    from peyja.extract.pipeline import CATEGORY_MAP, _fold_fa, _key_hits

    counts: dict[str, int] = {}
    for key in CATEGORY_MAP:
        n = 0
        for s in samples:
            text = (s.get("caption") or "").replace("\u200c", " ")
            if _key_hits(_fold_fa(text), _fold_fa(key)):
                n += 1
        counts[key] = n
    return {
        "dead_keys": sorted(k for k, n in counts.items() if n == 0),
        "single_sample_keys": sorted(k for k, n in counts.items() if n == 1),
        "counts": counts,
    }


def score(gold: list[dict[str, Any]], preds: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """نمره‌ی کل + تفکیک‌ها: مصنوعی/واقعی و — قانون ۵ — dev/test.

    نمونه‌های `split=test` هرگز در tuning موتور دیده نمی‌شوند؛ نمره‌شان «عددِ رسمی»
    است و dev_only همان نمره‌ی خوش‌بینانه‌ی tuning است (گزارشِ صادقانه).
    """
    result = _score_subset(gold, preds)
    result.update(score_products(gold, preds))
    synthetic = [s for s in gold if s.get("synthetic")]
    real = [s for s in gold if not s.get("synthetic")]
    if synthetic and real:
        result["real_only"] = _score_subset(real, preds)
        result["synthetic_only"] = _score_subset(synthetic, preds)
    dev = [s for s in gold if s.get("split", "dev") == "dev"]
    test = [s for s in gold if s.get("split") == "test"]
    if test:
        result["dev_only"] = {**_score_subset(dev, preds), **score_products(dev, preds)}
        result["test_only"] = {**_score_subset(test, preds), **score_products(test, preds)}
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
        for c in s.get("categories") or []:
            if c not in CATEGORY_VOCAB:
                issues.append(f"{sid}: دسته‌ی چندگانه‌ی ناشناخته «{c}»")
        for p in s.get("products") or []:
            pc = p.get("category")
            if pc is not None and pc not in CATEGORY_VOCAB:
                issues.append(f"{sid}: دسته‌ی محصولِ چندمحصوله ناشناخته «{pc}»")
        st = s.get("stock_state")
        if st is not None and st not in STOCK_VOCAB:
            issues.append(f"{sid}: وضعیت موجودی ناشناخته «{st}»")
        if s.get("split", "dev") not in SPLIT_VOCAB:
            issues.append(f"{sid}: split ناشناخته «{s.get('split')}»")
    # سپرِ خطای انسانی: تضادهای برچسبی باید پیش از نمره‌دهی لو بروند (قانون ۵)
    rep = consistency_report(samples)
    issues.extend(rep["term_conflicts"])
    issues.extend(rep["family_conflicts"])
    return issues


def render_verification_sheet(samples: list[dict[str, Any]]) -> str:
    """برگه‌ی تأیید انسانیِ متنی (Markdown) — کپشن کامل، برای ثبت در مخزن."""
    lines = [
        "# برگه‌ی تأیید انسانی gold-100",
        "",
        "> هر ردیف را با کپشن اصلی مقایسه کنید؛ اگر برچسب درست است ☑ بزنید،",
        "> اگر غلط است همان‌جا اصلاح کنید (طبق قاعده: «هر ایرانی چطور می‌خواند»).",
        "> نسخه‌ی تعاملی (تیک‌خور + خروجی اصلاحات): `verification.html` را در مرورگر باز کنید.",
        "",
        "> ستونِ **موجودی تأییدنشده** است (نکته‌ی ۳ بررس): in_stock=«موجود» · "
        "on_request=«استعلام در دایرکت/کامنت» · unknown=«نامشخص».",
        "",
        "| # | کپشن (کامل) | نام محصول | قیمت (تومان) | دسته | برند | رنگ/سایز/شید | موجودی | تأیید |",
        "|---|------------|----------|--------------|------|------|------------|--------|------|",
    ]
    for s in samples:
        cap = (s.get("caption") or "").replace("\n", " ").replace("|", "/")
        attrs = (
            "،".join(
                list(s.get("colors", [])) + list(s.get("sizes", [])) + list(s.get("shades", []))
            )
            or "—"
        )
        syn = " *(synthetic)*" if s.get("synthetic") else ""
        lines.append(
            f"| {s['id']}{syn} | {cap} | {s.get('product_name') or '—'} | "
            f"{s.get('price_toman') or '—'} | "
            f"{s.get('category') or '/'.join(s.get('categories', [])) or '—'} | "
            f"{s.get('brand') or '—'} | {attrs} | "
            f"{s.get('stock_state') or '—'} | ☐ |"
        )
    lines.append("")
    return "\n".join(lines)


def render_verification_html(samples: list[dict[str, Any]]) -> str:
    """برگه‌ی تأییدِ تعاملی (HTML) — تیک‌خور، کپشن کامل، دکمه‌ی «خروجیِ اصلاحات».

    مشکلات نسخه‌ی قبلی اینجا حل شد: کپشن خلاصه نمی‌شود، ☐ واقعاً تیک می‌خورد،
    اصلاحات به‌صورت JSON قابل کپی و بازگردانی است.
    """
    import html
    import json as _json

    rows = []
    for s in samples:
        syn = " 🧪synthetic" if s.get("synthetic") else ""
        cap = html.escape(s.get("caption") or "")
        vals: dict[str, str] = {
            "price_toman": str(s.get("price_toman") if s.get("price_toman") is not None else ""),
            "category": str(s.get("category") or ""),
            "brand": str(s.get("brand") or ""),
            "shades": "،".join(map(str, s.get("shades", []))),
            "colors": "،".join(map(str, s.get("colors", []))),
            "sizes": "،".join(map(str, s.get("sizes", []))),
            "stock_state": str(s.get("stock_state") or "unknown"),
        }
        original = _json.dumps(vals, ensure_ascii=False)
        fields = (
            f'<input data-f="price_toman" value="{vals["price_toman"]}" data-orig="{vals["price_toman"]}" style="width:9rem" placeholder="تومان">'
            f'<input data-f="category" value="{vals["category"]}" data-orig="{vals["category"]}" style="width:6.5rem" placeholder="دسته">'
            f'<input data-f="brand" value="{html.escape(vals["brand"])}" data-orig="{html.escape(vals["brand"])}" style="width:6.5rem" placeholder="برند">'
            f'<input data-f="shades" value="{html.escape(vals["shades"])}" data-orig="{html.escape(vals["shades"])}" style="width:5.5rem" placeholder="شیدها">'
            f'<input data-f="colors" value="{html.escape(vals["colors"])}" data-orig="{html.escape(vals["colors"])}" style="width:7rem" placeholder="رنگ‌ها">'
            f'<input data-f="sizes" value="{html.escape(vals["sizes"])}" data-orig="{html.escape(vals["sizes"])}" style="width:5.5rem" placeholder="سایز/حجم">'
            f'<select data-f="stock_state" data-orig="{vals["stock_state"]}">'
            + "".join(
                f"<option{' selected' if v == vals['stock_state'] else ''}>{v}</option>"
                for v in ("in_stock", "out_of_stock", "on_request", "unknown")
            )
            + "</select>"
        )
        rows.append(
            f'<div class="row" data-id="{s["id"]}" data-original=\'{html.escape(original)}\'>'
            f'<label class="ok"><input type="checkbox" class="verify"> تأیید</label>'
            f'<div class="meta"><b>{s["id"]}{syn}</b><div class="cap">{cap}</div></div>'
            f'<div class="fields">{fields}</div></div>'
        )
    body = "\n".join(rows)
    template = """<!DOCTYPE html>
<html lang="fa" dir="rtl"><head><meta charset="utf-8">
<title>برگه‌ی تأیید gold-100</title>
<style>
 body{font-family:Vazirmatn,Tahoma,sans-serif;background:#111827;color:#e5e7eb;margin:0;padding:1rem}
 h1{font-size:1.2rem} .bar{position:sticky;top:0;background:#111827;padding:.6rem 0;z-index:5}
 .row{display:flex;gap:.8rem;align-items:flex-start;background:#1f2937;border-radius:.6rem;padding:.7rem;margin:.5rem 0}
 .ok{display:flex;align-items:center;gap:.35rem;white-space:nowrap;padding-top:.3rem}
 .meta{flex:1;min-width:0} .cap{white-space:pre-wrap;font-size:.82rem;color:#9ca3af;max-height:6.5rem;overflow:auto}
 .fields{display:flex;flex-wrap:wrap;gap:.35rem}
 input,select{background:#374151;color:#f9fafb;border:.5px solid #4b5632;border-radius:.35rem;padding:.3rem .45rem;font-size:.82rem}
 button{background:#2563eb;color:#fff;border:0;border-radius:.5rem;padding:.55rem 1rem;font-size:.95rem;cursor:pointer;margin-left:.5rem}
 textarea{width:97%;height:10rem;background:#0b1220;color:#a7f3d0;border-radius:.5rem;padding:.6rem;font-size:.8rem}
 .row.done{opacity:.55} .tag{font-size:.75rem;background:#065f46;border-radius:.4rem;padding:.15rem .5rem}
</style></head><body>
<h1>☑ برگه‌ی تأیید انسانی gold-100 — «یک نفر چک کند»</h1>
<div class="bar">
 <span class="tag" id="progress">0 / 0 تأیید شده</span>
 <button onclick="verifyAll()">تأیید همه</button>
 <button onclick="makeReport()">ساختِ خروجیِ اصلاحات</button>
 <button onclick="document.getElementById('out').select();document.execCommand('copy')">کپیِ خروجی</button>
</div>
<p style="font-size:.85rem;color:#9ca3af">کپشن‌ها کامل‌اند. برای هر ردیف: اگر درست است ☑ بزنید؛
اگر غلط است فیلدها را همان‌جا اصلاح کنید (و ☐ را باز بگذارید). در پایان «ساختِ خروجیِ اصلاحات»
را بزنید و متن JSON را برای پیجا بفرستید.<br>
راهنمای موجودی (این ستون تأییدنشده است — نکته‌ی ۳): <b>in_stock</b>=«موجود» ·
<b>on_request</b>=«قیمت/موجودی با استعلام در دایرکت/کامنت» · <b>unknown</b>=«نامشخص».</p>
__ROWS__
<h2 style="font-size:1rem">خروجیِ اصلاحات (JSON)</h2>
<textarea id="out" readonly></textarea>
<script>
function refresh(){
 let all=document.querySelectorAll('.row'),d=0;
 all.forEach(r=>{if(r.querySelector('.verify').checked){r.classList.add('done');d++}else r.classList.remove('done')});
 document.getElementById('progress').textContent=d+' / '+all.length+' تأیید شده';
}
document.addEventListener('change',refresh);
function verifyAll(){document.querySelectorAll('.verify').forEach(c=>c.checked=true);refresh()}
function makeReport(){
 const report=[];
 document.querySelectorAll('.row').forEach(r=>{
  const orig=JSON.parse(r.dataset.original),cur={};
  r.querySelectorAll('[data-f]').forEach(el=>cur[el.dataset.f]=el.value);
  const changed={};
  for(const k in orig){if(String(cur[k])!==String(orig[k]))changed[k]={old:orig[k],new:cur[k]}}
  const checked=r.querySelector('.verify').checked;
  if(!checked||Object.keys(changed).length>0){
   report.push({id:r.dataset.id,verified:checked,changes:changed});
  }
 });
 document.getElementById('out').value=JSON.stringify(report,null,1);
}
refresh();
</script></body></html>"""
    return template.replace("__ROWS__", body)
