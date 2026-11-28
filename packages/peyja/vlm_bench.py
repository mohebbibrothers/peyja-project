"""بنچ‌مارکِ مغزِ هوشمند (VLM) روی gold — «منتقدِ بی‌رحم» (قانون ۵).

مسیر: هر نمونه → پرامپتِ استخراج → JSON ساخت‌یافته → SafeProductExtraction →
نگاشت به قالبِ نمره‌دهی → `evals.score` — کنارِ خروجیِ موتورِ آفلاین روی دقیقاً همان نمونه‌ها.

ویژگی‌ها (قانون ۱ و ۵):
- **ضدِ خطای بقاء (No survivorship bias):** همه‌ی نمونه‌های هدف در مخرجِ نمره می‌آیند؛
  نمونه‌ی شکست‌خورده با مقدارِ نامعتبر جریمه می‌شود تا روی برچسب‌های `null` امتیازِ مفت نگیرد.
- **امتناعِ آگاهانه (`extraction_successful=false`) ≠ شکست:** اگر مدل با JSONِ سالم
  گفت «پست محصولی ندارد»، به‌عنوانِ abstention با `category=None` نمره‌دهی می‌شود.
- **ازسرگیری‌پذیر:** کشِ `out/vlm-predictions.json` پس از هر نمونه ذخیره می‌شود؛
  نمونه‌های `failed` در اجرای بعدی خودکار retry می‌شوند.
- **تله‌متری و گزارشِ منتقدِ بی‌رحم:** p50/p95 تأخیر، مصرف توکن، نرخ `<think>`،
  و فهرستِ دقیقِ پیروزی‌ها/شکست‌های VLM در برابر موتور آفلاین در `vlm-report.md`.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from peyja.extract.pipeline import (
    VLM_RETRY_PROMPT,
    VLM_SYSTEM_PROMPT,
    ExtractionPipeline,
    _defensive_parse,
)
from peyja.extract.schema import SafeProductExtraction

_SCHEMA_JSON = json.dumps(SafeProductExtraction.model_json_schema(), ensure_ascii=False)
_FAILED_SENTINEL_CAT = "__VLM_FAILED__"
_FAILED_SENTINEL_PRICE = -1


@dataclass
class VlmOutcome:
    """نتیجه‌ی استخراج یک نمونه با تله‌متری."""

    status: str  # "ok" | "abstained" | "failed"
    extraction: SafeProductExtraction | None = None
    error: str | None = None
    latency_ms: float = 0.0
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    attempts: int = 0
    had_thinking: bool = False


def _to_pred(sample_id: str, r: SafeProductExtraction) -> dict[str, Any]:
    """نگاشتِ خروجیِ مغز به قالبِ نمره‌دهیِ `evals.score`."""
    if not r.extraction_successful:
        return {
            "id": sample_id,
            "price_toman": None,
            "old_price_toman": None,
            "category": None,
            "product_name": None,
            "brand": None,
            "colors": [],
            "shades": [],
            "sizes": [],
            "stock_state": "unknown",
            "confidence": 0.0,
            "method": "vlm",
            "abstained": True,
            "failure_reason": r.failure_reason,
            "products": [],
        }

    # نگاشت چندمحصوله ← اگر سطح بالا خالی ماند، از products استنتاج کن تا نمره‌ی دسته/برند نسوزد
    derived_cat = r.category
    if derived_cat is None and r.products:
        cats = [p.category for p in r.products if p.category]
        if cats:
            derived_cat = Counter(cats).most_common(1)[0][0]
    derived_brand = r.brand
    if derived_brand is None and r.products:
        derived_brand = next((p.brand for p in r.products if p.brand), None)
    first_prod_name = next((p.name_fa for p in r.products if p.name_fa), None)
    derived_name = r.product_name or (first_prod_name if r.products else None) or r.title_fa

    return {
        "id": sample_id,
        "price_toman": r.price_toman,
        "old_price_toman": r.old_price_toman,
        "category": derived_cat,
        "product_name": derived_name,
        "brand": derived_brand,
        "colors": r.colors,
        "shades": r.shades,
        "sizes": r.sizes,
        "stock_state": r.stock_state,
        "confidence": r.confidence,
        "method": "vlm",
        "abstained": False,
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


async def vlm_extract_detailed(provider: Any, caption: str) -> VlmOutcome:
    """استخراج یک کپشن با ثبتِ دقیق وضعیت، زمان، توکن و دلیل شکست."""
    user_prompt = f"اسکیمای JSON:\n{_SCHEMA_JSON}\n\nکپشن:\n{caption or '(بدون کپشن)'}"
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": VLM_SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]
    t0 = time.perf_counter()
    prompt_toks = 0
    comp_toks = 0
    has_toks = False
    had_thinking = False
    last_err = ""

    for attempt in range(1, 4):
        try:
            if hasattr(provider, "chat_detailed"):
                res = await provider.chat_detailed(messages, temperature=0)
                text = res.content
                had_thinking = had_thinking or res.had_thinking
                if res.prompt_tokens is not None:
                    prompt_toks += res.prompt_tokens
                    has_toks = True
                if res.completion_tokens is not None:
                    comp_toks += res.completion_tokens
                    has_toks = True
            else:
                text = await provider.chat(messages, temperature=0)
                if "<think>" in text.lower():
                    had_thinking = True
        except Exception as e:
            return VlmOutcome(
                status="failed",
                error=f"{type(e).__name__}: {e}",
                latency_ms=round((time.perf_counter() - t0) * 1000, 1),
                prompt_tokens=prompt_toks if has_toks else None,
                completion_tokens=comp_toks if has_toks else None,
                attempts=attempt,
                had_thinking=had_thinking,
            )
        try:
            parsed = _defensive_parse(text)
            result = SafeProductExtraction.model_validate(parsed)
        except Exception as e:
            last_err = f"{type(e).__name__}: {str(e)[:280]}"
            messages.append(
                {"role": "user", "content": VLM_RETRY_PROMPT.format(reason=str(e)[:500])}
            )
            continue

        elapsed = round((time.perf_counter() - t0) * 1000, 1)
        status = "ok" if result.extraction_successful else "abstained"
        return VlmOutcome(
            status=status,
            extraction=result,
            latency_ms=elapsed,
            prompt_tokens=prompt_toks if has_toks else None,
            completion_tokens=comp_toks if has_toks else None,
            attempts=attempt,
            had_thinking=had_thinking,
        )

    return VlmOutcome(
        status="failed",
        error=last_err or "خروجی پس از ۳ تلاش معتبر نشد",
        latency_ms=round((time.perf_counter() - t0) * 1000, 1),
        prompt_tokens=prompt_toks if has_toks else None,
        completion_tokens=comp_toks if has_toks else None,
        attempts=3,
        had_thinking=had_thinking,
    )


async def vlm_extract(provider: Any, caption: str) -> SafeProductExtraction | None:
    """استخراجِ یک کپشن با مغز — سازگار با رابط قبلی."""
    out = await vlm_extract_detailed(provider, caption)
    if out.status == "ok":
        return out.extraction
    return None


def _compute_offline_preds(samples: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    preds: dict[str, dict[str, Any]] = {}
    for s in samples:
        sid = str(s["id"])
        cap = s.get("caption") or ""
        r = ExtractionPipeline.extract_signals_fa(cap)
        r["products"] = ExtractionPipeline.extract_multi_fa(cap)
        r["is_multi_product"] = len(r["products"]) > 1 or ExtractionPipeline.is_multi_caption(cap)
        preds[sid] = {"id": sid, **r}
    return preds


def _percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    s = sorted(values)
    idx = min(len(s) - 1, max(0, round((len(s) - 1) * p)))
    return round(s[idx], 1)


def _cat_ok(sample: dict[str, Any], pred: dict[str, Any]) -> bool:
    gold_cats = sample.get("categories") or ([sample["category"]] if sample.get("category") else [])
    p_cat = pred.get("category") or None
    return (p_cat in gold_cats) if gold_cats else (p_cat is None)


def _head_to_head(
    todo: list[dict[str, Any]],
    vlm_preds: dict[str, dict[str, Any]],
    offline_preds: dict[str, dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """مقایسه‌ی نقطه‌به‌نقطه: کجا VLM برد، کجا باخت، کجا هر دو خطا دادند."""
    from peyja.evals import price_within_tolerance

    vlm_wins: list[dict[str, Any]] = []
    offline_wins: list[dict[str, Any]] = []
    both_wrong: list[dict[str, Any]] = []

    for s in todo:
        sid = str(s["id"])
        vp = vlm_preds.get(sid, {})
        op = offline_preds.get(sid, {})
        v_ok = _cat_ok(s, vp) and price_within_tolerance(
            s.get("price_toman"), vp.get("price_toman")
        )
        o_ok = _cat_ok(s, op) and price_within_tolerance(
            s.get("price_toman"), op.get("price_toman")
        )
        if v_ok == o_ok:
            if not v_ok:
                both_wrong.append(
                    {
                        "id": sid,
                        "gold_cat": s.get("categories") or s.get("category"),
                        "gold_price": s.get("price_toman"),
                        "offline_cat": op.get("category"),
                        "offline_price": op.get("price_toman"),
                        "vlm_cat": vp.get("category"),
                        "vlm_price": vp.get("price_toman"),
                        "caption": (s.get("caption") or "").replace("\n", " ")[:90],
                    }
                )
            continue
        row = {
            "id": sid,
            "gold_cat": s.get("categories") or s.get("category"),
            "gold_price": s.get("price_toman"),
            "offline_cat": op.get("category"),
            "offline_price": op.get("price_toman"),
            "vlm_cat": vp.get("category"),
            "vlm_price": vp.get("price_toman"),
            "caption": (s.get("caption") or "").replace("\n", " ")[:90],
        }
        if v_ok:
            vlm_wins.append(row)
        else:
            offline_wins.append(row)
    return {"vlm_wins": vlm_wins, "offline_wins": offline_wins, "both_wrong": both_wrong}


async def run_vlm_benchmark(
    samples: list[dict[str, Any]],
    out_path: Path,
    provider: Any,
    fresh: bool = False,
    limit: int = 0,
    concurrency: int = 1,
    report_path: Path | None = None,
    on_progress: Callable[[int, int, str, dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """بنچ‌مارکِ کامل — نتیجه + تله‌متری + مقایسه‌ی هم‌ارز با موتور آفلاین."""
    from peyja.evals import score

    health = await provider.health() if hasattr(provider, "health") else {"ok": True}
    if not health.get("ok", False):
        return {"ok": False, "error": f"مغزِ هوشمند در دسترس نیست: {health.get('error')}"}

    cache: dict[str, dict[str, Any]] = {}
    if out_path.exists() and not fresh:
        try:
            loaded = json.loads(out_path.read_text(encoding="utf-8"))
            for p in loaded:
                if isinstance(p, dict) and p.get("id") and not p.get("failed"):
                    cache[str(p["id"])] = p
        except (json.JSONDecodeError, OSError):
            cache = {}

    todo = samples[:limit] if limit > 0 else samples
    total = len(todo)
    t_bench = time.perf_counter()
    done_count = sum(1 for s in todo if str(s.get("id")) in cache)
    sem = asyncio.Semaphore(max(1, concurrency))
    lock = asyncio.Lock()

    def _flush_cache() -> None:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(
            json.dumps(list(cache.values()), ensure_ascii=False, indent=2), encoding="utf-8"
        )

    async def _run_one(sample: dict[str, Any]) -> None:
        nonlocal done_count
        sid = str(sample.get("id"))
        if sid in cache:
            return
        async with sem:
            outcome = await vlm_extract_detailed(provider, sample.get("caption") or "")
        if outcome.status == "failed" or outcome.extraction is None:
            entry: dict[str, Any] = {
                "id": sid,
                "method": "vlm",
                "failed": True,
                "error": outcome.error,
                "latency_ms": outcome.latency_ms,
                "attempts": outcome.attempts,
                "had_thinking": outcome.had_thinking,
            }
        else:
            entry = _to_pred(sid, outcome.extraction)
            entry["latency_ms"] = outcome.latency_ms
            entry["attempts"] = outcome.attempts
            entry["had_thinking"] = outcome.had_thinking
            if outcome.prompt_tokens is not None:
                entry["prompt_tokens"] = outcome.prompt_tokens
            if outcome.completion_tokens is not None:
                entry["completion_tokens"] = outcome.completion_tokens

        async with lock:
            cache[sid] = entry
            done_count += 1
            _flush_cache()
            if on_progress is not None:
                on_progress(done_count, total, sid, entry)

    await asyncio.gather(*(_run_one(s) for s in todo))

    todo_entries = [cache[str(s["id"])] for s in todo if str(s["id"]) in cache]
    failed_entries = [e for e in todo_entries if e.get("failed")]
    abstained_entries = [e for e in todo_entries if not e.get("failed") and e.get("abstained")]
    valid_entries = [e for e in todo_entries if not e.get("failed")]

    # ضدِ survivorship bias: برای نمونه‌های failed از sentinel خطادار استفاده می‌کنیم
    eval_preds: dict[str, dict[str, Any]] = {}
    for s in todo:
        sid = str(s["id"])
        e = cache.get(sid, {"failed": True})
        if e.get("failed"):
            eval_preds[sid] = {
                "id": sid,
                "category": _FAILED_SENTINEL_CAT,
                "price_toman": _FAILED_SENTINEL_PRICE,
                "products": [],
            }
        else:
            eval_preds[sid] = e

    full_score = score(todo, eval_preds) if todo else {}
    valid_preds = {str(e["id"]): e for e in valid_entries}
    valid_samples = [s for s in todo if str(s["id"]) in valid_preds]
    valid_score = score(valid_samples, valid_preds) if valid_samples and failed_entries else None

    offline_preds = _compute_offline_preds(todo)
    offline_score = score(todo, offline_preds) if todo else {}

    latencies = [float(e["latency_ms"]) for e in todo_entries if e.get("latency_ms") is not None]
    prompt_toks = sum(int(e.get("prompt_tokens") or 0) for e in todo_entries)
    comp_toks = sum(int(e.get("completion_tokens") or 0) for e in todo_entries)
    think_calls = sum(1 for e in todo_entries if e.get("had_thinking"))

    telemetry = {
        "total_s": round(time.perf_counter() - t_bench, 2),
        "p50_ms": _percentile(latencies, 0.50),
        "p95_ms": _percentile(latencies, 0.95),
        "mean_ms": round(sum(latencies) / len(latencies), 1) if latencies else None,
        "prompt_tokens": prompt_toks or None,
        "completion_tokens": comp_toks or None,
        "thinking_calls": think_calls,
    }
    diffs = _head_to_head(todo, eval_preds, offline_preds)
    target_report = report_path or (out_path.parent / "vlm-report.md")

    report = {
        "ok": True,
        "n_total": total,
        "n_scored": len(valid_entries),
        "n_abstained": len(abstained_entries),
        "n_failed": len(failed_entries),
        "coverage": round(len(valid_entries) / total, 4) if total else 0.0,
        "health": health,
        "telemetry": telemetry,
        "vlm_score": full_score,
        "vlm_valid_only_score": valid_score,
        "comparison": {"offline": offline_score},
        "head_to_head": {
            "vlm_wins": len(diffs["vlm_wins"]),
            "offline_wins": len(diffs["offline_wins"]),
            "both_wrong": len(diffs["both_wrong"]),
        },
        "out": str(out_path),
        "report_path": str(target_report),
    }
    _write_report_md(report, diffs, failed_entries, target_report)
    return report


def _write_report_md(
    report: dict[str, Any],
    diffs: dict[str, list[dict[str, Any]]],
    failed_entries: list[dict[str, Any]],
    rp: Path,
) -> None:
    v = report.get("vlm_score") or {}
    o = (report.get("comparison") or {}).get("offline") or {}
    t = report.get("telemetry") or {}
    health = report.get("health") or {}
    models = ", ".join(health.get("models") or []) or "—"

    lines = [
        "# گزارشِ بنچ‌مارکِ مغزِ هوشمند (VLM — Qwen محلی)",
        "",
        f"- مدل‌های دیده‌شده روی سرور: `{models}`",
        f"- کل نمونه‌ها: **{report.get('n_total')}** · معتبر: **{report.get('n_scored')}** "
        f"(امتناعِ آگاهانه: {report.get('n_abstained')}) · شکستِ فنی: **{report.get('n_failed')}** "
        f"· پوشش: **{report.get('coverage', 0):.0%}**",
        f"- تأخیر: p50={t.get('p50_ms') or '—'}ms · p95={t.get('p95_ms') or '—'}ms · "
        f"کل اجرا={t.get('total_s') or '—'}s · فراخوانیِ با `<think>`: {t.get('thinking_calls', 0)}",
    ]
    if t.get("prompt_tokens") or t.get("completion_tokens"):
        lines.append(
            f"- مصرف توکن: ورودی={t.get('prompt_tokens')} · خروجی={t.get('completion_tokens')}"
        )
    lines += [
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

    if v.get("test_only"):
        vt = v["test_only"]
        ot = o.get("test_only") or {}
        lines += [
            "",
            "## نمره‌ی رسمی (Holdout / `split=test` — قانون ۵)",
            f"- قیمت: آفلاین={ot.get('price_within_2pct', 0):.0%} · VLM={vt.get('price_within_2pct', 0):.0%}",
            f"- دسته: آفلاین={ot.get('category_accuracy', 0):.0%} · VLM={vt.get('category_accuracy', 0):.0%}",
            f"- ویژگی: آفلاین={ot.get('attribute_accuracy', 0):.0%} · VLM={vt.get('attribute_accuracy', 0):.0%}",
        ]

    lines += ["", "## مقایسه‌ی نقطه‌به‌نقطه (منتقدِ بی‌رحم)"]
    for title, key in [
        ("پیروزی‌های VLM بر آفلاین", "vlm_wins"),
        ("خطاهای VLM که آفلاین درست زده", "offline_wins"),
        ("خطای مشترک هر دو", "both_wrong"),
    ]:
        rows = diffs.get(key) or []
        lines.append(f"### {title} ({len(rows)})")
        if not rows:
            lines.append("- موردی نبود")
        else:
            for r in rows[:25]:
                lines.append(
                    f"- `{r['id']}` · طلا={r['gold_cat']}/{r['gold_price']} · "
                    f"آفلاین={r['offline_cat']}/{r['offline_price']} · "
                    f"VLM={r['vlm_cat']}/{r['vlm_price']} · «{r['caption']}»"
                )

    if failed_entries:
        lines += ["", f"## شکست‌های فنی / پارس ({len(failed_entries)})"]
        for fe in failed_entries[:25]:
            lines.append(f"- `{fe.get('id')}`: {fe.get('error')}")

    lines += [
        "",
        "> نکته‌ی قانون ۵: تا پیش از holdout (بچ ۲)، این‌ها روی **dev** هستند؛ عددِ رسمی با holdout می‌آید.",
        "",
    ]
    rp.parent.mkdir(parents=True, exist_ok=True)
    rp.write_text("\n".join(lines), encoding="utf-8")
