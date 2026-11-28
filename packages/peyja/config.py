"""تنظیمات مرکزی — نگاشت دقیق به متغیرهای محیطی BoxAPI/Sendbox."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # ── BoxAPI / Sendbox (خط ① رسمی — DM/Comment) ─────────────────────────────
    boxapi_api_key: str = ""  # X-Api-Key برای /service/actions/{action}
    boxapi_login_token: str = ""  # جریان login-link اتصال پیج
    boxapi_base_url: str = "https://api.sendbox.chat/api/v1"
    boxapi_login_url: str = "https://api.sendbox.chat"
    boxapi_domain: str = ""  # دامنه/تونل وب‌هوک (ngrok یا دامنه‌ی VPS)
    app_url: str = ""
    demo_mode: bool = False
    enable_test_page: bool = False

    # ── خط ② دیتای عمومی (توکن جدا — طرح ۱۴ روزه‌ی boxapi.ir) ─────────────────
    boxapi_data_token: str = ""
    boxapi_data_url: str = "https://boxapi.ir"

    # ── خط ③ AI (gateway سازگار با OpenAI) ─────────────────────────────────────
    boxapi_ai_token: str = ""
    boxapi_ai_base_url: str = "https://ai.boxapi.ir"

    # ── وب‌هوک ─────────────────────────────────────────────────────────────────
    boxapi_webhook_secret: str = ""  # ممکن است با whsec_ شروع شود
    # پنجره‌ی replay به ثانیه. اگر ساعت سرور میزبان منحرف است (doctor هشدار می‌دهد)
    # این مقدار را بزرگ‌تر کنید؛ هزینه‌اش: بازپخش رویدادهای قدیمی در همان بازه ممکن می‌شود.
    webhook_tolerance_seconds: int = 300
    # پاسخ خودکار اولیه (P0) به پیام‌های ورودی — خالی یعنی غیرفعال
    auto_reply_text: str = ""

    # ── ذخیره‌سازی ─────────────────────────────────────────────────────────────
    database_url: str = ""  # اگر خالی باشد ← sqlite از db_path
    db_path: str = "./data/app.db"
    media_dir: str = "./data/media"

    # ── استخراج ───────────────────────────────────────────────────────────────
    extract_vlm_model: str = "gpt-4o-mini"
    extract_confidence_tau: float = 0.75  # بالای این ← تأییدشده
    extract_review_floor: float = 0.4  # زیر این ← رد/لاگ شکست

    # ── مغزِ هوشمند (LLM/VLM) — مدلِ محلیِ Qwen روی سرورِ خودمان ────────────────
    vlm_provider: str = "qwen-local"  # "qwen-local" (مغزِ اصلی) | "boxapi" (پشتیبان)
    qwen_base_url: str = "http://10.1.231.22:8000/v1"
    qwen_model: str = "qwen3.6-35b-a3b"
    qwen_api_key: str = ""  # سرورِ داخلی معمولاً بدون کلید
    qwen_timeout: float = 120.0
    qwen_max_retries: int = 3
    qwen_max_tokens: int = 4096  # سقف کافی تا مدلِ تفکرمحور وسطِ <think> قطع نشود
    qwen_json_mode: bool = False  # response_format=json_object اگر سرور پشتیبانی کند
    qwen_enable_thinking: bool | None = None  # False برای سرعت بیشتر (اگر سرور vLLM/SGLang باشد)
    qwen_context_length: int = 131072

    # ── بودجه‌ها ───────────────────────────────────────────────────────────────
    crawl_max_requests: int = 300
    crawl_max_downloads: int = 120

    @property
    def effective_database_url(self) -> str:
        """Postgres اگر باشد، وگرنه SQLite محلی (برای VPS/لوکال بدون Docker)."""
        if self.database_url:
            return self.database_url
        db = Path(self.db_path)
        db.parent.mkdir(parents=True, exist_ok=True)
        return f"sqlite+aiosqlite:///{db}"

    @property
    def webhook_secret_key(self) -> str:
        """کلید HMAC — پیشوند whsec_ (فرمت متداول) حذف می‌شود."""
        s = self.boxapi_webhook_secret
        return s[6:] if s.startswith("whsec_") else s


@lru_cache
def get_settings() -> Settings:
    return Settings()
