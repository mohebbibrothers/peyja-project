# راهنمای استقرار روی هاست cPanel (پارس‌هاست) — مقاوم در برابر همه‌ی حالت‌ها

## چرا `python3: command not found`؟
هاست‌های اشتراکی CloudLinux/cPanel پایتون را در PATH عمومی قرار نمی‌دهند (قفس CageFS).
پایتون در مسیرهای جایگزین است یا از طریق **Setup Python App** (Passenger) ارائه می‌شود.

## گام ۰ — کشف محیط (این بلوک را کامل اجرا و خروجی را بفرستید)

```bash
echo "PATH=$PATH"
for c in python python3 python3.9 python3.10 python3.11 python3.12 git curl wget unzip; do
  p=$(command -v $c 2>/dev/null); echo "$c => ${p:-NOT FOUND}"
done
ls /opt/alt/ 2>/dev/null
ls /opt/cpanel/3rdparty/bin/ 2>/dev/null | grep -i python
ls /usr/local/cpanel/3rdparty/bin/ 2>/dev/null | grep -i python
```

## گام ۱ — فعال‌سازی پایتون (هر سناریو که خروجی گام ۰ نشان دهد)

### سناریو A — `/opt/alt/python3XX` دیده شد (رایج‌ترین)
```bash
export PATH=/opt/alt/python311/usr/bin:$PATH   # اگر 312 داشتید، python312
python3 --version
```

### سناریو B — هیچ python نبود
از cPanel: **Tools → Setup Python App → Create Application**
- Python version: بالاترین موجود (ترجیحاً ≥۳.۱۱)
- Application root: `/home/kavehncp/peyja`
- Application startup file: `passenger_wsgi.py`
- Entry point: `application`
پس از Create، همان صفحه دستور فعال‌سازی virtualenv را نشان می‌دهد (مثل
`source /home/kavehncp/virtualenv/peyja/3.11/bin/activate`) — آن را در ترمینال اجرا کنید.

### سناریو C — git نبود (دانلود بدون git)
```bash
cd ~
curl -L -H "Authorization: Bearer <YOUR_GITHUB_TOKEN>" \
  -o peyja.zip https://api.github.com/repos/mohebbibrothers/peyja-project/zipball/main
unzip -q peyja.zip && mv mohebbibrothers-peyja-project-* peyja && rm peyja.zip
```

## گام ۲ — نصب (بعد از فعال شدن python)

```bash
cd ~/peyja   # یا مسیری که کد را ریختید
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -e .
```

## گام ۳ — تنظیمات (`cat > .env <<'EOF' ... EOF` طبق سند progress-report)

## گام ۴ — معاینه
```bash
python -m apps.cli.main doctor
```

## گام ۵ — استقرار وب (Setup Python App)
- اگر اپ را در گام ۱-B ساختید: فقط **Reload** کنید.
- وگرنه حالا با مقادیر فوق بسازید.
- آزمون: `curl https://kaveh185.n-cpanel.xyz/health` → `{"status":"ok"...}`

## گام ۶ — وب‌هوک در داشبورد BoxAPI
`https://kaveh185.n-cpanel.xyz/webhooks/boxapi` + سکرت `whsec_...` (همان `.env`)

## عیب‌یابی سریع
| خطا | راه‌حل |
|---|---|
| `python3: command not found` | گام ۱ (PATH یا Setup Python App) |
| `pip: command not found` | مطمئن شوید venv فعال است (`source .venv/bin/activate`) |
| ۵۰۲ در سایت | لاگ Passenger را در Setup Python App ببینید؛ معمولاً خطای import یا نسخه‌ی پایتون |
| `no such table` | `python -m apps.cli.main doctor` را یک بار اجرا کنید (ساخت خودکار جداول) |
| وب‌هوک ۴۰۱ | سکرت یا URL اشتباه — باید دقیقاً یکی باشند |
