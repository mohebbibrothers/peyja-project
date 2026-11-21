"""کرالر بودجه‌دار — CrawlBudgetGuard برای خطوط اشتراکی.

«ماشین بازی‌خور»: هر fetch با ردیابی بودجه و resume cursor ثبت می‌شود.
رسانه‌ها بلافاصله دانلود و آرشیو می‌شوند (URL منقضی‌شونده = داده‌ی ازدست‌رفته).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from peyja.config import get_settings
from peyja.ingest.media import MediaArchiver, media_urls_from_raw
from peyja.models import Page, RawItem, utcnow
from peyja.provider.boxapi_data import BoxApiDataProvider


class BudgetExceeded(Exception):
    """بودجه‌ی کرال تمام شد — resume بعدی از sync_cursor ادامه می‌دهد."""


class CrawlBudgetGuard:
    def __init__(self, max_requests: int | None = None):
        self.max_requests = max_requests or get_settings().crawl_max_requests
        self.used = 0

    def consume(self, n: int = 1) -> None:
        self.used += n
        if self.used > self.max_requests:
            raise BudgetExceeded(f"بودجه‌ی کرال تمام شد ({self.used}/{self.max_requests})")

    @property
    def remaining(self) -> int:
        return max(0, self.max_requests - self.used)


class Crawler:
    def __init__(
        self,
        session: AsyncSession,
        provider: BoxApiDataProvider,
        budget: CrawlBudgetGuard | None = None,
    ):
        self.session = session
        self.provider = provider
        self.budget = budget or CrawlBudgetGuard()

    async def crawl_page(
        self, username: str, max_media: int = 60, download_media: bool = True
    ) -> dict[str, Any]:
        """یک پیج عمومی را کرال می‌کند؛ هر رسانه در raw_items آرشیو و فایل‌ها دانلود می‌شوند."""
        self.budget.consume()
        profile = await self.provider.get_user_by_username(username)

        page = (
            await self.session.execute(select(Page).where(Page.username == profile.username))
        ).scalar_one_or_none()
        if page is None:
            page = Page(username=profile.username, ig_user_id=profile.ig_user_id, source="data")
            self.session.add(page)
            await self.session.flush()

        # پروفایل هم آرشیو می‌شود
        await self._archive(page.id, "profile", profile.ig_user_id, profile.raw)

        archiver = MediaArchiver(self.session) if download_media else None
        downloads = 0
        max_downloads = get_settings().crawl_max_downloads

        cursor = (page.sync_cursor or {}).get("next_max_id")
        fetched = 0
        try:
            while fetched < max_media:
                self.budget.consume()
                media_page, next_cursor = await self.provider.get_media_page(
                    page.ig_user_id or "", count=12, max_id=cursor
                )
                for m in media_page:
                    raw_item = await self._archive(page.id, "media", m.ig_media_id, m.raw)
                    if archiver and raw_item is not None and downloads < max_downloads:
                        saved = await archiver.archive_urls(raw_item, media_urls_from_raw(m.raw))
                        downloads += len(saved)
                    fetched += 1
                cursor = next_cursor
                page.sync_cursor = {"next_max_id": cursor}
                if not cursor:
                    break
        except BudgetExceeded:
            page.sync_cursor = {"next_max_id": cursor}  # رزومه از همین‌جا
            await self.session.commit()
            raise
        finally:
            if archiver:
                await archiver.aclose()

        page.last_synced_at = utcnow()
        await self.session.commit()
        return {
            "username": profile.username,
            "fetched": fetched,
            "media_downloaded": downloads,
            "budget_remaining": self.budget.remaining,
        }

    async def _archive(
        self, page_id: int, kind: str, ig_id: str, raw: dict[str, Any]
    ) -> RawItem | None:
        """آرشیو خام + آپدیت در صورت تغییر — «منبع حقیقت اولیه»."""
        existing = (
            await self.session.execute(
                select(RawItem).where(
                    RawItem.page_id == page_id, RawItem.kind == kind, RawItem.ig_id == str(ig_id)
                )
            )
        ).scalar_one_or_none()
        if existing:
            existing.raw = raw
            existing.fetched_at = utcnow()
            return existing
        new_item = RawItem(page_id=page_id, kind=kind, ig_id=str(ig_id), raw=raw)
        self.session.add(new_item)
        await self.session.flush()
        return new_item
