# استقرار قطعی روی هاست cPanel (پارس‌هاست) — بر اساس مستندات رسمی

> بازنویسی‌شده پس از مطالعه‌ی کامل مستندات cPanel «Setup Python App» (CloudLinux/Passenger)
> و آموزش‌های رسمی هاست پایتون — دیگر حدسی در کار نیست.

## 🔑 یک حقیقت کلیدی (ریشه‌ی همه‌ی خطاهای قبلی)
در هاست‌های اشتراکی CloudLinux، پایتون **فقط از طریق virtualenv خودِ اپ** در ترمینال در دسترس است:
- پوشه‌های `/opt/alt/pythonXXX` دیده می‌شوند ولی باینری‌هایشان داخل قفس امنیتی (CageFS) اجرایی نیستند.
- مسیر **مستند و پشتیبانی‌شده**: ابتدا در cPanel اپ بسازید → cPanel دستور فعال‌سازی virtualenv را
  نشان می‌دهد (شکل: `source /home/USER/virtualenv/APP/3.x/bin/activate && cd /home/USER/APP`) →
  همان را Paste کنید. هرگز مسیر virtualenv را حدس نزنید — از صفحه‌ی اپ کپی کنید.

## گام ۱ — ساخت اپ (cPanel UI)
**Software → Setup Python App → Create Application**

| فیلد | مقدار |
|---|---|
| Python version | بالاترین موجود (۳.۱۲ یا ۳.۱۱) |
| Application root | `peyja` |
| Application URL | خالی (ریشه‌ی دامنه‌ی اصلی) یا kaveh185.n-cpanel.xyz |
| Application startup file | `passenger_wsgi.py` |
| Application Entry point | `application` |
| Passenger Log File | `logs/passenger.log` (توصیه‌شده) |

→ **CREATE** · سپس دستور «فعال‌سازی» بالای صفحه را **کپی** کنید.

## گام ۲ — ترمینال (دستور کپی‌شده از صفحه‌ی اپ!)
```bash
# ۱) دقیقاً همان دستوری که صفحه‌ی اپ نشان داد (مثال):
source /home/kavehncp/virtualenv/peyja/3.12/bin/activate && cd /home/kavehncp/peyja
# ✅ نشانه‌ی موفقیت: ابتدای prompt می‌شود (peyja:3.12)

# ۲) اگر cPanel فایل ورودی را جایگزین کرده بود، نسخه‌ی ما را برگردانید:
git checkout -- passenger_wsgi.py

# ۳) فایل تنظیمات (.env) — بلوک cat heredoc طبق progress-report

# ۴) نصب وابستگی‌ها
pip install -e .
# (جایگزین بدون ترمینال: دکمه‌ی «Run Pip Install» با requirements.txt)

# ۵) معاینه
python -m apps.cli.main doctor
```

## گام ۳ — راه‌اندازی و آزمون
1. در صفحه‌ی اپ: **Restart** (آیکون دایره‌ای) — Passenger اپ را با virtualenv خودش بالا می‌آورد.
2. `https://kaveh185.n-cpanel.xyz/health` → باید `{"status":"ok"...}` بدهد.

## عیب‌یابی (از مستندات)
| خطا | معنا | راه‌حل |
|---|---|---|
| `python3: command not found` | virtualenv فعال نیست | دستور فعال‌سازی صفحه‌ی اپ را Paste کنید |
| ۵۰۳ Service Unavailable | اپ Start نشده یا import خطا | Setup Python App → Start/Restart؛ لاگ Passenger |
| ۵۰۲/ImportError | اشتباه در passenger_wsgi.py | `git checkout -- passenger_wsgi.py` سپس Restart |
| `No module named ...` | بسته خارج از virtualenv نصب شده | حتماً بعد از activate نصب کنید |
| `no such table` | جداول ساخته نشده | `python -m apps.cli.main doctor` یک بار اجرا شود |
| `ERR_CONNECTION_CLOSED` / باز نشدن دامنه | معمولاً **DNS دامنه ثبت نشده (NXDOMAIN)** یا SSL | اول `curl -sk -H "Host: DOMAIN" https://127.0.0.1/health` روی خود سرور؛ اگر پاسخ داد = فقط DNS/SSL دامنه خراب است → تیکت میزبان یا دامنه‌ی اختصاصی + AutoSSL |

## تشخیص DNS از بیرون (تجربه‌ی واقعی ۲۰۲۶-۱۱)
دامنه‌ی موقت `kaveh185.n-cpanel.xyz` هنگام استقرار، **NXDOMAIN** بود (تأیید با Google/Cloudflare DoH).
آزمون محلی روی سرور (`curl -k -H "Host: ..." https://127.0.0.1/health`) سلامت اپ را از خرابی DNS جدا می‌کند.
برای وب‌هوک BoxAPI حتماً HTTPS معتبر لازم است ← دامنه‌ی اختصاصی + AutoSSL مسیر پایدار است.

## سناریوی «۴۰۴ استاتیک cPanel» (تجربه‌ی واقعی)
اگر آزمون محلی، صفحه‌ی ۴۰۴ استاندارد cPanel برگرداند (نه JSON):
- وب‌سرور و SSL سالم‌اند؛ ولی دامنه به اپ Passenger **bind نشده**.
- رفع: Setup Python App → Edit اپ → فیلد **Application URL** = دامنه → Save → Restart.
- سپس دوباره آزمون محلی → باید JSON سلامت بیاید.

## رفع DNS بدون تیکت (بدون خرید دامنه)
1. **Zone Editor** (cPanel → Domains → Zone Editor): اگر زون `kaveh185.n-cpanel.xyz` هست،
   رکورد **A** اضافه کنید: مقدار `85.158.145.202` (IP سرور از رکوردهای مادر).
2. اگر زون نبود: **دامنه‌ی رایگان `nip.io`** — هر زیردامنه‌ی شکل-IP به همان IP resolve می‌شود:
   `85.158.145.202.nip.io` را در cPanel → Domains → Create a New Domain اضافه کنید،
   Application URL را روی آن بگذارید، سپس **SSL/TLS Status → Run AutoSSL**.
3. راه پایدار بلندمدت: دامنه‌ی اختصاصی + AutoSSL.

## نکات معماری
- Passenger فقط WSGI می‌فهمد ← `a2wsgi.ASGIMiddleware` در `passenger_wsgi.py` پل ASGI است.
- دیتابیس: SQLite (فایل `data/app.db` داخل ریشه‌ی اپ) — مناسب هاست اشتراکی؛ بعداً Postgres.
- بعد از هر `git pull` روی هاست: `pip install -e .` (اگر وابستگی عوض شده) + **Restart**.
