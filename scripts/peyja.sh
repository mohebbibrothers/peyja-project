#!/usr/bin/env bash
# ── ورودی واحد و بدون‌خطای CLI پیجا ─────────────────────────────────────
# فعال‌سازی venv و ورود به ریشه‌ی اپ را «خودکار» می‌کند تا هیچ‌وقت
# خطای «python: command not found» یا دیتابیس اشتباه (اجرای از home) پیش نیاید.
#
# استفاده (از هر جای هاست):
#   bash /home/kavehncp/peyja/scripts/peyja.sh doctor
#   bash /home/kavehncp/peyja/scripts/peyja.sh selftest-webhook --url https://<دامنه>/webhooks/boxapi
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# اولویت: PEYJA_VENV (دستی) ← مسیر استاندارد هاست cPanel ← venv محلی پروژه
for act in "${PEYJA_VENV:-}" \
           /home/kavehncp/virtualenv/peyja/3.12/bin/activate \
           "$REPO_ROOT/.venv/bin/activate"; do
  if [ -n "$act" ] && [ -f "$act" ]; then
    # shellcheck disable=SC1090
    source "$act"
    break
  fi
done

cd "$REPO_ROOT"   # مهم: دیتابیس/داده‌ها نسبی‌اند؛ اجرا باید از ریشه‌ی اپ باشد
exec python -m apps.cli.main "$@"
