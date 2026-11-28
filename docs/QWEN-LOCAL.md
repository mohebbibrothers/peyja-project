# مغزِ هوشمندِ پیجا — مدلِ محلیِ Qwen (`qwen-local`)

> این API «مغزِ هوشمندِ سرور و هوشِ مصنوعیِ اصلی» ماست: **رایگان، روی سرورِ خودمان،
> بدونِ وابستگیِ بیرونی**. همه‌ی لایه‌های AI پیجا (استخراجِ VLM، و بعدتر ایجنت‌های
> پاسخ/DM) از یک دروازه‌ی واحد می‌گذرند: `peyja.provider.llm.get_ai_provider()`.

## ۱. مشخصات

| مورد | مقدار |
|---|---|
| endpoint | `http://10.1.231.22:8000/v1` (سازگار با OpenAI — پشتِ WireGuard شرکت) |
| مدل | `qwen3.6-35b-a3b` (نامِ مستعار: `qwen-local`) |
| context | ۱۳۱٬۰۷۲ توکن |
| کلید | ندارد (شبکه‌ی داخلی) — `qwen_api_key` برای آینده |

## ۲. معماری (چرا این‌طور)

```
apps (CLI/worker/loop) ──► get_ai_provider()  ──► QwenLocalProvider  ──► 10.1.231.22:8000/v1
                                │                       (OpenAICompatProvider)
                                └──► BoxApiAiProvider (پشتیبانِ خطِ ③)
```

- **`OpenAICompatProvider`** (`provider/llm.py`):
  - timeout سخت + retry نمایی برای 408/429/5xx/قطعی + احترام به Retry-After
  - **ضدِ تله‌ی پراکسی + WireGuard:** برای IPهای خصوصی/محلی (`10.x.x.x`, `127.0.0.1`)
    خودکار `trust_env=False` می‌شود تا `HTTP_PROXY`/`HTTPS_PROXY` سیستم ترافیکِ
    تونلِ WireGuard را نرباید
  - **مقاوم در برابر مدل‌های تفکرمحور (`<think>...</think>` در Qwen3.6):** بخشِ
    `<think>` حذف و شیءِ JSON با `JSONDecoder.raw_decode` از متنِ واقعی جدا می‌شود
    (حتی اگر داخل `<think>` علامت `{...}` باشد)
  - تصویر با data URI داخلی (بدون URL منقضی‌شونده)
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
| `QWEN_MAX_TOKENS` | `4096` | سقف توکن تا مدل وسطِ `<think>` قطع نشود |
| `QWEN_JSON_MODE` | `false` | `response_format=json_object` (اگر سرور پشتیبانی کند) |
| `QWEN_ENABLE_THINKING` | خالی | `false` برای غیرفعال‌کردن تفکر در سرورهای vLLM/SGLang |

## ۳. اثبات‌ها (قانون ۱، ۲، ۴، ۵)

- **۲۲ تستِ اختصاصی** (`tests/test_qwen_provider.py`) با **HTTP سرورِ زنده‌ی سازگار
  با OpenAI**: قراردادِ سیم، `<think>` حاوی `{...}`، قطعِ `<think>`، `reasoning_content`،
  خطای فوری 400، دورزدنِ پراکسی روی IP خصوصی، `enable_thinking`، امتناعِ آگاهانه
  بدونِ خطای بقاء (survivorship bias)، ازسرگیری‌پذیری، و اجرای سرتاسریِ CLI بدون `.env`.
- **`python -m peyja doctor --vlm`** سلامتِ مغز، تأخیر و انطباقِ نام مدل را بدون نیاز
  به کلیدهای `.env` می‌سنجد.

## ۴. بنچ‌مارکِ زنده — با اتصالِ WireGuard روی سیستمِ خودتان

> IPِ `10.1.231.22` داخلی است؛ **نیازی به ارسالِ فایل کانفیگ WireGuard نیست** (آن فایل
> کلیدِ خصوصیِ VPN شما را دارد). کافی است در کلاینت WireGuard سیستمِ خودتان متصل شوید
> و فرمان زیر را اجرا کنید:

### لینوکس / مک (Bash)

```bash
git clone https://github.com/mohebbibrothers/peyja-project.git ~/peyja-project 2>/dev/null || true
cd ~/peyja-project && git pull -q && \
python3 -m venv .venv && . .venv/bin/activate && pip install -q -e ".[dev]" && \
python3 -m peyja doctor --vlm && \
python3 -m peyja vlm-gold --fresh
```

### ویندوز (PowerShell)

```powershell
if (!(Test-Path "$HOME\peyja-project")) { git clone https://github.com/mohebbibrothers/peyja-project.git "$HOME\peyja-project" }
cd "$HOME\peyja-project"; git pull -q
python -m venv .venv; .\.venv\Scripts\Activate.ps1; pip install -q -e ".[dev]"
python -m peyja doctor --vlm
python -m peyja vlm-gold --fresh
```

خروجی:
- `out/vlm-predictions.json` — پیش‌بینیِ مغز + زمان و توکن هر نمونه (کشِ ازسرگیری‌پذیر؛
  نمونه‌های ناموفق در اجرای بعدی بدون `--fresh` خودکار دوباره تلاش می‌شوند)
- `out/vlm-report.md` — **گزارشِ مقایسه‌ایِ کامل + p50/p95 تأخیر + اختلاف‌های نقطه‌به‌نقطه**
- گزینه‌های مفید:
  - `--limit 5` — تستِ سریعِ ۵ نمونه‌ی اول در چند ثانیه پیش از اجرای کامل
  - `--no-think` — اگر پاسخ‌دهی کُند بود، حالت تفکر را خاموش می‌کند (در سرورهای vLLM/SGLang)
  - `-j 2` — اجرای ۲ درخواست هم‌زمان برای سرعت بیشتر
  - `--base-url` / `--model` — اگر در `/models` نامِ دیگری ثبت شده باشد

**کافی است** `out/vlm-report.md` (و در صورت امکان `out/vlm-predictions.json`) را
برای من بفرستید تا تحلیلِ منتقدِ بی‌رحم (قانون ۵) روی خروجیِ واقعیِ مدل انجام شود.

## ۵. عیب‌یابی

| نشانه | معنا | کار |
|---|---|---|
| `مغزِ هوشمند در دسترس نیست: ConnectError` | تونلِ WireGuard وصل نیست یا مسیر بسته است | اتصال WireGuard را چک کنید و `curl --noproxy '*' -s http://10.1.231.22:8000/v1/models` بزنید |
| با وجود اتصال WireGuard، timeout/502 می‌دهد | `HTTP_PROXY` سیستم مزاحم است | کدِ ما خودکار دور می‌زند؛ در `curl` هم `--noproxy '*'` بدهید |
| `مدل در /models نیست` / HTTP 404/400 | نامِ ثبت‌شده روی سرور فرق دارد | از خروجی `doctor --vlm` نام دقیق را بردارید و با `--model <id>` بدهید |
| مدل وسطِ `<think>` قطع شد | بودجه‌ی `max_tokens` کم بوده | `--max-tokens 8192` یا `--no-think` بگذارید |

## ۶. نقشه‌ی آینده (همین مغز)
- ایجنتِ پاسخِ خودکارِ DM (`loop/reply.py`) — همین provider
- طبقه‌بندِ «پستِ بدون محصول» (v0.3) — همین provider
- استخراجِ تصویرمحور (مدلِ vision در همان endpoint اگر اضافه شود — `extract_from_image` آماده است)
