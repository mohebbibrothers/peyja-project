"""ارزیابی extraction روی gold-100 — «عدد سرنوشت».

لایه‌ی نازک CLI؛ منطق واقعی در peyja/evals.py (تست‌پذیر).

استفاده:
  python evals/score.py --gold evals/gold-100/samples.json --pred out/predictions.json

گیت‌های موفقیت Phase 1:
  - قیمت (خطای ≤۲٪):   ≥ ۸۵٪
  - دسته:              ≥ ۷۵٪
  - ویژگی (رنگ/سایز):  ≥ ۷۵٪
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "packages"))

from peyja.evals import score


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gold", required=True)
    ap.add_argument("--pred", required=True)
    args = ap.parse_args()
    gold = json.loads(Path(args.gold).read_text(encoding="utf-8"))
    preds = {p["id"]: p for p in json.loads(Path(args.pred).read_text(encoding="utf-8"))}
    result = score(gold, preds)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
