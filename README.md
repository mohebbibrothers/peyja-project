# Peyja — Supply-Side OS for Agentic Commerce

**فاز یک: «ستون فقرات داده» (Data Spine)** · نیچ: **لوازم آرایشی بهداشتی** 🧴

> «پلتفرمِ ابزار نمی‌سازیم؛ یک سیستم‌عاملِ کسب‌وکارِ واسطه‌ها می‌سازیم.»

## نصب و اجرا

```bash
# ۱. (اختیاری) زیرساخت — یا مستقیم SQLite محلی
docker compose up -d

# ۲. وابستگی‌ها (uv — استاندارد ۲۰۲۶)
uv sync --group dev

# ۳. تنظیمات
cp .env.example .env   # توکن‌های BoxAPI را وارد کنید

# ۴. سلامت سیستم
uv run python -m apps.cli.main doctor

# ۵. تست‌ها
make test

# ۶. ساخت کاتالوگ یک پیج
uv run python -m apps.cli.main build <username>
```

## معماری

```
packages/peyja/
├── config.py              # تنظیمات (pydantic-settings) — SQLite/Postgres
├── db.py                  # اتصال async
├── models.py              # schema v1 (Truth Graph — JSONType قابل‌حمل)
├── provider/              # Anti-Corruption Layer
│   ├── boxapi_data.py       # خط ② دیتای عمومی (کرال/ایندکس)
│   ├── boxapi_official.py   # خط ① رسمی (sendbox — async actions)
│   └── boxapi_ai.py         # خط ③ VLM
├── extract/
│   ├── schema.py            # Structured Outputs — SafeExtraction با مسیر فرار
│   ├── price_parser.py      # پارسر قیمت فارسی («۴/۸۰»، توافقی، تخفیف)
│   ├── price_history.py     # تاریخچه‌ی قیمت = «هشدار افت قیمت رقیب»
│   ├── caption.py           # تحلیل کپشن + seller_echo
│   └── pipeline.py          # regex → VLM → Evidence + بازه‌بندی اطمینان
├── ingest/
│   ├── crawler.py           # کرالر بودجه‌دار + آرشیو رسانه
│   ├── media.py             # MediaArchiver — sha256، بدون URL منقضی‌شونده
│   └── webhook.py           # HMAC-SHA256 (whsec_) — raw body
apps/
├── api/                     # FastAPI + وب‌هوک چندمسیره
├── cli/                     # peyja crawl | extract | build | doctor
└── worker/                  # وظایف پس‌زمینه
tests/                       # ۳۴ تست — پارسر، امنیت، schema، media، تاریخچه
evals/                       # «عدد سرنوشت» — gold-100 (لوازم آرایشی)
docs/                        # ADR-001 + BOXAPI-FINDINGS
```

## قواعد طلایی
1. **استخراج‌شده ≠ تأییدشده** — هر قیمت Evidence دارد (confidence + منبع).
2. **URL منقضی‌شونده = داده‌ی ازدست‌رفته** — رسانه‌ها فوراً دانلود و هش می‌شوند.
3. **هیچ‌جا BoxAPI مستقیم** — فقط از طریق `provider/`.
4. **کپشن = تاریخچه‌ی قیمت** — CaptionVersion → هشدار افت قیمت رقیب.
5. **confidence ≥ 0.7 ذخیره | 0.4–0.7 صف انسانی | < 0.4 رد** — نه حدس خودکار.

## گیت‌های موفقیت فاز یک
| سنجه | آستانه |
|---|---|
| استخراج قیمت (gold-100) | ≥ ۸۵٪ |
| دسته/ویژگی | ≥ ۷۵٪ |
| حلقه‌ی رسمی (وب‌هوک+ارسال+echo) | کامل روی صفحه‌ی تست |
| بایگانی خام + رسانه | ۱۰۰٪ |
| `peyja build <username>` | < ۵ دقیقه |

## CI/CD
هر push → GitHub Actions: `ruff` + `mypy` + `pytest` (ماتریس ۳.۱۱–۳.۱۳) + coverage.
