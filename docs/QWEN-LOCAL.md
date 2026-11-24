# مغزِ هوشمندِ پیجا — مدلِ محلیِ Qwen (`qwen-local`)

> این API «مغزِ هوشمندِ سرور و هوشِ مصنوعیِ اصلی» ماست: **رایگان، روی سرورِ خودمان،
> بدونِ وابستگیِ بیرونی**. همه‌ی لایه‌های AI پیجا (استخراجِ VLM، و بعدتر ایجنت‌های
> پاسخ/DM) از یک دروازه‌ی واحد می‌گذرند: `peyja.provider.llm.get_ai_provider()`.

## ۱. مشخصات

| مورد | مقدار |
|---|---|
| endpoint | `http://10.1.231.22:8000/v1` (سازگار با OpenAI) |
| مدل | `qwen3.6-35b-a3b` (نامِ مستعار: `qwen-local`) |
| context | ۱۳۱٬۰۷۲ توکن |
| کلید | ندارد (شبکه‌ی داخلی) — `qwen_api_key` برای آینده |

## ۲. معماری (چرا این‌طور)

```
apps (CLI/worker/loop) ──► get_ai_provider()  ──► QwenLocalProvider  ──► 10.1.231.22:8000/v1
                                │                       (OpenAICompatProvider)
                                └──► BoxApiAiProvider (پشتیبانِ خطِ ③)
```

- **`OpenAICompatProvider`** (`provider/llm.py`): هر endpointِ سازگار با OpenAI —
  timeout سخت، retry نمایی برای 429/5xx/قطعی، احترام به Retry-After، JSONِ مقاوم،
  تصویر با data URI.
- **ایزولاسیونِ خرابی:** اگر مغز قطع باشد، `_vlm_extract_safe` بی‌صدا None برمی‌گرداند
  و خطِ استخراج با موتورِ regex ادامه می‌دهد — «شکستِ مغز ≠ شکستِ محصول».
- **تنظیمات** (env یا `.env`):

| متغیر | پیش‌فرض | معنا |
|---|---|---|
| `VLM_PROVIDER` | `qwen-local` | `qwen-local` \| `boxapi` |
| `QWEN_BASE_URL` | `http://10.1.231.22:8000/v1` | endpoint |
| `QWEN_MODEL` | `qwen3.6-35b-a3b` | نامِ مدل |
| `QWEN_API_KEY` | خالی | Bearer در صورت نیاز |
| `QWEN_TIMEOUT` | `120` | ثانیه |
| `QWEN_MAX_RETRIES` | `3` | تعدادِ retry |
| `QWEN_JSON_MODE` | `false` | `response_format=json_object` (اگر سرور پشتیبانی کند) |

## ۳. اثبات‌ها (قانون ۱، ۲، ۴، ۵)

- **۱۵ تستِ اختصاصی** (`tests/test_qwen_provider.py`) با **HTTP سرورِ زنده‌ی سازگار
  با OpenAI در مقیاسِ کوچک**: قراردادِ سیم (URL/مدل/پیام‌ها)، JSON حصاردار،
  retry پس از 500، LlmError پس از اتمامِ retry، timeout، تصویرِ data URI،
  `response_format`، سقوطِ نرمِ مغز در pipeline، کارخانه‌ی provider، و
  **ازسرگیری‌پذیریِ بنچ‌مارک** (اجرای دوم = صفر صدای تازه به مدل).
- **`python -m peyja doctor`** حالا سلامتِ مغز را (GET `/models`) گزارش می‌دهد.

## ۴. بنچ‌مارکِ زنده — از داخلِ شبکه‌ی سرورِ مدل

> IPِ `10.1.231.22` داخلی است؛ از بیرونِ شبکه‌ی شما در دسترس نیست. این فرمان را
> روی هر ماشینِ داخلِ همان شبکه (یا خودِ سرور) اجرا کنید — **خودکفا و کامل**:

```bash
cd ~/peyja-project && git pull -q && \
python3 -m venv .venv && . .venv/bin/activate && pip install -q -e ".[dev]" && \
python3 -m peyja doctor && \
python3 -m peyja vlm-gold --fresh
```

خروجی:
- `out/vlm-predictions.json` — پیش‌بینیِ مغز برای هر نمونه (کشِ ازسرگیری‌پذیر؛
  اجرای بعدی فقط نمونه‌های جدید را صدا می‌زند — `--fresh` برای شروعِ دوباره)
- `out/vlm-report.md` — **جدولِ مقایسه‌ایِ مغزِ VLM در برابرِ موتورِ آفلاین** روی
  معیارهای gold (قیمت/دسته/ویژگی/بازیابیِ محصولات/نام/برند)
- گزینه‌ها: `--limit N` (آزمایشِ سریع) · `--base-url` / `--model` (جایگزینی) · `--fresh`

**کافی است** `out/vlm-report.md` (و در صورت امکان `out/vlm-predictions.json`) را
برای من بفرستید تا تحلیلِ منتقدِ بی‌رحم (قانون ۵) روی خروجیِ واقعیِ مدل انجام شود.

## ۵. عیب‌یابی

| نشانه | معنا | کار |
|---|---|---|
| `مغزِ هوشمند در دسترس نیست: ConnectError` | مسیرِ شبکه/پورت | `curl -s http://10.1.231.22:8000/v1/models` از همان ماشین |
| `HTTP 404` روی chat | base_url ناقص است | باید به `/v1` ختم شود |
| مدل ناشناخته (400) | نامِ مدل | از `/v1/models` نامِ دقیق را بردارید یا `--model` بدهید |
| پاسخِ کند/timeout | بارِ مدل | `QWEN_TIMEOUT` را بالا ببرید؛ `--limit` بگذارید |
| JSON خرابِ مکرر | مدلِ کوچک/پرامپت | موتور خودکار بازخورد می‌دهد (۳ تلاش)؛ خروجی‌ها را بفرستید تا پرامپت تنظیم شود |

## ۶. نقشه‌ی آینده (همین مغز)
- ایجنتِ پاسخِ خودکارِ DM (`loop/reply.py`) — همین provider
- طبقه‌بندِ «پستِ بدون محصول» (v0.3) — همین provider
- استخراجِ تصویرمحور (مدلِ vision در همان endpoint اگر اضافه شود — `extract_from_image` آماده است)
