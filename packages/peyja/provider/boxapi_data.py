"""کلاینت خط ② — BoxAPI Instagram Data API (داده‌ی عمومی، ۳۵ اندپوینت).

الگو: POST https://boxapi.ir/api/instagram/{category}/{action} با Bearer token.
Rate limit: ۱۰ ریکوئست/ثانیه ⇒ RateLimiter توکنی + Backoff نمایی برای خطاهای گذرا.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from peyja.config import get_settings
from peyja.provider.base import IgComment, IgMedia, IgProfile


class TransientApiError(Exception):
    """خطای گذرا (۵xx / timeout) — قابل retry."""


class FatalApiError(Exception):
    """خطای دائمی (۴xx / توکن نامعتبر) — retry ممنوع."""


def _is_transient(exc: BaseException) -> bool:
    return isinstance(exc, (TransientApiError, httpx.TransportError))


class RateLimiter:
    """محدودکننده‌ی نرخ ساده‌ی توکنی (N درخواست در ثانیه)."""

    def __init__(self, rate_per_sec: float = 9.0):  # کمی پایین‌تر از سقف ۱۰ (حاشیه ایمنی)
        self._interval = 1.0 / rate_per_sec
        self._lock = asyncio.Lock()
        self._next_at = 0.0

    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()
            wait = self._next_at - now
            self._next_at = max(now, self._next_at) + self._interval
        if wait > 0:
            await asyncio.sleep(wait)


class BoxApiDataProvider:
    """کلاینت API دیتا — منبع ایندکس بازار و استخراج کاتالوگ."""

    def __init__(self, token: str | None = None, base_url: str | None = None):
        settings = get_settings()
        self._token = token or settings.boxapi_data_token
        self._base_url = (base_url or settings.boxapi_data_url).rstrip("/")
        self._limiter = RateLimiter()
        self._client = httpx.AsyncClient(timeout=30.0)

    async def aclose(self) -> None:
        await self._client.aclose()

    # ----- هسته‌ی فراخوانی -----
    async def _call(self, category: str, action: str, params: dict[str, Any]) -> dict[str, Any]:
        await self._limiter.acquire()
        url = f"{self._base_url}/api/instagram/{category}/{action}"
        headers = {"Authorization": f"Bearer {self._token}", "Content-Type": "application/json"}

        @retry(
            retry=retry_if_exception(_is_transient),
            wait=wait_exponential(multiplier=1, min=1, max=30),
            stop=stop_after_attempt(4),
            reraise=True,
        )
        async def _do() -> dict[str, Any]:
            resp = await self._client.post(url, headers=headers, json=params)
            if resp.status_code in (429,) or resp.status_code >= 500:
                raise TransientApiError(f"HTTP {resp.status_code}: {resp.text[:200]}")
            if resp.status_code >= 400:
                raise FatalApiError(f"HTTP {resp.status_code}: {resp.text[:200]}")
            data = resp.json()
            if data.get("status") != "done":
                # خطاهای منطقی سرویس — اگر قابل‌تشخیصِ گذرا بود، بالا می‌ریزد
                msg = str(data)[:200]
                if "rate" in msg.lower():
                    raise TransientApiError(msg)
                raise FatalApiError(msg)
            return data

        return await _do()

    @staticmethod
    def _body(data: dict[str, Any]) -> dict[str, Any]:
        body = data.get("response", {}).get("body", {}) or {}
        if isinstance(body, str):
            import json

            try:
                body = json.loads(body)
            except ValueError:
                return {}
        return body

    # ----- User -----
    async def get_user_by_username(self, username: str) -> IgProfile:
        data = await self._call("user", "get_web_profile_info", {"username": username})
        u = (self._body(data).get("data", {}) or {}).get("user", {}) or {}
        if not u:
            # fallback به اندپوینت اصلی
            data = await self._call("user", "get_info_by_username", {"username": username})
            u = self._body(data).get("user", {}) or self._body(data).get("data", {}) or {}
        return IgProfile(
            ig_user_id=str(u.get("pk") or u.get("id") or ""),
            username=u.get("username", username),
            full_name=u.get("full_name"),
            biography=u.get("biography"),
            follower_count=(u.get("edge_followed_by") or {}).get("count")
            or u.get("follower_count"),
            following_count=(u.get("edge_follow") or {}).get("count") or u.get("following_count"),
            media_count=(u.get("edge_owner_to_timeline_media") or {}).get("count")
            or u.get("media_count"),
            is_private=bool(u.get("is_private")),
            is_verified=bool(u.get("is_verified")),
            profile_pic_url=u.get("profile_pic_url_hd") or u.get("profile_pic_url"),
            external_url=u.get("external_url"),
            raw=u,
        )

    async def get_info_by_id(self, ig_user_id: str) -> IgProfile:
        data = await self._call("user", "get_info_by_id", {"id": ig_user_id})
        u = self._body(data).get("user", {}) or self._body(data).get("data", {}) or {}
        return IgProfile(
            ig_user_id=str(u.get("pk") or ig_user_id),
            username=u.get("username", ""),
            full_name=u.get("full_name"),
            biography=u.get("biography"),
            follower_count=(u.get("follower_count") if "follower_count" in u else None),
            is_private=bool(u.get("is_private")),
            is_verified=bool(u.get("is_verified")),
            profile_pic_url=u.get("profile_pic_url"),
            raw=u,
        )

    # ----- Media -----
    @staticmethod
    def _normalize_media(item: dict[str, Any]) -> IgMedia:
        mt = item.get("media_type")
        media_type = (
            {1: "image", 2: "video", 8: "carousel"}.get(mt, "unknown")
            if isinstance(mt, int)
            else "unknown"
        )
        urls: list[str] = []
        if item.get("image_versions2", {}).get("candidates"):
            urls = [c["url"] for c in item["image_versions2"]["candidates"] if c.get("url")]
        if item.get("video_versions"):
            urls += [v["url"] for v in item["video_versions"] if v.get("url")]
        if item.get("media_url"):
            urls.append(item["media_url"])
        carousel = item.get("carousel_media") or []
        for sub in carousel:
            if sub.get("image_versions2", {}).get("candidates"):
                urls += [c["url"] for c in sub["image_versions2"]["candidates"] if c.get("url")]
        caption = item.get("caption")
        if isinstance(caption, dict):
            caption = caption.get("text")
        return IgMedia(
            ig_media_id=str(item.get("pk") or item.get("id") or ""),
            media_type=media_type,
            caption=caption,
            like_count=item.get("like_count"),
            comment_count=item.get("comment_count"),
            view_count=item.get("view_count"),
            permalink=(
                f"https://www.instagram.com/p/{item['code']}/" if item.get("code") else None
            ),
            media_urls=list(dict.fromkeys(urls)),  # حذف تکراری حفظ ترتیب
            taken_at=item.get("taken_at"),
            code=item.get("code"),
            raw=item,
        )

    async def get_media_page(
        self, ig_user_id: str, count: int = 12, max_id: str | None = None
    ) -> tuple[list[IgMedia], str | None]:
        params: dict[str, Any] = {"id": ig_user_id, "count": min(count, 12)}
        if max_id:
            params["max_id"] = max_id
        data = await self._call("user", "get_media", params)
        body = self._body(data)
        items = body.get("items", []) or []
        return [self._normalize_media(it) for it in items], body.get("next_max_id")

    async def get_media_by_username(self, username: str, count: int = 12) -> list[IgMedia]:
        data = await self._call(
            "user", "get_media_by_username", {"username": username, "count": count}
        )
        items = self._body(data).get("items", []) or []
        return [self._normalize_media(it) for it in items]

    # ----- Comments -----
    async def get_comments(self, ig_media_id: str) -> list[IgComment]:
        data = await self._call("media", "get_comments", {"id": ig_media_id})
        comments = self._body(data).get("comments", []) or self._body(data).get("items", []) or []
        out: list[IgComment] = []
        for c in comments:
            user = c.get("user", {}) or {}
            out.append(
                IgComment(
                    ig_comment_id=str(c.get("pk") or c.get("id") or ""),
                    ig_media_id=str(ig_media_id),
                    text=c.get("text"),
                    username=user.get("username"),
                    ig_user_id=str(user.get("pk") or "") or None,
                    created_at=c.get("created_at"),
                    like_count=c.get("comment_like_count"),
                    raw=c,
                )
            )
        return out

    # ----- Stories / Highlights (منبع «سایزبندی/قیمت» در هایلایت‌ها!) -----
    async def get_stories(self, ig_user_ids: list[str]) -> list[dict[str, Any]]:
        data = await self._call("user", "get_stories", {"ids": ig_user_ids})
        return self._body(data).get("items", []) or []

    async def get_highlights(self, ig_user_id: str) -> list[dict[str, Any]]:
        data = await self._call("user", "get_highlights", {"id": ig_user_id})
        return self._body(data).get("items", []) or []

    async def get_highlight_stories(self, highlight_ids: list[str]) -> list[dict[str, Any]]:
        data = await self._call("highlight", "get_stories", {"ids": highlight_ids})
        return self._body(data).get("items", []) or []

    # ----- Discovery (ایندکس بازار) -----
    async def hashtag_media(self, name: str) -> list[dict[str, Any]]:
        data = await self._call("hashtag", "get_media", {"name": name})
        return self._body(data).get("items", []) or []

    async def similar_accounts(self, ig_user_id: str) -> list[dict[str, Any]]:
        data = await self._call("user", "get_similar_accounts", {"id": ig_user_id})
        return self._body(data).get("users", []) or []
