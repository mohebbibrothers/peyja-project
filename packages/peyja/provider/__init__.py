"""لایه‌ی Provider — Anti-Corruption Layer بین سیستم ما و هر ارائه‌دهنده‌ی دسترسی.

هرگز کدی بیرون از این پکیج، BoxAPI را مستقیم صدا نمی‌زند.
فردا اگر مستقیم Meta API زدیم، فقط یک Provider جدید اضافه می‌شود.
"""

from peyja.provider.base import IgComment, IgMedia, IgProfile
from peyja.provider.boxapi_ai import BoxApiAiProvider
from peyja.provider.boxapi_data import BoxApiDataProvider
from peyja.provider.boxapi_official import BoxApiOfficialProvider

__all__ = [
    "BoxApiAiProvider",
    "BoxApiDataProvider",
    "BoxApiOfficialProvider",
    "IgComment",
    "IgMedia",
    "IgProfile",
]
