"""مدل‌سازی کپشن و ادغام سیگنال فروشنده (echo)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from peyja.extract.price_parser import PriceParseResult, normalize, parse_price_fa

COLOR_WORDS = [
    "مشکی",
    "سفید",
    "سرمه‌ای",
    "قرمز",
    "آبی",
    "سبز",
    "زرد",
    "نارنجی",
    "صورتی",
    "بنفش",
    "قهوه‌ای",
    "کرم",
    "طوسی",
    "خاکستری",
    "نقره‌ای",
    "طلایی",
    "زیتونی",
    "یاسی",
    "فیروزه‌ای",
    "مشکی مات",
    "شتری",
    "عنابی",
    "سبز یشمی",
    "آبی نفتی",
    "نود",
]
SIZE_PATTERN = re.compile(r"(?:سایز|سایزها|حجم|وزن|س)\s*[:\-]?\s*(\d{1,3}(?:\s*[-–،,]\s*\d{1,3})*)")
VOLUME_SUFFIX_PATTERN = re.compile(r"(?<![\d/.,])(\d{1,4})\s*(?:میل|ml|mil)\b")

# ── شماره‌ی شید (shade): «شماره ۱۲» نه رنگ است نه سایز — فیلدِ مستقل ──────────
SHADE_PATTERN = re.compile(r"(?:شماره|شماره‌ی|شید)\s*[:\-]?\s*(\d{1,2}(?:\s*[-–،,]\s*\d{1,2})*)")
MAKEUP_ANCHORS = (
    "رژلب",
    "رژ لب",
    "لیپ",
    "گلاس",
    "سایه",
    "کیکو",
    "کانسیلر",
    "فوندیشن",
    "ریمل",
    "مداد",
    "لاک",
    "پنکیک",
    "پرایمر",
    "هایلایتر",
    "بالم",
    "میکاپ",
    "bb",
    "cc",
    "شیگلم",
    "اسنس",
    "گلدن رز",
)
SHADE_BLOCK_SUFFIX = re.compile(r"(?:میل|ml|mil|ساعت|گرم|رنگ|تایی|عدد|تومان|درصد|٪)\b")
SHADE_BLOCK_PREFIX = re.compile(r"(?:حجم|وزن|ساعت|spf)\s*$", re.IGNORECASE)
LATIN_TONES = [
    "berry",
    "burgundy",
    "nude",
    "rose",
    "peach",
    "coral",
    "mauve",
    "plum",
    "mocha",
    "wine",
]
COLOR_PATTERN = re.compile("|".join(COLOR_WORDS + LATIN_TONES))
MATERIAL_WORDS = [
    "چرم",
    "جیر",
    "نبوک",
    "پارچه‌ای",
    "کتان",
    "فلزی",
    "استیل",
    "پلاستیکی",
    "ضدآب",
]


@dataclass
class CaptionSignals:
    prices: PriceParseResult
    colors: list[str]
    sizes: list[str]
    materials: list[str]
    hashtags: list[str]
    shades: list[str] = field(default_factory=list)  # شماره‌ی شید — نه رنگ، نه سایز


def _extract_shades(t: str) -> list[str]:
    """شماره‌ی شید: «شماره ۱۲» صریح یا عددِ ۲رقمیِ چسبیده به محصول آرایشی.

    نگهبان‌ها: «۲۴ ساعت»، «۱۲ رنگ»، «۹۰ میل» شید نیستند؛ و بدون لنگرِ آرایشی
    هیچ عددِ برهنه‌ای شید تلقی نمی‌شود (ضدِ‌توهم).
    """
    tn = normalize(t)
    shades: list[str] = []

    def _add(v: str) -> None:
        for part in re.split(r"[-–،,]", v):
            p = part.strip()
            if p and p not in shades:
                shades.append(p)

    for m in SHADE_PATTERN.finditer(tn):
        _add(normalize(m.group(1)))
    has_anchor = any(a in tn.lower() for a in MAKEUP_ANCHORS)
    if has_anchor:
        for m in re.finditer(r"(?<![\d/.,])(\d{2})(?![\d/.,%])", tn):
            raw = m.group(1)
            if any(raw in s for s in shades):
                continue
            after = tn[m.end() : m.end() + 8].lstrip()
            before = tn[max(0, m.start() - 12) : m.start()]
            if SHADE_BLOCK_SUFFIX.match(after) or SHADE_BLOCK_PREFIX.search(before):
                continue
            _add(raw)
    return shades


def analyze_caption(text: str | None) -> CaptionSignals:
    t = text or ""
    size_m = SIZE_PATTERN.search(t)
    sizes: list[str] = []
    if size_m:
        norm = normalize(size_m.group(1))
        sizes = [s.strip() for s in re.split(r"[-–،,]", norm) if s.strip()]
    # حجم با پسوند واحد: «۹۰ میل» «250mil» «۵۰ ml»
    for vm in VOLUME_SUFFIX_PATTERN.finditer(normalize(t)):
        v = vm.group(1)
        if v not in sizes:
            sizes.append(v)
    return CaptionSignals(
        prices=parse_price_fa(t),
        colors=sorted({c.lower() for c in COLOR_PATTERN.findall(t)}),
        sizes=sizes,
        materials=[m for m in MATERIAL_WORDS if m in t],
        hashtags=re.findall(r"#(\w+)", t),
        shades=_extract_shades(t),
    )


def merge_seller_echo(
    current_price: int | None, echoed_price: int | None, echoed_text: str | None
) -> tuple[int | None, str, float]:
    """ادغام قیمت فعلی با قیمت تأییدشده‌ی فروشنده در چت.

    «قیمتی که فروشنده در چت می‌گوید، قیمت نهایی است» — confidence=1.0.
    خروجی: (قیمت نهایی, منبع, اعتماد)
    """
    if echoed_price is not None:
        return echoed_price, "seller_echo", 1.0
    if current_price is not None:
        return current_price, "caption", 0.85
    return None, "none", 0.0
