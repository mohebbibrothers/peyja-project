"""schema v1 — «حداقلی ولی درست» (از سند FINAL-PROPOSAL).

سه ستونی که هرگز نباید فراموش شوند:
  - evidence.confidence        (بدون آن صف تأیید ساخته نمی‌شود)
  - pages.sync_cursor          (ادامه‌ی pagination از همان‌جا که ماندیم)
  - raw_items (آرشیو خام)      (بدون آن هرگز نمی‌فهمیم چرا extraction اشتباه کرد)
"""

from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# قابل‌حمل: روی Postgres ← JSONB (GIN-indexable)؛ روی SQLite ← JSON
JSONType = JSON().with_variant(JSONB(), "postgresql")


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.UTC)


class Base(DeclarativeBase):
    pass


class Page(Base):
    """هر پیج اینستاگرام (منبع عمومی یا متصل‌شده)."""

    __tablename__ = "pages"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    ig_user_id: Mapped[str | None] = mapped_column(String(32), index=True)
    source: Mapped[str] = mapped_column(String(16), default="data")  # data | official
    account_id: Mapped[str | None] = mapped_column(
        String(64)
    )  # UUID پیج متصل (BoxAPI official)
    sync_cursor: Mapped[dict[str, Any] | None] = mapped_column(
        JSONType
    )  # {"next_max_id": ...}
    last_synced_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))

    raw_items: Mapped[list[RawItem]] = relationship(
        back_populates="page", cascade="all, delete-orphan"
    )
    products: Mapped[list[Product]] = relationship(
        back_populates="page", cascade="all, delete-orphan"
    )


class RawItem(Base):
    """آرشیو خام هر چیزی که از API آمده — منبع حقیقت اولیه برای reprocessing."""

    __tablename__ = "raw_items"
    __table_args__ = (
        UniqueConstraint("page_id", "kind", "ig_id", name="uq_raw_identity"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    page_id: Mapped[int] = mapped_column(
        ForeignKey("pages.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(
        String(16)
    )  # media | comment | profile | highlight | story
    ig_id: Mapped[str] = mapped_column(String(40), index=True)
    raw: Mapped[dict[str, Any]] = mapped_column(JSONType)
    fetched_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )

    page: Mapped[Page] = relationship(back_populates="raw_items")
    media_files: Mapped[list[MediaFile]] = relationship(
        back_populates="raw_item", cascade="all, delete-orphan"
    )


class MediaFile(Base):
    """رسانه‌های دانلودشده — ⚠️ هرگز فقط media_url (منقضی‌شونده) نگه نمی‌داریم."""

    __tablename__ = "media_files"

    id: Mapped[int] = mapped_column(primary_key=True)
    raw_item_id: Mapped[int] = mapped_column(
        ForeignKey("raw_items.id", ondelete="CASCADE"), index=True
    )
    url_original: Mapped[str] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    local_path: Mapped[str] = mapped_column(Text)
    downloaded_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )

    raw_item: Mapped[RawItem] = relationship(back_populates="media_files")


class CaptionVersion(Base):
    """تاریخچه‌ی کپشن = تاریخچه‌ی قیمت — یک دارایی داده‌ای."""

    __tablename__ = "caption_versions"

    id: Mapped[int] = mapped_column(primary_key=True)
    raw_item_id: Mapped[int] = mapped_column(
        ForeignKey("raw_items.id", ondelete="CASCADE"), index=True
    )
    caption_text: Mapped[str | None] = mapped_column(Text)
    captured_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )


class Product(Base):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(primary_key=True)
    page_id: Mapped[int] = mapped_column(
        ForeignKey("pages.id", ondelete="CASCADE"), index=True
    )
    title_fa: Mapped[str] = mapped_column(Text)
    category: Mapped[str | None] = mapped_column(String(64))
    attributes: Mapped[dict[str, Any] | None] = mapped_column(
        JSONType
    )  # رنگ/سایز/جنس/...
    status: Mapped[str] = mapped_column(
        String(16), default="candidate"
    )  # candidate|confirmed|rejected

    page: Mapped[Page] = relationship(back_populates="products")
    variants: Mapped[list[Variant]] = relationship(
        back_populates="product", cascade="all, delete-orphan"
    )


class Variant(Base):
    __tablename__ = "variants"

    id: Mapped[int] = mapped_column(primary_key=True)
    product_id: Mapped[int] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), index=True
    )
    sku: Mapped[str | None] = mapped_column(String(64))
    options: Mapped[dict[str, Any] | None] = mapped_column(
        JSONType
    )  # {"سایز": "42", "رنگ": "مشکی"}

    product: Mapped[Product] = relationship(back_populates="variants")
    offers: Mapped[list[Offer]] = relationship(
        back_populates="variant", cascade="all, delete-orphan"
    )


class Offer(Base):
    """قیمت/موجودی — همیشه با Evidence و TTL."""

    __tablename__ = "offers"

    id: Mapped[int] = mapped_column(primary_key=True)
    variant_id: Mapped[int] = mapped_column(
        ForeignKey("variants.id", ondelete="CASCADE"), index=True
    )
    price_minor: Mapped[int | None] = mapped_column(BigInteger)  # تومان
    currency: Mapped[str] = mapped_column(String(8), default="IRR")
    stock_state: Mapped[str] = mapped_column(
        String(16), default="unknown"
    )  # in_stock|out_of_stock|on_request|unknown
    valid_from: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    ttl_hours: Mapped[int] = mapped_column(Integer, default=24)
    stale: Mapped[bool] = mapped_column(Boolean, default=False)

    variant: Mapped[Variant] = relationship(back_populates="offers")
    evidence: Mapped[list[Evidence]] = relationship(
        back_populates="offer", cascade="all, delete-orphan"
    )


class Evidence(Base):
    """«استخراج‌شده ≠ تأییدشده» — مدرک هر ادعا."""

    __tablename__ = "evidence"

    id: Mapped[int] = mapped_column(primary_key=True)
    offer_id: Mapped[int | None] = mapped_column(
        ForeignKey("offers.id", ondelete="CASCADE"), index=True
    )
    product_id: Mapped[int | None] = mapped_column(
        ForeignKey("products.id", ondelete="CASCADE"), index=True
    )
    source_type: Mapped[str] = mapped_column(
        String(24)
    )  # caption|ocr|vlm|seller_echo|comment
    source_id: Mapped[str | None] = mapped_column(String(64))  # ig_id مرجع
    method: Mapped[str] = mapped_column(String(48))
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    extracted_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    verified_by: Mapped[str | None] = mapped_column(String(64))
    verified_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))

    offer: Mapped[Offer | None] = relationship(back_populates="evidence")


class ExtractionRun(Base):
    __tablename__ = "extraction_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    page_id: Mapped[int] = mapped_column(
        ForeignKey("pages.id", ondelete="CASCADE"), index=True
    )
    pipeline_version: Mapped[str] = mapped_column(String(32))
    stats: Mapped[dict[str, Any] | None] = mapped_column(JSONType)
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))


class LabeledSample(Base):
    """داده‌ی طلایی ارزیابی (gold set) — «عدد سرنوشت» از اینجا می‌آید."""

    __tablename__ = "labeled_samples"

    id: Mapped[int] = mapped_column(primary_key=True)
    raw_item_id: Mapped[int] = mapped_column(
        ForeignKey("raw_items.id", ondelete="CASCADE"), unique=True
    )
    gold: Mapped[dict[str, Any]] = mapped_column(
        JSONType
    )  # {"price_toman":..., "colors":[...], ...}
    labeled_by: Mapped[str | None] = mapped_column(String(64))


class WebhookEvent(Base):
    """رویدادهای وب‌هوک — برای حذف تکراری‌ها (dedup با event_id)."""

    __tablename__ = "webhook_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[str] = mapped_column(String(96), unique=True, index=True)
    account_id: Mapped[str | None] = mapped_column(String(64), index=True)
    event_type: Mapped[str] = mapped_column(String(48))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONType)
    received_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
    processed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))


class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[str] = mapped_column(String(64), index=True)
    ig_user_id: Mapped[str] = mapped_column(String(32), index=True)
    state: Mapped[str] = mapped_column(String(16), default="open")
    window_expires_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True)
    )

    messages: Mapped[list[Message]] = relationship(
        back_populates="conversation", cascade="all, delete-orphan"
    )


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(
        ForeignKey("conversations.id", ondelete="CASCADE"), index=True
    )
    direction: Mapped[str] = mapped_column(String(8))  # in|out
    text: Mapped[str | None] = mapped_column(Text)
    mid: Mapped[str | None] = mapped_column(String(64), index=True)
    sent_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )

    conversation: Mapped[Conversation] = relationship(back_populates="messages")
