"""گردآوریِ خودکارِ پست‌های واقعیِ فروشگاه‌های ایرانی از کانال‌های عمومی وب (بدون هزینه Data API).

وقتی سهمیه‌ی آزمایشی Data API (`boxapi.ir`) تمام شده و هنوز پلن پولی خریداری نشده است،
این ماژول پست‌های عمومیِ فروشگاه‌های واقعیِ زیبایی/آرایشی را از نمای وبِ عمومی
(`https://t.me/s/<channel>`) همراه با تصاویر محصول دانلود و با همان قرارداد استانداردِ
`Page` + `RawItem` + `MediaFile` در دیتابیس پیجا آرشیو می‌کند.
"""

from __future__ import annotations

import asyncio
import html
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from peyja.ingest.media import MediaArchiver, media_urls_from_raw
from peyja.models import Page, RawItem, utcnow

_BLOCK_SPLIT_RE = re.compile(
    r'<div class="tgme_widget_message_wrap[^>]*>(.*?)(?=<div class="tgme_widget_message_wrap|</section>|$)',
    re.S,
)
_POST_ID_RE = re.compile(r'data-post="([^"]+)"')
_DATETIME_RE = re.compile(r'datetime="([^"]+)"')
_TEXT_DIV_RE = re.compile(
    r'<div class="tgme_widget_message_text[^"]*"[^>]*>(.*?)</div>',
    re.S,
)
_PHOTO_URL_RE = re.compile(
    r'class="[^"]*tgme_widget_message_(?:photo_wrap|video_thumb)[^"]*"'
    r"[^>]*style=\"[^\"]*background-image:url\('(https://[^']+)'\)",
    re.S,
)
_STYLE_URL_FALLBACK_RE = re.compile(
    r"tgme_widget_message_(?:photo_wrap|video_thumb).*?"
    r"background-image:url\('(https://cdn\d*\.telesco\.pe/file/[^']+)'\)",
    re.S,
)
_PREV_CURSOR_RE = re.compile(r'data-before="(\d+)"')
_BR_RE = re.compile(r"<br\s*/?>", re.I)
_TAG_RE = re.compile(r"<[^>]+>")


def _clean_caption_html(fragment: str) -> str:
    """تبدیل امن HTML متن پست به متن تمیز فارسی با حفظ شکست خط."""
    with_newlines = _BR_RE.sub("\n", fragment)
    stripped = _TAG_RE.sub("", with_newlines)
    text = html.unescape(stripped)
    lines = [line.rstrip() for line in text.splitlines()]
    # حذف سطرهای خالیِ متوالیِ اضافه
    cleaned: list[str] = []
    prev_empty = False
    for line in lines:
        is_empty = not line.strip()
        if is_empty and prev_empty:
            continue
        cleaned.append(line)
        prev_empty = is_empty
    return "\n".join(cleaned).strip()


def _parse_iso_epoch(dt_str: str) -> int | None:
    if not dt_str:
        return None
    try:
        return int(datetime.fromisoformat(dt_str.replace("Z", "+00:00")).timestamp())
    except ValueError:
        return None


def parse_public_channel_html(
    page_html: str,
    channel: str,
    *,
    min_caption_chars: int = 20,
) -> tuple[list[dict[str, Any]], str | None]:
    """پارسِ HTML صفحه عمومی `t.me/s/<channel>` به رکوردهای استاندارد پیجا + مکان‌نمای صفحه‌ی قبل."""
    posts: list[dict[str, Any]] = []
    for match in _BLOCK_SPLIT_RE.finditer(page_html or ""):
        block = match.group(1)
        pid_m = _POST_ID_RE.search(block)
        txt_m = _TEXT_DIV_RE.search(block)
        if not pid_m or not txt_m:
            continue
        post_id = pid_m.group(1).strip()
        caption = _clean_caption_html(txt_m.group(1))
        if len(caption) < min_caption_chars:
            continue

        dt_m = _DATETIME_RE.search(block)
        taken_at = _parse_iso_epoch(dt_m.group(1)) if dt_m else None

        photo_urls = [
            u
            for u in dict.fromkeys(
                _PHOTO_URL_RE.findall(block) or _STYLE_URL_FALLBACK_RE.findall(block)
            )
            if "telegram.org/img/emoji" not in u
        ]

        posts.append(
            {
                "pk": post_id,
                "id": post_id,
                "code": post_id.replace("/", "_"),
                "taken_at": taken_at,
                "user": {"username": channel},
                "caption": {"text": caption},
                "image_versions2": {"candidates": [{"url": u} for u in photo_urls]},
                "permalink": f"https://t.me/{post_id}",
                "_source": "public_channel",
            }
        )

    prev_m = _PREV_CURSOR_RE.search(page_html or "")
    prev_cursor = prev_m.group(1) if prev_m else None
    return posts, prev_cursor


class PublicChannelCrawler:
    """کرالر ایمن و قابل‌تکرارِ کانال‌های عمومی فروشگاه‌های آرایشی/بهداشتی."""

    def __init__(
        self,
        session: AsyncSession,
        client: httpx.AsyncClient | None = None,
        base_url: str = "https://t.me/s",
        request_delay: float = 0.25,
        media_dir: str | Path | None = None,
    ):
        self.session = session
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            timeout=20.0,
            follow_redirects=True,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 Chrome/126.0.0.0 Safari/537.36"
                )
            },
        )
        self._base_url = base_url.rstrip("/")
        self._delay = request_delay
        self._media_dir = media_dir

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def crawl_channel(
        self,
        channel: str,
        *,
        max_posts: int = 25,
        before: str | None = None,
        download_media: bool = True,
        min_caption_chars: int = 20,
    ) -> dict[str, Any]:
        """دریافت پست‌ها + تصاویر واقعی یک کانال عمومی و ذخیره‌ی استاندارد در DB."""
        clean_channel = channel.strip().lstrip("@")
        page = (
            await self.session.execute(select(Page).where(Page.username == clean_channel))
        ).scalar_one_or_none()
        if page is None:
            page = Page(username=clean_channel, ig_user_id=None, source="public_channel")
            self.session.add(page)
            await self.session.flush()

        archiver = (
            MediaArchiver(
                self.session,
                client=None if self._owns_client else self._client,
                media_dir=self._media_dir,
            )
            if download_media
            else None
        )
        cursor = before
        collected: list[dict[str, Any]] = []
        seen_ids: set[str] = set()
        pages_fetched = 0
        downloads = 0

        try:
            while len(collected) < max_posts:
                url = f"{self._base_url}/{clean_channel}"
                if cursor:
                    url = f"{url}?before={cursor}"
                resp = await self._client.get(url)
                pages_fetched += 1
                if resp.status_code != 200:
                    break

                batch, next_cursor = parse_public_channel_html(
                    resp.text, clean_channel, min_caption_chars=min_caption_chars
                )
                # جدیدترین پست‌ها از پایین صفحه به بالا مرتب‌اند
                batch_ordered = list(reversed(batch))
                added_in_page = 0
                for item in batch_ordered:
                    pid = str(item["pk"])
                    if pid in seen_ids:
                        continue
                    seen_ids.add(pid)
                    collected.append(item)
                    added_in_page += 1
                    raw_item = await self._archive_media_item(page.id, pid, item)
                    if archiver is not None and raw_item is not None:
                        urls = media_urls_from_raw(item)
                        if urls:
                            saved = await archiver.archive_urls(raw_item, urls[:1], max_per_item=1)
                            downloads += len(saved)
                    if len(collected) >= max_posts:
                        break

                if not next_cursor or next_cursor == cursor or (added_in_page == 0 and not batch):
                    cursor = next_cursor
                    break
                cursor = next_cursor
                if self._delay > 0:
                    await asyncio.sleep(self._delay)
        finally:
            if archiver is not None and self._owns_client:
                await archiver.aclose()

        page.sync_cursor = {"before": cursor}
        page.last_synced_at = utcnow()
        await self.session.commit()

        return {
            "page_id": page.id,
            "username": clean_channel,
            "source": "public_channel",
            "fetched": len(collected),
            "media_downloaded": downloads,
            "pages_fetched": pages_fetched,
            "next_before": cursor,
        }

    async def _archive_media_item(
        self, page_id: int, post_id: str, raw: dict[str, Any]
    ) -> RawItem | None:
        existing = (
            await self.session.execute(
                select(RawItem).where(
                    RawItem.page_id == page_id,
                    RawItem.kind == "media",
                    RawItem.ig_id == str(post_id),
                )
            )
        ).scalar_one_or_none()
        if existing is not None:
            existing.raw = raw
            existing.fetched_at = utcnow()
            return existing
        item = RawItem(page_id=page_id, kind="media", ig_id=str(post_id), raw=raw)
        self.session.add(item)
        await self.session.flush()
        return item
