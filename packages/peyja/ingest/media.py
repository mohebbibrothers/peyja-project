"""آرشیو رسانه — «URL منقضی‌شونده = داده‌ی ازدست‌رفته».

دانلود فوری + sha256 + ذخیره‌ی محلی + حذف تکراری‌ها. هرگز فقط media_url نگه نمی‌داریم.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from peyja.config import get_settings
from peyja.models import MediaFile, RawItem

_EXT_BY_MAGIC: dict[bytes, str] = {
    b"\xff\xd8\xff": "jpg",
    b"\x89PNG\r\n\x1a\n": "png",
    b"GIF8": "gif",
    b"RIFF": "webp",
}


def _guess_ext(data: bytes, url: str) -> str:
    for magic, ext in _EXT_BY_MAGIC.items():
        if data.startswith(magic):
            return ext
    for cand in ("jpg", "jpeg", "png", "webp", "mp4", "gif"):
        if f".{cand}" in url.split("?")[0][-12:]:
            return cand
    return "bin"


class MediaArchiver:
    def __init__(
        self,
        session: AsyncSession,
        client: httpx.AsyncClient | None = None,
        media_dir: str | Path | None = None,
    ):
        self.session = session
        self._client = client or httpx.AsyncClient(timeout=60.0, follow_redirects=True)
        self._dir = Path(media_dir or get_settings().media_dir)
        self._dir.mkdir(parents=True, exist_ok=True)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def archive_urls(
        self, raw_item: RawItem, urls: list[str], max_per_item: int = 3
    ) -> list[MediaFile]:
        """چند URL اول را دانلود و آرشیو می‌کند؛ هش تکراری دوباره ذخیره نمی‌شود."""
        saved: list[MediaFile] = []
        for url in [u for u in dict.fromkeys(urls) if u][:max_per_item]:
            mf = await self._archive_one(raw_item, url)
            if mf:
                saved.append(mf)
        return saved

    async def _archive_one(self, raw_item: RawItem, url: str) -> MediaFile | None:
        # اول جستجو در DB — از دانلود تکراری جلوگیری می‌شود
        existing_by_url = (
            await self.session.execute(
                select(MediaFile).where(MediaFile.url_original == url)
            )
        ).scalar_one_or_none()
        if existing_by_url:
            return existing_by_url

        try:
            resp = await self._client.get(url)
            if resp.status_code != 200:
                return None
            data = resp.content
        except httpx.HTTPError:
            return None

        sha = hashlib.sha256(data).hexdigest()
        existing = (
            await self.session.execute(select(MediaFile).where(MediaFile.sha256 == sha))
        ).scalar_one_or_none()
        if existing:
            return existing  # حذف تکراری — همان فایل قبلی

        ext = _guess_ext(data, url)
        sub = self._dir / sha[:2]
        sub.mkdir(parents=True, exist_ok=True)
        path = sub / f"{sha}.{ext}"
        path.write_bytes(data)

        mf = MediaFile(
            raw_item_id=raw_item.id,
            url_original=url,
            sha256=sha,
            local_path=str(path),
        )
        self.session.add(mf)
        await self.session.flush()
        return mf


def media_urls_from_raw(raw: dict[str, Any]) -> list[str]:
    """استخراج URLها از payload خام اینستاگرام (با کیفیت اولویت‌دار)."""
    urls: list[str] = []
    item = raw
    iv2 = (item.get("image_versions2") or {}).get("candidates") or []
    urls += [c["url"] for c in iv2 if c.get("url")]
    urls += [v["url"] for v in (item.get("video_versions") or []) if v.get("url")]
    if item.get("media_url"):
        urls.append(item["media_url"])
    for sub in item.get("carousel_media") or []:
        iv2s = (sub.get("image_versions2") or {}).get("candidates") or []
        urls += [c["url"] for c in iv2s if c.get("url")]
    return list(dict.fromkeys(urls))
