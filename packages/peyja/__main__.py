"""نقطه‌ی ورود رسمی CLI پیجا: ``python -m peyja <فرمان>``.

چرا این فایل وجود دارد: «python -m peyja» قرارداد ورودی ماست، اما بدون
``__main__.py`` این فرمان با «No module named peyja.__main__» می‌مُرد (تجربه‌ی
واقعی روی هاست — درس ۱۴۰۵/۰۹/۰۵). این shim:

1. مسیر ریشه‌ی مخزن را به sys.path تزریق می‌کند تا از «هر» دایرکتوری‌ای کار کند؛
2. به CLI تایپر (``apps.cli.main:app``) واگذار می‌کند — همان مسیری که
   ``scripts/peyja.sh`` و کنسول‌اسکریپت ``peyja`` می‌روند (ورودی واحد).

رگرسیون این قرارداد در ``tests/test_cli_entry.py`` قفل شده است.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

try:
    from apps.cli.main import app
except ImportError as exc:  # pragma: no cover - فقط در نصبِ خراب رخ می‌دهد
    sys.stderr.write(
        "❌ ماژول CLI پیجا در دسترس نیست "
        f"({exc}).\n"
        "راهنما: venv را فعال و بسته را نصب کنید:\n"
        "  source /home/kavehncp/virtualenv/peyja/3.12/bin/activate && "
        "pip install -e '/home/kavehncp/peyja'\n"
    )
    raise SystemExit(1) from exc

if __name__ == "__main__":
    app()
