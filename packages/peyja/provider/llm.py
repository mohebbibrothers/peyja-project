"""کلاینتِ یکپارچه‌ی LLM/VLM — «مغزِ هوشمندِ» پیجا.

قرارداد: هر endpointِ سازگار با OpenAI (`{base_url}/chat/completions`) —
vLLM / SGLang / LiteLLM / one-api / BoxAPI. مغزِ اصلی: مدلِ محلیِ **Qwen**
روی سرورِ خودمان (`qwen-local` — رایگان، داخلی، بدون وابستگیِ بیرونی)؛
BoxAPI AI = پشتیبان.

اصولِ مهندسی (قانون ۱):
- timeout سخت + retry با backoff نمایی برای 429/5xx/قطعی شبکه + احترام به Retry-After
- خروجیِ JSON: پاک‌سازیِ حصارِ کد + استخراجِ نخستین شیءِ متوازن
- **شکستِ مغز ≠ شکستِ خطِ استخراج**: LlmError بالا می‌آید و pipeline با مسیرِ
  regex ادامه می‌دهد (fault isolation)
- تصویر: data URI داخلی (بدونِ URL منقضی‌شونده) — برای مدل‌های vision
"""

from __future__ import annotations

import asyncio
import base64
import json
import re
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import httpx

from peyja.config import get_settings


class LlmError(RuntimeError):
    """خطای غیرقابل‌بازگشتِ لایه‌ی مغز (پس از همه‌ی retryها)."""


@runtime_checkable
class AiProvider(Protocol):
    """قراردادِ حداقلیِ مغزِ هوشمند — هر provider باید این را رعایت کند."""

    async def chat(
        self, messages: list[dict[str, Any]], model: str | None = None, **kwargs: Any
    ) -> str: ...

    async def extract_from_image(
        self, image_path: str | Path, prompt: str, model: str | None = None
    ) -> str: ...


def _parse_json_loose(text: str) -> dict[str, Any]:
    """JSONِ مقاوم: حصارِ کد، متنِ اضافه، شیءِ میانی."""
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\n?", "", t)
        t = re.sub(r"\n?```$", "", t)
    start, end = t.find("{"), t.rfind("}")
    if start != -1 and end != -1 and end > start:
        t = t[start : end + 1]
    parsed = json.loads(t)
    if not isinstance(parsed, dict):
        raise ValueError(f"JSON خروجی شیء نیست: {type(parsed).__name__}")
    return parsed


def _retry_delay(resp: httpx.Response, attempt: int) -> float:
    """تأخیرِ هوشمند: Retry-After اگر باشد، وگرنه backoff نمایی (سقف ۱۰ ثانیه)."""
    ra = resp.headers.get("Retry-After")
    if ra:
        try:
            return min(float(ra), 10.0)
        except ValueError:
            pass
    return min(0.5 * (2**attempt), 10.0)


class OpenAICompatProvider:
    """کلاینتِ هر سرویسِ سازگار با OpenAI — مغزِ هوشمندِ قابلِ تعویض."""

    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str = "",
        timeout: float = 120.0,
        max_retries: int = 3,
        json_mode: bool = False,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._api_key = api_key
        self._timeout = timeout
        self._max_retries = max(1, max_retries)
        self._json_mode = json_mode
        self._client = client or httpx.AsyncClient(timeout=timeout)

    async def aclose(self) -> None:
        await self._client.aclose()

    # ── سلامت ────────────────────────────────────────────────────────────
    async def health(self) -> dict[str, Any]:
        """GET /models — پیش‌آزمونِ doctor و بنچ‌مارک (هرگز exception نمی‌دهد)."""
        try:
            resp = await self._client.get(f"{self._base_url}/models", headers=self._headers())
            resp.raise_for_status()
            data = resp.json()
            ids = [str(m.get("id")) for m in data.get("data", [])]
            return {"ok": True, "models": ids, "count": len(ids)}
        except Exception as e:
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    # ── گفت‌وگو ───────────────────────────────────────────────────────────
    async def chat(
        self, messages: list[dict[str, Any]], model: str | None = None, **kwargs: Any
    ) -> str:
        payload: dict[str, Any] = {
            "model": model or self._model,
            "messages": messages,
            **kwargs,
        }
        if self._json_mode and "response_format" not in payload:
            payload["response_format"] = {"type": "json_object"}
        last: Exception | None = None
        for attempt in range(self._max_retries):
            try:
                resp = await self._client.post(
                    f"{self._base_url}/chat/completions",
                    headers=self._headers(),
                    json=payload,
                )
                if resp.status_code in {429, 500, 502, 503, 504}:
                    last = LlmError(f"HTTP {resp.status_code}: {resp.text[:200]}")
                    await asyncio.sleep(_retry_delay(resp, attempt))
                    continue
                resp.raise_for_status()
                data = resp.json()
                return str(data["choices"][0]["message"]["content"])
            except (httpx.TimeoutException, httpx.TransportError) as e:
                last = e
                await asyncio.sleep(min(0.5 * (2**attempt), 10.0))
        raise LlmError(f"مغزِ هوشمند پس از {self._max_retries} تلاش پاسخ نداد: {last}")

    async def json_completion(
        self, system: str, user: str, model: str | None = None
    ) -> dict[str, Any]:
        """پاسخِ ساخت‌یافته‌ی JSON — برای pipeline استخراج."""
        text = await self.chat(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            model=model,
        )
        return _parse_json_loose(text)

    async def extract_from_image(
        self, image_path: str | Path, prompt: str, model: str | None = None
    ) -> str:
        """تحلیلِ تصویر با VLM — data URI داخلی (برای مدل‌های vision)."""
        raw = Path(image_path).read_bytes()
        ext = Path(image_path).suffix.lstrip(".").lower() or "jpg"
        mime = {"jpg": "jpeg", "jpeg": "jpeg", "png": "png", "webp": "webp", "gif": "gif"}.get(
            ext, "jpeg"
        )
        b64 = base64.b64encode(raw).decode()
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": prompt},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/{mime};base64,{b64}"},
                    },
                ],
            }
        ]
        return await self.chat(messages, model=model)

    # ── داخلی ─────────────────────────────────────────────────────────────
    def _headers(self) -> dict[str, str]:
        h = {"Content-Type": "application/json"}
        if self._api_key:
            h["Authorization"] = f"Bearer {self._api_key}"
        return h


class QwenLocalProvider(OpenAICompatProvider):
    """مغزِ اصلی: مدلِ محلیِ Qwen روی سرورِ خودمان (رایگان — `qwen-local`)."""

    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
        api_key: str | None = None,
        timeout: float | None = None,
        max_retries: int | None = None,
        json_mode: bool | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        s = get_settings()
        super().__init__(
            base_url=base_url or s.qwen_base_url,
            model=model or s.qwen_model,
            api_key=s.qwen_api_key if api_key is None else api_key,
            timeout=s.qwen_timeout if timeout is None else timeout,
            max_retries=s.qwen_max_retries if max_retries is None else max_retries,
            json_mode=s.qwen_json_mode if json_mode is None else json_mode,
            client=client,
        )


def get_ai_provider(name: str | None = None) -> Any:
    """کارخانه‌ی مغزِ هوشمند — از تنظیمات (`vlm_provider`) یا نامِ صریح."""
    s = get_settings()
    name = name or s.vlm_provider
    if name == "boxapi":
        from peyja.provider.boxapi_ai import BoxApiAiProvider

        return BoxApiAiProvider()
    return QwenLocalProvider()
