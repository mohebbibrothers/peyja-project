"""بنچ‌مارکِ مغزِ هوشمند (VLM) روی gold — «منتقدِ بی‌رحم» (قانون ۵).

مسیر: هر نمونه → پرامپتِ ۸بخشیِ استخراج → JSON ساخت‌یافته → SafeProductExtraction →
نگاشت به قالبِ نمره‌دهی → `evals.score` — کنارِ خروجیِ موتورِ آفلاین (مقایسه‌ای).

ویژگی‌ها:
- **ازسرگیری‌پذیر:** کشِ `out/vlm-predictions.json` — هر نمونه یک‌بار مدل را صدا می‌زند
- **پیش‌آزمونِ سلامت:** بدونِ پاسخِ /models = پیامِ روشن، نه crash
- **شکستِ تک‌نمونه ≠ شکستِ بنچ‌مارک:** خطای مدل برای آن نمونه ثبت و ادامه
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from peyja.extract.pipeline import VLM_RETRY_PROMPT, VLM_SYSTEM_PROMPT, _defensive_parse
from peyja.extract.schema import SafeProductExtraction

_SCHEMA_JSON = json.dumps(SafeProductExtraction.model_json_schema(), ensure_ascii=False)


def _to_pred(sample_id: str, r: SafeProductExtraction) -> dict[str, Any]:
    """نگاشتِ خروجیِ مغز به قالبِ نمره‌دهیِ `evals.score`."""
    return {
        "id": sample_id,
        "price_toman": r.price_toman,
        "old_price_toman": r.old_price_toman,
        "category": r.category,
        "product_name": r.product_name or r.title_fa,
        "brand": r.brand,
        "colors": r.colors,
        "shades": r.shades,
        "sizes": r.sizes,
        "stock_state": r.stock_state,
        "confidence": r.confidence,
        "method": "vlm",
        "products": [
            {
                "product_name": p.name_fa,
                "brand": p.brand,
                "category": p.category,
                "price_toman": p.price_toman,
                "colors": p.colors,
                "shades": p.shades,
                "sizes": p.sizes,
                "stock_state": p.stock_state,
            }
            for p in r.products
        ],
    }


async def vlm_extract(provider: Any, caption: str) -> SafeProductExtraction | None:
    """استخراجِ یک کپشن با مغز — همان منطقِ retryِ pipeline (مستقل برای بنچ‌مارک)."""
    user_prompt = f"اسکیمای JSON:\n{_SCHEMA_JSON}\n\nکپشن:\n{caption or '(بدون کپشن)'}"
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": VLM_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]
    for _attempt in range(3):
        try:
            text = await provider.chat(messages, temperature=0)
        except Exception:
            return None
        try:
            result = SafeProductExtraction.model_validate(_defensive_parse(text))
        except Exception as e:
            messages.append(
                {"role": "user", "content": VLM_RETRY_PROMPT.format(reason=str(e)[:500])}
            )
            continue
        return result if result.extraction_successful else None
    return None


async def run_vlm_benchmark(
    samples: list[dict[str, Any]],
    out_path: Path,
    provider: Any,
    fresh: bool = False,
    limit: int = 0,
) -> dict[str, Any]:
    """بنچ‌مارکِ کامل — نتیجه + مقایسه با موتورِ آفلاین (اگر predictions موجود باشد)."""
    from peyja.evals import score

    health = await provider.health() if hasattr(provider, "health") else {"ok": True}
    if not health.get("ok", False):
        return {"ok": False, "error": f"مغزِ هوشمند در دسترس نیست: {health.get('error')}"}

    cache: dict[str, dict[str, Any]] = {}
    if out_path.exists() and not fresh:
        try:
            loaded = json.loads(out_path.read_text(encoding="utf-8"))
            cache = {p["id"]: p for p in loaded if isinstance(p, dict) and p.get("id")}
        except (json.JSONDecodeError, OSError):
            cache = {}

    todo = samples[:limit] if limit > 0 else samples
    done = failed = 0
    for s in todo:
        sid = str(s.get("id"))
        if sid in cache:
            done += 1
            continue
        r = await vlm_extract(provider, s.get("caption") or "")
        if r is None:
            failed += 1
            cache[sid] = {"id": sid, "method": "vlm", "failed": True}
            continue
        cache[sid] = _to_pred(sid, r)
        done += 1
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps(list(cache.values()), ensure_ascii=False, indent=2), encoding="utf-8"
        )

    preds = {pid: p for pid, p in cache.items() if not p.get("failed")}
    scored_ids = [s for s in todo if str(s["id"]) in preds]
    result = score(scored_ids, preds) if scored_ids else {}

    comparison: dict[str, Any] = {}
    offline_path = Path("out/predictions.json")
    if offline_path.exists():
        try:
            offline_preds = {p["id"]: p for p in json.loads(offline_path.read_text("utf-8"))}
            comparison["offline"] = score(samples, offline_preds)
        except (json.JSONDecodeError, OSError, KeyError):
            comparison = {}

    report = {
        "ok": True,
        "n_scored": len(scored_ids),
        "n_failed": failed,
        "health": health,
        "vlm_score": result,
        "comparison": comparison,
        "out": str(out_path),
    }
    _write_report_md(report)
    return report


def _write_report_md(report: dict[str, Any]) -> None:
    v = report.get("vlm_score") or {}
    o = (report.get("comparison") or {}).get("offline") or {}
    lines = [
        "# گزارشِ بنچ‌مارکِ مغزِ هوشمند (VLM — Qwen محلی)",
        "",
        f"- نمونه‌های نمره‌خورده: **{report.get('n_scored')}** · شکست‌های مدل: {report.get('n_failed')}",
        "",
        "| معیار | موتورِ آفلاین (regex) | مغزِ VLM |",
        "|---|---|---|",
    ]
    for key, label in [
        ("price_within_2pct", "قیمت (±۲٪)"),
        ("category_accuracy", "دسته"),
        ("attribute_accuracy", "ویژگی"),
        ("product_recall", "بازیابیِ محصولات"),
        ("product_precision", "دقتِ تکه‌ها"),
        ("name_accuracy", "نامِ محصول"),
        ("brand_accuracy", "برند"),
    ]:
        ov = o.get(key)
        vv = v.get(key)
        fmt = lambda x: "—" if x is None else f"{x:.0%}"  # noqa: E731
        lines.append(f"| {label} | {fmt(ov)} | {fmt(vv)} |")
    lines += [
        "",
        "> نکته‌ی قانون ۵: این‌ها روی **dev** (بچ ۱)‌اند؛ عددِ رسمی با holdout (بچ ۲) می‌آید.",
        "",
    ]
    rp = Path("out/vlm-report.md")
    rp.parent.mkdir(parents=True, exist_ok=True)
    rp.write_text("\n".join(lines), encoding="utf-8")
