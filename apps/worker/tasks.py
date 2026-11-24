"""Worker وظایف پس‌زمینه (Phase 1: حداقلی — بدون Celery؛ async مستقیم).

بعداً به ردیف پردازش (queue) منتقل می‌شود؛ فعلاً توابع async خام.
"""

from __future__ import annotations

from peyja.db import get_session_factory
from peyja.extract.pipeline import ExtractionPipeline
from peyja.provider.llm import get_ai_provider


async def process_extraction(page_id: int) -> dict:
    factory = get_session_factory()
    async with factory() as session:
        pipeline = ExtractionPipeline(session, ai=get_ai_provider())
        stats = await pipeline.run_for_page(page_id)
        await session.commit()
        return stats
