"""مدل‌های نرمال‌شده — قرارداد مشترک همه‌ی Providerها."""

from __future__ import annotations

from typing import Any, Protocol

from pydantic import BaseModel, Field


class IgProfile(BaseModel):
    """پروفایل نرمال‌شده‌ی اینستاگرام."""

    ig_user_id: str
    username: str
    full_name: str | None = None
    biography: str | None = None
    follower_count: int | None = None
    following_count: int | None = None
    media_count: int | None = None
    is_private: bool = False
    is_verified: bool = False
    profile_pic_url: str | None = None
    external_url: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


class IgMedia(BaseModel):
    """پست/رسانه‌ی نرمال‌شده."""

    ig_media_id: str
    media_type: str = "unknown"  # image | video | carousel | unknown
    caption: str | None = None
    like_count: int | None = None
    comment_count: int | None = None
    view_count: int | None = None
    permalink: str | None = None
    media_urls: list[str] = Field(default_factory=list)
    taken_at: int | None = None  # unix seconds
    code: str | None = None  # shortcode
    raw: dict[str, Any] = Field(default_factory=dict)


class IgComment(BaseModel):
    ig_comment_id: str
    ig_media_id: str | None = None
    text: str | None = None
    username: str | None = None
    ig_user_id: str | None = None
    created_at: int | None = None
    like_count: int | None = None
    raw: dict[str, Any] = Field(default_factory=dict)


class DataProvider(Protocol):
    """قرارداد مسیر «داده‌ی عمومی» (بدون اتصال پیج)."""

    async def get_user_by_username(self, username: str) -> IgProfile: ...

    async def get_media_page(
        self, ig_user_id: str, count: int = 12, max_id: str | None = None
    ) -> tuple[list[IgMedia], str | None]:
        """یک صفحه پست + cursor صفحه‌ی بعد (next_max_id)."""
        ...

    async def get_comments(self, ig_media_id: str) -> list[IgComment]: ...
