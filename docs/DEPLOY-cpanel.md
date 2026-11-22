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
- ⚠️ **اول منفی کاذب را حذف کنید:** نام‌های مجازی cPanel روی IP عمومی سرور bind می‌شوند؛
  آزمون با `127.0.0.1` ممکن است به vhost پیش‌فرض بخورد و ۴۰۴ کاذب بدهد.
  آزمون معتبر از **بیرون** (بدون نیاز به DNS — روی سیستم خودتان؛ IP را از Shared IP بگیرید):
  `curl -k --resolve kaveh185.n-cpanel.xyz:80:85.198.13.88 http://kaveh185.n-cpanel.xyz/health`
- اگر آزمون بیرونی هم ۴۰۴ داد: وب‌سرور به اپ **نمی‌رساند** — مراحل رفع:
  Setup Python App → Edit اپ → فیلد **Application URL** = دامنه (+ مسیر `/` به‌صورت صریح) → Save → Restart.
  سپس `~/public_html/.htaccess` را ببینید: باید حاوی `PassengerAppRoot`/`PassengerBaseURI` باشد؛
  اگر نبود، خودتان بنویسید (الگو در همین سند) و دوباره Restart کنید.

## رفع DNS بدون تیکت (بدون خرید دامنه)
> ⚠️ **درس طلایی (تجربه‌ی واقعی):** IP رکورد مادر (`n-cpanel.xyz` → 85.158.145.202) **IP وبِ حساب نیست!**
> vhost هر حساب cPanel روی **IP اشتراکی خودش** bind می‌شود (در این هاست: `85.198.13.88` — از PTR هم مشخص است: `mail.client.nitrocpanel.ir`).
> مرجع قطعی: cPanel → **General Information → Shared IP Address**. اگر DNS به IP اشتباه اشاره کند، همیشه ۴۰۴ می‌گیرید
> حتی وقتی اپ کاملاً سالم است (دقیقاً همان چیزی که برای ما اتفاق افتاد!).

1. **Zone Editor** (cPanel → Domains → Zone Editor): اگر زون `kaveh185.n-cpanel.xyz` هست،
   رکورد **A** اضافه کنید: مقدار = **Shared IP** (`85.198.13.88`).
2. اگر زون نبود: **دامنه‌ی رایگان `nip.io`** — هر زیردامنه‌ی شکل-IP به همان IP resolve می‌شود:
   `85.198.13.88.nip.io` را در cPanel → Domains → Create a New Domain اضافه کنید،
   Application URL را روی آن بگذارید، سپس **SSL/TLS Status → Run AutoSSL**.
3. راه پایدار بلندمدت: دامنه‌ی اختصاصی + AutoSSL.
4. نکته‌ی AutoSSL: صدور Let's Encrypt نیاز به تأیید مالکیت (DCV) دارد؛ اگر اعتبارسنجی HTTP از خارجِ ایران
   به سرور نرسد، cPanel برای زون‌های روی NS خود هاست معمولاً **DCV از طریق DNS** را جایگزین می‌کند؛
   اگر AutoSSL خطا داد، خروجی «SSL/TLS Status → Certificate Status» را بفرستید.

## ⏰ سناریوی پنجم: انحراف ساعت سرور (Clock Skew)
اگر در صفحه‌ی خطای سرور تاریخ میلادی متفاوتی با امروز دیدید (مثلاً چند روز جلو)، یعنی **ساعت میزبان منحرف است**.
این برای تأیید HMAC وب‌هوک BoxAPI (پنجره‌ی ۳۰۰ ثانیه) کشنده است: همه‌ی رویدادها reject می‌شوند.
`doctor` انحراف را در ردیف «ساعت سرور» گزارش می‌کند. رفع (بدون تیکت — ساعت هاست اشتراکی قابل تغییر نیست):

```bash
cd /home/kavehncp/peyja
echo 'WEBHOOK_TOLERANCE_SECONDS=604800' >> .env   # ۷ روز؛ ≥ انحراف + ۳۰۰
```

سپس Setup Python App → **Restart**. هزینه‌ی امنیتی: بازپخش رویدادهای قدیمی در همان بازه ممکن می‌شود؛
در production با سرور اختصاصی، ساعت را با `chrony` همگام و مقدار را به ۳۰۰ برگردانید.

## سناریوی ششم: زون درست، ولی «تفویض» در زون مادر خراب (تجربه‌ی واقعی — مهم‌ترین درس)
علائم: Zone Editor زون دامنه را با رکورد A درست نشان می‌دهد، ولی دامنه در DNS عمومی **NXDOMAIN** است.
تشخیص قطعی (بدون تیکت — با کوئری خام UDP به خود سرور DNS مرجع):

```python
# کوئری مستقیم به NS مرجع زون مادر — اگر NXDOMAIN داد یعنی تفویض (NS) برای زیردامنه وجود ندارد
# (نمونه‌ی واقعی: زون مادر n-cpanel.xyz روی ns1.85-158-145-202.cprapid.com میزبانی می‌شود
#  اما زون مشتری روی ir1/ir2.main-dns-cloud.xyz (= IP وب خود حساب) — و تفویض بین این دو ثبت نشده)
```

- **رکورد A شما ممکن است بی‌نقص باشد** (`kaveh185.n-cpanel.xyz.` ← IP اشتراکی)؛ مقصر فقط نبودِ
  رکوردهای NS (تفویض) در زون مادر است که **فقط مدیر هاست** می‌تواند اضافه کند.
- رکوردهای NS داخل زونِ خودتان (مثل `ir1/ir2.main-dns-cloud.xyz`) را با NSهای ثبت‌شده در **رجیستریِ دامنه‌ی مادر** مقایسه کنید؛
  اگر متفاوت باشند و تفویضی در کار نباشد، هیچ‌کس در دنیا به سرور DNS شما نمی‌رسد.
- **دامنه‌ی رایگان هاستی (`*.n-cpanel.xyz`) با این ساختار قابل‌رفع نیست** ← مسیر پایدار:
  دامنه‌ی اختصاصی با NS = `ir1.main-dns-cloud.xyz` / `ir2.main-dns-cloud.xyz` (مقدارها در همان صفحه‌ی Zone Editor).

### خطای XID هنگام افزودن دامنه‌ی nip.io
cPanel هنگام «Create a New Domain» (حالت Registered Domain) بررسی می‌کند NS دامنه به سرور خودش اشاره کند؛
`nip.io`/`sslip.io` این شرط را رد می‌کنند. راه‌های دور زدن:
1. رادیوی **«Choose a domain later»** (دامنه‌ی موقت) — بدون بررسی NS؛ نام موقتِ ساخته‌شده معمولاً
   از DNS خود هاست resolve می‌شود (برای تست/راه‌اندازی اولیه عالی است).
2. دامنه‌ی اختصاصی با NSهای `ir1/ir2.main-dns-cloud.xyz` ← بررسی cPanel پاس می‌شود + AutoSSL
   با DCV از DNS (ثبت TXT روی همان سرور DNS) کار می‌کند — حتی اگر HTTP از خارج مسدود باشد.
3. در production جلوی همه‌چیز **Cloudflare (رایگان)** بگذارید — دقیقاً الگوی خودِ سایت هاست (`nitrocpanel.ir`).

## سناریوی هفتم: صفحه‌ی «428 Technical Domain» روی دامنه‌های موقت cpanel.site
دامنه‌های موقت (`*.cpanel.site`) در مرورگر یک صفحه‌ی هشدار cPanel نشان می‌دهند
(«این دامنه فنی است و هرگز برای سایت‌های زنده استفاده نمی‌شود»). رفع برای انسان:
تیک «Dismiss this warning for 90 days» + دکمه‌ی **Continue** (URL و query حفظ می‌شود).
نکته‌ی حیاتی: این هشدار مخصوص **مرورگرها** است؛ ترافیک سرور-به-سرور (curl / وب‌هوک BoxAPI)
باید مستقیم به اپ برسد — با آزمون POST با امضای نادرست تأیید کنید (انتظار: JSON خطای ۴۰۱ اپ، نه HTML).
برای production هرگز روی دامنه‌ی موقت نمانید ← دامنه‌ی اختصاصی + AutoSSL (سناریوی ۶).

## نکات معماری
- Passenger فقط WSGI می‌فهمد ← `a2wsgi.ASGIMiddleware` در `passenger_wsgi.py` پل ASGI است.
- دیتابیس: SQLite (فایل `data/app.db` داخل ریشه‌ی اپ) — مناسب هاست اشتراکی؛ بعداً Postgres.
- بعد از هر `git pull` روی هاست: `pip install -e .` (اگر وابستگی عوض شده) + **Restart**.
- ⚠️ **قانون فرمان‌ها:** هر نشست SSH تازه = venv غیرفعال؛ اگر پرامپت `(peyja:3.12)` را نمی‌بینید،
  `python` در دسترس نیست (`command not found`) و ممکن است دیتابیس اشتباه (از `~`) خوانده شود.
  **راه بدون‌خطا:** فقط اسکریپت واحد را بزنید (فعال‌سازی venv و `cd` خودکار):
  `bash /home/kavehncp/peyja/scripts/peyja.sh doctor` — و مثلاً
  `bash /home/kavehncp/peyja/scripts/peyja.sh selftest-webhook --url https://<دامنه>/webhooks/boxapi`.
