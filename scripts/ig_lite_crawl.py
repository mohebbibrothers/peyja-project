#!/usr/bin/env python3
"""اسکریپت مستقل و بسیار سبک جمع‌آوری پست‌های اینستاگرام (فقط برای بچ ۲ تست با اکانت تستی).

استفاده:
  python scripts/ig_lite_crawl.py --sessionid "<کوکی sessionid مرورگر>" --out out/ig-batch2-raw.json
  # یا با ورود وب:
  python scripts/ig_lite_crawl.py --username my_test_acc --out out/ig-batch2-raw.json
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "packages"))
sys.path.insert(0, str(ROOT))

from peyja.ingest.ig_lite import (  # noqa: E402
    DEFAULT_IG_SHOPS,
    IgLiteAuthError,
    IgLiteCrawler,
    dump_raw_json,
)


async def _run(args: argparse.Namespace) -> int:
    shops = [s.strip() for s in args.accounts.split(",") if s.strip()]
    sessionid = (args.sessionid or os.environ.get("IG_SESSION_ID") or "").strip()
    crawler = IgLiteCrawler(
        sessionid=sessionid,
        csrftoken=(args.csrftoken or os.environ.get("IG_CSRF_TOKEN") or "").strip(),
    )
    try:
        if not sessionid:
            if not args.username:
                print("❌ یا --sessionid بدهید یا --username (ترجیح امن: کوکی sessionid مرورگر).")
                return 2
            pw = args.password or getpass.getpass(f"رمز عبور اینستاگرام (@{args.username}): ")
            print(f"🔐 ورود سبک با اکانت @{args.username} ...")
            try:
                await crawler.login_web(args.username, pw)
            except IgLiteAuthError as e:
                print(f"❌ {e}")
                return 1

        print(f"📥 شروع کرال سبک روی {len(shops)} فروشگاه (هر پیج حداکثر {args.per_shop} پست)...")
        res = await crawler.crawl_shops(shops, per_shop=args.per_shop, max_total=args.max_total)
        out_path = dump_raw_json(res["items"], Path(args.out))
        print(
            json.dumps(
                {k: v for k, v in res.items() if k != "items"},
                ensure_ascii=False,
                indent=2,
            )
        )
        print(f"💾 خروجی ذخیره شد: {out_path}")
        return 0 if res["count"] > 0 else 1
    finally:
        await crawler.aclose()


def main() -> None:
    p = argparse.ArgumentParser(description="کرالر بسیار سبک اینستاگرام برای بچ ۲ پیجا")
    p.add_argument("--sessionid", default="", help="کوکی sessionid از مرورگر (امن‌ترین روش)")
    p.add_argument("--csrftoken", default="", help="کوکی csrftoken (اختیاری)")
    p.add_argument("--username", default="", help="نام کاربری اکانت تستی (در صورت نبود sessionid)")
    p.add_argument("--password", default="", help="رمز عبور (خالی = پرسش امن در ترمینال)")
    p.add_argument(
        "--accounts",
        default=",".join(DEFAULT_IG_SHOPS),
        help="فهرست پیج‌های فروشگاه‌های هدف با کاما",
    )
    p.add_argument("--per-shop", type=int, default=8, help="تعداد پست از هر پیج (پیش‌فرض: ۸)")
    p.add_argument("--max-total", type=int, default=60, help="سقف کل پست‌ها (پیش‌فرض: ۶۰)")
    p.add_argument("--out", default="out/ig-batch2-raw.json", help="مسیر فایل خروجی JSON")
    raise SystemExit(asyncio.run(_run(p.parse_args())))


if __name__ == "__main__":
    main()
