"""قراردادهای ساخت‌یافته‌ی استخراج (Structured Outputs — قله‌ی تکنولوژی ۲۰۲۶).

الگوها (از پژوهش روز دنیا):
- SafeExtraction با «مسیر فرار صریح»: extraction_successful + failure_reason
  (مدل مجبور نیست حدس بزند؛ اگر مطمئن نیست صادقانه شکست می‌خورد)
- Optional بودن فیلدهای نایاب + Field(description=...) برای راهنمایی مدل
- confidence با بازه‌ی معتبر [0,1] + اعتبارسنجی قواعد کسب‌وکار (بازه‌ی قیمت)
- اسکیمای flat (حداکثر ۳ سطح) — دقت بالاتر در decoding
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

# دسته‌بندی لوازم آرایشی بهداشتی (نیچ جدید پیجا)
Category = Literal["skincare", "makeup", "perfume", "haircare", "hygiene", "other"]
StockState = Literal["in_stock", "out_of_stock", "on_request", "unknown"]

MIN_PRICE_TOMAN = 1_000
MAX_PRICE_TOMAN = 5_000_000_000


class SafeProductExtraction(BaseModel):
    """اسکیمای امن استخراج محصول از کپشن/تصویر — با مسیر فرار."""

    extraction_successful: bool = Field(
        default=True,
        description="آیا استخراج موفق بود؟ اگر اطلاعات کافی نیست false بگذار",
    )
    failure_reason: str | None = Field(
        default=None, description="در صورت شکست، دلیل کوتاه"
    )
    title_fa: str | None = Field(default=None, description="عنوان کوتاه فارسی محصول")
    category: Category | None = Field(default=None, description="دسته‌ی محصول")
    brand: str | None = Field(default=None, description="برند (فارسی یا لاتین)")
    colors: list[str] = Field(default_factory=list, description="رنگ‌ها/شیدها")
    sizes: list[str] = Field(default_factory=list, description="سایزها/حجم‌ها")
    volume_ml: float | None = Field(
        default=None, description="حجم به میلی‌لیتر اگر ذکر شده"
    )
    spf: int | None = Field(default=None, description="ضدآفتاب: SPF اگر ذکر شده")
    price_toman: int | None = Field(
        default=None, description="قیمت فعلی به تومان کامل (مثلا 4800000)"
    )
    old_price_toman: int | None = Field(
        default=None, description="قیمت قبل از تخفیف به تومان"
    )
    stock_state: StockState = Field(default="unknown", description="وضعیت موجودی")
    confidence: float = Field(
        default=0.0, ge=0.0, le=1.0, description="اطمینان مدل بین ۰ تا ۱"
    )

    @model_validator(mode="after")
    def _business_rules(self) -> SafeProductExtraction:
        if not self.extraction_successful:
            if not self.failure_reason:
                raise ValueError(
                    "اگر extraction_successful نیست، failure_reason الزامی است"
                )
            return self
        for label, v in (
            ("price_toman", self.price_toman),
            ("old_price_toman", self.old_price_toman),
        ):
            if v is not None and not (MIN_PRICE_TOMAN <= v <= MAX_PRICE_TOMAN):
                raise ValueError(f"{label}={v} خارج از بازه‌ی معقول است")
        if (
            self.price_toman is not None
            and self.old_price_toman is not None
            and self.old_price_toman < self.price_toman
        ):
            raise ValueError("قیمت قدیم نمی‌تواند کمتر از قیمت فعلی باشد")
        return self


# بازه‌بندی اطمینان — مسیریابی انسانی (Human-in-the-Loop)
CONFIDENCE_VERIFIED = 0.7  # ≥ ← ذخیره‌ی خودکار
# بین [review_floor, verified) ← صف تأیید انسانی
# < review_floor ← رد + لاگ شکست


def route_by_confidence(
    confidence: float, review_floor: float = 0.4
) -> Literal["store", "review", "reject"]:
    if confidence >= CONFIDENCE_VERIFIED:
        return "store"
    if confidence >= review_floor:
        return "review"
    return "reject"
