"""کاتالوگِ لغتِ محصول/برندِ لایه‌ی آفلاین.

دستورِ کار (دور ۲ — تأییدِ انسانیِ verification-sheet):
- چند محصول در یک کپشن → «همه» جدا شوند (نکته‌ی ۱ کاربر)
- نامِ محصول («کرم آبرسان» …) برای کاتالوگ/جست‌وجو استخراج شود (نکته‌ی ۲)
- جست‌وجوی برند/نام «موقعیت‌محور» است: نزدیک‌ترین به ابتدای متن، نه اولینِ دیکشنری
- الگوها با فاصله/خط‌شکنِ بین‌واژه‌ای مدارا می‌کنند («سرم ضد\\nریزش») و جمعِ فارسی
  («اسپریای» = اسپری+ای) را می‌پذیرند؛ مرزِ واژه‌ی سخت‌گیرانه برای تله‌های کوتاه
  («داو»⊂«مداومه»، «مام»⊂«مامان») حفظ شده.
"""

from __future__ import annotations

import itertools
import re

# ── برندها (نیچِ لوازم آرایشی/بهداشتی ایران — قابلِ توسعه با هر کشفِ کرال) ───────
BRANDS: list[str] = [
    "ایزیدین",
    "آرنسیا",
    "سنتلا",
    "نامبوزین",
    "فارم استی",
    "فارماسی",
    "دکتر التیا",
    "دکتر ملاکسین",
    "کلینیک",
    "اکسیس وای",
    "گارنیه",
    "نیوآ",
    "داو",
    "لورال",
    "میبلین",
    "اوریفلیم",
    "کالیستا",
    "شانل",
    "دیور",
    "تام فورد",
    "این‌لی",
    "مای",
    "شون",
    "سی‌گل",
    "کامان",
    "بیول",
    "لیو",
    "هیدرودرم",
    "اولای",
    "نیتروژنا",
    "سینره",
    "پریم",
    "آردن",
    "اسنس",
    "کیکو",
    "شیگلم",
    "میکاپ",
    "لیلیو",
    "لیلی‌یو",
    "آنوا",
    "سان‌سل",
    "گلدن رز",
    "بث اند بادی",
    "ویکتوریا سیکرت",
    "کیکو میلانو",
    "وازلین",
    "مک",
    "نارس",
    "هلنس رز",
    "فنتی بیوتی",
    "گابرینی",
    "لپی‌کر",
    "lilyeve",
]

# نگاشت هم‌ارزِ دوخطی (لاتین/املایی → نام فارسیِ کاتالوگ) — هم برای یکتاسازی کاتالوگ، هم ارزیابی
BRAND_ALIASES: dict[str, str] = {
    "sheglam": "شیگلم",
    "kiko": "کیکو میلانو",
    "kiko milano": "کیکو میلانو",
    "lilyeve": "لیلی‌یو",
    "لیلیو": "لیلی‌یو",
    "isdin": "ایزیدین",
    "farmasi": "فارماسی",
    "numbuzin": "نامبوزین",
    "centella": "سنتلا",
    "anua": "آنوا",
    "farmstay": "فارم استی",
    "farm stay": "فارم استی",
    "axis-y": "اکسیس وای",
    "axis y": "اکسیس وای",
    "dr althea": "دکتر التیا",
    "dr. althea": "دکتر التیا",
    "clinique": "کلینیک",
    "vaseline": "وازلین",
    "dove": "داو",
    "essence": "اسنس",
    "nivea": "نیوآ",
    "garnier": "گارنیه",
    "loreal": "لورال",
    "l'oreal": "لورال",
    "maybelline": "میبلین",
    "golden rose": "گلدن رز",
    "bath & body works": "بث اند بادی",
    "bath and body": "بث اند بادی",
    "victoria's secret": "ویکتوریا سیکرت",
    "mac": "مک",
    "nars": "نارس",
    "fenty beauty": "فنتی بیوتی",
}


def canonical_brand(brand: str | None) -> str | None:
    """یکتاسازی نام برند (مثل Sheglam → شیگلم، Lilyeve → لیلی‌یو)."""
    if not brand:
        return None
    cleaned = brand.strip()
    if not cleaned:
        return None
    key = fold(cleaned).strip()
    return BRAND_ALIASES.get(key, cleaned)


# ── نام‌های محصول (برای نام/کاتالوگ و تکه‌بندیِ پست‌های چندمحصوله) ───────────────
PRODUCT_TERMS: list[str] = [
    "کرم ضد افتاب",
    "ضد افتاب",
    "کرم دست و صورت",
    "کرم دست",
    "کرم دور چشم",
    "دورچشم",
    "دور چشم",
    "کرم مرطوب",
    "مرطوب کننده",
    "ابرسان چند کاره",
    "ابرسان",
    "ترمیم کننده",
    "ضد چروک",
    "ضد لک",
    "ضد جوش",
    "روشن کننده",
    "ماسک صورت",
    "اسکراب بدن",
    "اسکراب",
    "تونر",
    "میسلار",
    "فیس واش",
    "ژل شستشو",
    "ویتامین c",
    "ویتامین سی",
    "امپول کلاژن",
    "امپول",
    "سرم صورت",
    "سرم ضد ریزش",
    "سرم مو",
    "نرم کننده",
    "ماسک مو",
    "روغن مو",
    "حالت دهنده",
    "شامپو بدون سولفات",
    "شامپو بدن",
    "شامپو",
    "کرم پودر",
    "بی بی کرم",
    "سی سی کرم",
    "کرم پوشش دهنده",
    "ریمل",
    "رژ لب",
    "رژلب",
    "لیپ گلاس",
    "لیپگلاس",
    "لیپ گلس",
    "لیپگلس",
    "لیپ بالم",
    "بالم لب",
    "پالت سایه",
    "سایه چشم",
    "سایه",
    "کانسیلر",
    "فوندیشن",
    "پنکیک",
    "هایلایتر",
    "کانتور",
    "پرایمر",
    "خط چشم",
    "خطچشم",
    "لاک",
    "مداد",
    "لوسیون بدن",
    "بادی اسپلش",
    "ضد تعریق",
    "ضدتعریق",
    "دئودورانت",
    "مام قارچی",
    "مام",
    "اسپری بدن",
    "اسپری",
    "خوشبو کننده",
    "خوشبوکننده",
    "ادوپرفیوم",
    "ادکلن",
    "عطر",
    "پک شیگلم",
    "پک ارایشی",
    "پک آرایشی",
]

# دسته‌ی هر نامِ محصول (پشتیبانِ دسته‌یابی برای تکه‌های چندمحصوله)
TERM_CATEGORY: dict[str, str] = {
    "کرم ضد افتاب": "skincare",
    "ضد افتاب": "skincare",
    "کرم دست و صورت": "skincare",
    "کرم دست": "skincare",
    "کرم دور چشم": "skincare",
    "دورچشم": "skincare",
    "دور چشم": "skincare",
    "کرم مرطوب": "skincare",
    "مرطوب کننده": "skincare",
    "ابرسان چند کاره": "skincare",
    "ابرسان": "skincare",
    "ترمیم کننده": "skincare",
    "ضد چروک": "skincare",
    "ضد لک": "skincare",
    "ضد جوش": "skincare",
    "روشن کننده": "skincare",
    "ماسک صورت": "skincare",
    "اسکراب بدن": "skincare",
    "اسکراب": "skincare",
    "تونر": "skincare",
    "میسلار": "skincare",
    "فیس واش": "skincare",
    "ژل شستشو": "skincare",
    "ویتامین c": "skincare",
    "ویتامین سی": "skincare",
    "امپول کلاژن": "skincare",
    "امپول": "skincare",
    "سرم صورت": "skincare",
    "سرم ضد ریزش": "skincare",
    "سرم": "skincare",
    "کرم پودر": "makeup",
    "بی بی کرم": "makeup",
    "سی سی کرم": "makeup",
    "کرم پوشش دهنده": "makeup",
    "ریمل": "makeup",
    "رژ لب": "makeup",
    "رژلب": "makeup",
    "لیپ گلس": "makeup",
    "لیپگلس": "makeup",
    "لیپ گلاس": "makeup",
    "لیپگلاس": "makeup",
    "لیپ بالم": "makeup",
    "بالم لب": "makeup",
    "پالت سایه": "makeup",
    "سایه چشم": "makeup",
    "سایه": "makeup",
    "کانسیلر": "makeup",
    "فوندیشن": "makeup",
    "پنکیک": "makeup",
    "هایلایتر": "makeup",
    "کانتور": "makeup",
    "پرایمر": "makeup",
    "خط چشم": "makeup",
    "خطچشم": "makeup",
    "لاک": "makeup",
    "مداد": "makeup",
    "پک شیگلم": "makeup",
    "پک ارایشی": "makeup",
    "پک آرایشی": "makeup",
    "لوسیون بدن": "deo_fragrance",
    "ضد تعریق": "deo_fragrance",
    "ضدتعریق": "deo_fragrance",
    "دئودورانت": "deo_fragrance",
    "مام قارچی": "deo_fragrance",
    "مام": "deo_fragrance",
    "اسپری بدن": "deo_fragrance",
    "اسپری": "deo_fragrance",
    "خوشبو کننده": "deo_fragrance",
    "خوشبوکننده": "deo_fragrance",
    "بادی اسپلش": "deo_fragrance",  # تصحیحِ بازبینی: جزوِ خانواده‌ی دئو (خطایِ تأییدِ انسانی)
    "شامپو بدن": "hygiene",
    "شامپو بدون سولفات": "haircare",
    "شامپو": "haircare",
    "نرم کننده": "haircare",
    "ماسک مو": "haircare",
    "روغن مو": "haircare",
    "سرم مو": "haircare",
    "حالت دهنده": "haircare",
    "ادوپرفیوم": "perfume",
    "ادکلن": "perfume",
    "عطر": "perfume",
}

# واژه‌هایِ «دسته‌ای» (نه نامِ محصول) — در انتخابِ نام، فقط وقتی انتخابِ دیگری نیست
CATEGORY_WORDS = {
    "خوشبو کننده",
    "خوشبوکننده",
    "ضد تعریق",
    "ضدتعریق",
    "دئودورانت",
    "اسپری",
    "مام",
    "کرم",
    "سرم",
    "سایه",
    "مداد",
    "شامپو",
    "عطر",
    "تونر",
    "اسکراب",
    "ریمل",
    "رژلب",
    "رژ لب",
    "لاک",
    "کانتور",
    "پرایمر",
    "امپول",
    "نرم کننده",
}

# نرمال‌سازی: ZWNJ و «_» → فاصله (نگاشتِ موقعیت ۱:۱)، الف/ی/تای عربی → فارسی، ارقام → لاتین
_TRANS = {ord("\u200c"): " ", ord("_"): " ", ord("\t"): " ", ord("\r"): " "}
_TRANS.update(
    {
        ord("آ"): "ا",
        ord("أ"): "ا",
        ord("إ"): "ا",
        ord("ٱ"): "ا",
        ord("ة"): "ه",
        ord("ۀ"): "ه",
        ord("ك"): "ک",
        ord("ي"): "ی",
        ord("ى"): "ی",
        ord("ؤ"): "و",
        ord("ئ"): "ی",
        ord("ـ"): "",
    }
)
_TRANS.update({0x06F0 + i: chr(0x30 + i) for i in range(10)})  # ۰-۹ → 0-9
_TRANS.update({0x0660 + i: chr(0x30 + i) for i in range(10)})  # ٠-٩ → 0-9
_LETTER = r"[a-z\u0600-\u06ff]"
_SUFFIX = r"(?:هایی|های|ها|ای)?"  # جمعِ فارسیِ مجاز: «اسپریای» = اسپری+ای
_CACHE: dict[str, list[tuple[re.Pattern[str], str]]] = {}


def fold(text: str) -> str:
    """نرمال‌سازیِ موقعیت‌محور (start/end را با متنِ اصلی یکی نگه می‌دارد)."""
    return text.translate(_TRANS).lower()


def _compress(text: str) -> str:
    """بدونِ فاصله — برای مقایسه‌ی «ضد تعریق» ≈ «ضدتعریق»."""
    return "".join(fold(text).split())


def _compile(terms: list[str]) -> list[tuple[re.Pattern[str], str]]:
    """الگو با مدارای فاصله/خط‌شکن بین واژه‌ها + جمعِ فارسی + مرزِ سخت‌گیرانه."""
    key = "|".join(terms)
    if key not in _CACHE:
        pats = []
        for term in sorted(terms, key=len, reverse=True):
            words = r"\s*".join(re.escape(w) for w in fold(term).split())
            pats.append(
                (
                    re.compile(rf"(?<!{_LETTER}){words}{_SUFFIX}(?!{_LETTER})"),
                    term,
                )
            )
        _CACHE[key] = pats
    return _CACHE[key]


def _all_matches(pats: list[tuple[re.Pattern[str], str]], t: str) -> list[tuple[int, int, str]]:
    """همه‌ی برخوردها: (شروع، پایان، واژه‌ی اصلی) — مرتب بر اساس موقعیت در متن."""
    found: list[tuple[int, int, str]] = []
    seen: set[tuple[int, str]] = set()
    for pattern, original in pats:
        for m in pattern.finditer(t):
            hit = (m.start(), original)
            if hit not in seen:
                seen.add(hit)
                found.append((m.start(), m.end(), original))
    found.sort(key=lambda x: (x[0], -(x[1] - x[0])))
    return found


def matched_terms(text: str, terms: list[str]) -> list[str]:
    """کدام‌یک از واژه‌های داده‌شده در متن هستند (مرزِ واژه + مدارای فاصله/جمع)."""
    return [h[2] for h in _all_matches(_compile(terms), fold(text))]


def find_brand(text: str) -> str | None:
    """برندِ نزدیک‌تر به ابتدای متن («ایزیدین» قبل از «فارماسی» در هر کپشن)."""
    hits = _all_matches(_compile(BRANDS), fold(text))
    return hits[0][2] if hits else None


def find_product_name(text: str) -> str | None:
    """نامِ محصول — امتیاز: همسایگیِ برند («امپول کلاژن فارم استی») > نامِ واقعی > اولِ متن."""
    t = fold(text)
    hits = _all_matches(_compile(PRODUCT_TERMS), t)
    if not hits:
        return None
    brand_pats = _compile(BRANDS)
    real = [h for h in hits if h[2] not in CATEGORY_WORDS]
    pool = real or hits

    def _score_num(h: tuple[int, int, str]) -> int:
        start, end, _ = h
        near = t[max(0, start - 15) : end + 30]
        has_brand = any(p.search(near) for p, _o in brand_pats)
        return 4 if has_brand else 0

    # امتیازِ بیشتر ← اولِ متن ← بلندتر
    pool.sort(key=lambda h: (-_score_num(h), h[0], -(h[1] - h[0])))
    return pool[0][2]


def find_product_category(text: str) -> str | None:
    """دسته‌ی نامِ محصولِ یافته‌شده — پشتیبانِ _guess_category."""
    name = find_product_name(text)
    return TERM_CATEGORY.get(name) if name else None


def split_product_spans(caption_text: str) -> list[str]:
    """تکه‌بندیِ کپشنِ چندمحصوله به تکه‌های «یک-محصوله» (نکته‌ی ۱ کاربر).

    قاعده‌ها به‌ترتیبِ اطمینان:
    ۱. جداکننده‌های صریحِ فهرستی: خط/گلوله/عددِ فهرستی («۱.» «2)») / «|» / «؛»
    ۲. مرزِ ایموجی‌ها وقتی دو طرفش متنِ فارسی/لاتین است («کرم ضدآفتاب ❄️ ضدآفتاب پریم»)
    ۳. تکه‌بندی روی شروعِ واژه‌ی محصولِ بعدی وقتی چند واژه‌ی محصول در یک خط‌اند
       («ضد افتاب ایزیدین اسکراب بدن فارماسی …»)
    """
    text = caption_text or ""
    if not text.strip():
        return []

    # ۱. جداکننده‌های صریحِ فهرستی (ارقامِ فارسی/لاتین)
    sep = re.compile(
        r"[\n\r]+|\s*[•●▪◦·]\s*|\s*\|+\s*|\s*؛+\s*"
        r"|(?<![\w])(?:[1-90-9۰-۹][0-9۰-۹]?\s*[.\-–)])\s*"
    )
    parts = [p.strip(" \t\u200c‏‎") for p in sep.split(text) if p.strip(" \t\u200c‏‎")]

    # ۲. اگر یک تکه هنوز چند «واژه‌ی محصول» دارد، با ایموجی/تکه‌بندِ واژه‌ی محصول بشکن
    out: list[str] = []
    for part in parts:
        out.extend(_chunk_by_product_terms(part))

    if not out:
        return [text.strip()]
    # تکه‌ی اول همان کپشنِ کامل نیست ولی همه‌ی متن پوشش داده می‌شود
    return out


def _chunk_by_product_terms(part: str) -> list[str]:
    """شکستنِ یک تکه روی مرزِ ایموجی/واژه‌ی محصولِ بعدی — «همه‌ی محصولات» گم نشوند."""
    t = fold(part)
    emoji_class = "\U0001f000-\U0001faff\u2600-\u27bf\u2b00-\u2bff\ufe0f\u200d"
    # مرزِ ایموجی بین دو متنِ فارسی/لاتین (نه بعد از متنِ انتهایی)
    emoji = re.compile(rf"{_LETTER}[^{emoji_class}]*?[{emoji_class}]+\s*(?={_LETTER})")
    spans = [m.end() for m in emoji.finditer(t)]

    # واژه‌ی محصولِ بعدی وقتی «بلافاصله» بعد از یک واژه‌ی محصولِ دیگر می‌آید
    terms = _compile([x for x in PRODUCT_TERMS if x not in CATEGORY_WORDS])
    term_starts = sorted({m.start() for pattern, _o in terms for m in pattern.finditer(t)})
    strong_heads = {
        "ضد",
        "اسکراب",
        "شامپو",
        "لیپ",
        "ریمل",
        "رژلب",
        "رژ",
        "پالت",
        "کانتور",
        "بالم",
        "ادوپرفیوم",
        "ادکلن",
        "مام",
        "اسپری",
        "لوسیون",
        "بادی",
        "تونر",
        "میسلار",
        "فیس",
        "ژل",
        "ویتامین",
        "امپول",
        "فوندیشن",
        "پنکیک",
        "هایلایتر",
        "پرایمر",
        "خط",
        "لاک",
        "مداد",
        "کانسیلر",
        "پک",
    }
    for i in range(1, len(term_starts)):
        # «فاصله» = از پایانِ واژه‌ی قبل تا شروعِ واژه‌ی بعدی
        prev_start = term_starts[i - 1]
        prev_end = next(
            (m.end() for p, _o in terms for m in [p.match(t[prev_start:])] if m),
            prev_start,
        )
        between = t[prev_end : term_starts[i]]
        if not between or not re.fullmatch(rf"(?:{_LETTER}|\d|\s|«|»|,|،|:|؛|/|-|–|ـ)+", between):
            continue
        prev_term = next((o for p, o in terms if p.match(t[term_starts[i - 1] :])), "")
        next_term = next((o for p, o in terms if p.match(t[term_starts[i] :])), "")
        # (الف) برند در فاصله = مرزِ قطعی  (ب) فاصله‌ی بلند  (ج) دو سرِ «قوی»
        has_brand = any(p.search(between) for p, _ in _compile(BRANDS))
        strong_pair = prev_term.split()[0] in strong_heads and next_term.split()[0] in strong_heads
        if has_brand or len(between) >= 5 or (between.strip() == "" and strong_pair):
            spans.append(term_starts[i])

    cuts = sorted({0, *spans, len(part)})
    chunks = []
    for a, b in itertools.pairwise(cuts):
        chunk = part[a:b].strip(" \t\u200c‏‎،,؛|•●▪◦·")
        if chunk:
            chunks.append(chunk)
    return chunks or [part.strip()]
