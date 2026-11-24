"""پارسر قیمت فارسی — «خطرناک‌ترین دام‌های متن فروشندگان ایرانی».

قواعد (تست‌شده روی نمونه‌های واقعی بازار):
  1. «۴/۸۰» / «4/80»  ⇒ 4.8 میلیون تومان = 4,800,000   (فرم «میلیون/هزار»)
  2. «۴/۸۰۰»        ⇒ 4,800,000 تومان                  (سه رقم بعد از ممیز)
  3. «۴۸۰» + تومن/ت   ⇒ 480,000 تومان                   (عدد ۳رقمی بی‌واحد = هزارتایی)
  4. «۴ میلیون و ۸۰۰» ⇒ 4,800,000                        (تفکیک «میلیون و هزار»)
  5. قیمت خط‌خورده (۹۰۰→۸۵۰) ⇒ old vs sale price
  6. «توافقی»/«تماس بگیرید» ⇒ price_on_request (نه null کور!)
  7. اعداد فارسی/عربی/لاتین همه نرمال می‌شوند (۴ ۴ 4)
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field

# ---------- نرمال‌سازی ارقام ----------
_FA_DIGITS = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")

_UNIT_WORDS = r"(?:تومان|تومن|توم|ت\b|tmn|toman|IRT|IRR)"

PRICE_ON_REQUEST_PATTERNS = [
    re.compile(p)
    for p in (
        r"توافقی",
        r"تماس\s*بگیرید",
        r"تماس\s*بگیر",
        r"پیوی|دایرکت|دایرکت\s*بدید|دایرکت\s*بزنید|پیام\s*بدید",
        r"قیمت\s*در\s*دایرکت",
        r"خاصیت\s*نداره",
    )
]


@dataclass
class PriceCandidate:
    value_toman: int | None
    raw_text: str
    kind: str  # "current" | "old" | "on_request"
    confidence: float
    notes: list[str] = field(default_factory=list)


@dataclass
class PriceParseResult:
    candidates: list[PriceCandidate]

    @property
    def current(self) -> PriceCandidate | None:
        return next((c for c in self.candidates if c.kind == "current"), None)

    @property
    def old(self) -> PriceCandidate | None:
        return next((c for c in self.candidates if c.kind == "old"), None)

    @property
    def on_request(self) -> bool:
        return any(c.kind == "on_request" for c in self.candidates)


def normalize(text: str) -> str:
    """یکدست‌سازی: NFKC + ارقام فارسی/عربی → لاتین، حذف کاراکترهای مزاحم.

    NFKC شکل‌های ارائه‌ی عربی/یونیکدِ عریض و ارقام تمام‌عرض را هم می‌شکند.
    """
    t = unicodedata.normalize("NFKC", text)
    t = t.translate(_FA_DIGITS)
    t = t.replace("\u200c", " ").replace("\u200f", "").replace("\u200e", "")
    t = re.sub(r"[،٬]", "", t)  # جداکننده‌ی هزارگان فارسی
    t = re.sub(r"(?<=\d),(?=\d{3})", "", t)  # جداکننده‌ی هزارگانِ لاتین: «380,000» ← «380000»
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _to_int(s: str) -> int:
    return int(s.replace(",", "").replace(" ", ""))


def _quick_value(s: str) -> int | None:
    """مقدار تومانی یک زیررشته‌ی عددی — با همان قواعد واحد (۹۰۰ ← ۹۰۰هزار)."""
    sub = parse_price_fa(normalize(s))
    return sub.current.value_toman if sub.current else None


def _overlaps(m: re.Match, spans: list[tuple[int, int]]) -> bool:
    return any(m.start() < e and s < m.end() for s, e in spans)


def _million_pattern() -> re.Pattern:
    """«۴/۸۰» «4/800» «۴.۸ میلیون» «۴ میلیون و ۸۰۰» — فرم‌های میلیونی.

    قاعده‌ی کسر: «X/YY» = X.YY میلیون (کسر ۲رقمی = صدم میلیون)؛ «X/YYY» = X میلیون و YYY هزار.
    """
    return re.compile(
        r"(?<![\d/.,])(?P<int>\d{1,3})\s*(?:/|\.)\s*(?P<frac>\d{1,3})"
        r"(?:\s*(?P<unit>میلیون|میل|mln))?"
        r"(?:\s*(?:و|&)\s*(?P<extra_thousand>\d{1,3})\s*(?:هزار|هـ))?"
    )


def _plain_with_unit_pattern() -> re.Pattern:
    """«۴۸۰ هزار تومان» «۲٬۵۰۰٬۰۰۰ تومان» «۴ میلیون تومان».

    نگهبان‌های (?<!...) و (?!...) مانع مسابقه‌ی اشتباه داخل اعشار («۴.۸» ← «۸») می‌شوند.
    """
    return re.compile(
        r"(?<![\d/.,])(?P<num>\d[\d,\s]{0,12}\d|\d)(?![\d/.,])\s*(?P<unit>میلیون|میلیارد|هزار|هـ)?\s*"
        + _UNIT_WORDS
    )


def _bare_pattern() -> re.Pattern:
    """«۴۸۰» بدون واحد — در بازار ایران = هزار تومان (فوق‌العاده مهم).

    نگهبانِ پسوند واحد («250mil») مانعِ خواندنِ حجم/وزن به‌جای قیمت می‌شود.
    """
    return re.compile(r"(?<![\d/.,])(?P<num>\d{3,12})(?![\d/.,%]|(?:mil|ml|cc|kg|گرم|میل)\b)")


def parse_price_fa(text: str) -> PriceParseResult:
    """متن کپشن/کامنت → کاندیداهای قیمت (تومان)."""
    if not text:
        return PriceParseResult([])

    t = normalize(text)
    candidates: list[PriceCandidate] = []

    # بافتِ «قیمت قبل/قدیم/اصلی» — عددِ بعدی = قیمتِ قدیم (نه قیمتِ فعلی)
    old_ctx_end: int | None = None
    m_old_ctx = re.search(r"(?:قیمت|نرخ)\s*(?:قبلی|قبل|قدیمی|قدیم|اصلی)", t)
    if m_old_ctx:
        old_ctx_end = m_old_ctx.end()

    # قیمت توافقی؟
    for pat in PRICE_ON_REQUEST_PATTERNS:
        m_por = pat.search(t)
        if m_por:
            candidates.append(PriceCandidate(None, m_por.group(0), "on_request", 0.9))
            break

    # --- قیمت خط‌خورده (فرم صریح): «تخفیف از ۹۰۰ به ۸۵۰» — اولی قدیم، دومی جدید ---
    discount_spans: list[tuple[int, int]] = []
    m_pair = re.search(
        r"(?:تخفیف|حراج|sale|off)[^0-9۰-۹]{0,8}(?:از)?\s*"
        r"(?P<old>\d[\d,/.\s]{0,12}\d|\d)\s*(?:به|تا|←|->)\s*(?P<new>\d[\d,/.\s]{0,12}\d|\d)",
        t,
    )
    explicit_discount = False
    if m_pair:
        old_v = _quick_value(m_pair.group("old"))
        new_v = _quick_value(m_pair.group("new"))
        if old_v and new_v:
            candidates.append(
                PriceCandidate(
                    old_v,
                    m_pair.group("old"),
                    "old",
                    0.92,
                    ["قیمت قبل از تخفیف (فرم صریح «از X به Y»)"],
                )
            )
            candidates.append(
                PriceCandidate(
                    new_v,
                    m_pair.group("new"),
                    "current",
                    0.95,
                    ["قیمت تخفیف‌خورده (فرم صریح)"],
                )
            )
            discount_spans += [
                (m_pair.start("old"), m_pair.end("old")),
                (m_pair.start("new"), m_pair.end("new")),
            ]
            explicit_discount = True
    else:
        m_single = re.search(
            r"(?:تخفیف|حراج|sale|off)[^0-9۰-۹]{0,8}از\s*(?P<old>\d[\d,/.\s]{0,12}\d|\d)",
            t,
        )
        if m_single:
            old_v = _quick_value(m_single.group("old"))
            if old_v:
                candidates.append(
                    PriceCandidate(
                        old_v,
                        m_single.group("old"),
                        "old",
                        0.85,
                        ["قیمت قبل از تخفیف («تخفیف از X»)"],
                    )
                )
                discount_spans.append((m_single.start("old"), m_single.end("old")))
                explicit_discount = True

    # الگو ۴: «۴ میلیون و ۸۰۰» (بدون ممیز)
    for m in re.finditer(
        r"(?P<int>\d{1,3})\s*(?:میلیون|میل)\s*(?:و|&)\s*(?P<thou>\d{1,3})\s*(?:هزار|هـ)?",
        t,
    ):
        if _overlaps(m, discount_spans):
            continue
        val = _to_int(m.group("int")) * 1_000_000 + _to_int(m.group("thou")) * 1_000
        candidates.append(PriceCandidate(val, m.group(0), "current", 0.95, ["فرم «میلیون و هزار»"]))

    # الگوی ممیزی: «۴/۸۰» «۴/۸۰۰» «۴.۸ میلیون»
    for m in _million_pattern().finditer(t):
        if _overlaps(m, discount_spans):
            continue
        frac_raw = m.group("frac")
        int_part = _to_int(m.group("int"))
        unit = m.group("unit")
        extra = m.group("extra_thousand")

        if unit or "/" in m.group(0) or "." in m.group(0):
            # کسر، کسرِ میلیون است: ۲ رقم = صدم (۴/۸۰ = ۴.۸M)؛ ۱ رقم = دهم (۴/۸ = ۴.۸M)؛ ۳ رقم = هزارتایی (۴/۸۰۰ = ۴٬۸۰۰٬۰۰۰)
            frac_mult = 10 ** (6 - len(frac_raw))
            val = int_part * 1_000_000 + _to_int(frac_raw) * frac_mult
            note = f"فرم «میلیون/کسر {len(frac_raw)}رقمی»"
            if extra:
                val += _to_int(extra) * 1_000
                note += " + هزار اضافه"
            if not unit and val < 1_000_000:
                # ممیز بدون واحد و مقدار کوچک — احتمالاً اشتباه تایپی؛ اعتماد کم
                candidates.append(
                    PriceCandidate(val, m.group(0), "current", 0.55, [note, "بدون واحد"])
                )
            else:
                candidates.append(PriceCandidate(val, m.group(0), "current", 0.95, [note]))

    # الگوی ساده با واحد: «۴۸۰ هزار تومان» «۲٬۵۰۰٬۰۰۰ تومان» «۴ میلیون تومان»
    for m in _plain_with_unit_pattern().finditer(t):
        if _overlaps(m, discount_spans):
            continue
        num = _to_int(m.group("num"))
        unit = m.group("unit")
        if unit in ("میلیون", "میل"):
            val = num * 1_000_000
        elif unit == "میلیارد":
            val = num * 1_000_000_000
        elif unit in ("هزار", "هـ"):
            val = num * 1_000
        elif num >= 100_000:
            val = num  # «۲٬۵۰۰٬۰۰۰ تومان» — عدد بزرگ خودش تومان است
        else:
            val = num * 1_000  # «۴۸۰ تومان» در فارکس فروشندگان = ۴۸۰ هزار
        kind = "current"
        note = "با واحد صریح"
        if old_ctx_end is not None and 0 <= m.start() - old_ctx_end <= 15:
            kind, note = "old", "قیمت قبل/قدیم (بافتِ صریح)"
        candidates.append(PriceCandidate(val, m.group(0), kind, 0.9, [note]))

    # اعداد برهنه (بدون واحد) — فقط وقتی هنوز کاندیدایی نداریم یا عدد بزرگ است
    covered = {c.raw_text for c in candidates}
    for m in _bare_pattern().finditer(t):
        if _overlaps(m, discount_spans):
            continue
        raw = m.group("num")
        if any(raw in c for c in covered):
            continue
        # حجم/وزن/سایز/شماره/تعداد/ساعت = مشخصه‌ی محصول است، نه قیمت («حجم ۴۰۰»)
        before = t[max(0, m.start() - 12) : m.start()]
        if re.search(r"(حجم|وزن|سایز|شماره|تعداد|ساعت|spf)\s*$", before, re.IGNORECASE):
            continue
        num = _to_int(raw)
        if num < 100:  # سایز/تعداد/شماره — قیمت نیست
            continue
        if num >= 10_000_000:  # عدد ۸رقمی: همان تومان است
            val, note, conf = num, "عدد ۸رقمی = تومان", 0.85
        elif num >= 100_000:  # ۶رقمی: تومان صریح
            val, note, conf = num, "عدد ۶رقمی = تومان", 0.85
        else:  # ۳رقمی مثل ۴۸۰: هزار تومان
            val, note, conf = num * 1_000, "عدد ۳رقمی = هزار تومان", 0.8
        kind = "current"
        if old_ctx_end is not None and 0 <= m.start() - old_ctx_end <= 15:
            kind, note = "old", "قیمت قبل/قدیم (بافتِ صریح)"
        candidates.append(PriceCandidate(val, raw, kind, conf, [note, "بدون واحد"]))

    # حدس خط‌خورده (بدون فرم صریح): دو عدد با بافت «تخفیف» — فقط با عقل سلیم اندازه (اختلاف حداکثر ۲۰ برابر)
    discount_ctx = re.search(r"تخفیف|حراج|off|%", t)
    if discount_ctx and not explicit_discount and len(candidates) >= 2:
        sorted_by_pos = sorted(
            [c for c in candidates if c.value_toman],
            key=lambda c: t.find(c.raw_text),
        )
        if len(sorted_by_pos) >= 2:
            first, second = sorted_by_pos[0], sorted_by_pos[1]
            if (
                first.kind == "current"
                and second.kind == "current"
                and 1 < (first.value_toman or 0) / max(second.value_toman or 1, 1) <= 20
            ):
                first.kind = "old"
                first.notes.append("قیمت خط‌خورده (حدس از بافت تخفیف)")
                first.confidence = 0.8

    # حذف تکراری‌ها (همان مقدار از الگوهای همپوشان)
    seen: set[tuple[str, int | None]] = set()
    unique: list[PriceCandidate] = []
    for c in candidates:
        key = (c.kind, c.value_toman)
        if key in seen:
            continue
        seen.add(key)
        unique.append(c)

    return PriceParseResult(unique)
