"""کلاینت خط ③ — BoxAPI AI API (gateway متن‌باز سازگار با OpenAI).

کاربرد در Phase 1: VLM برای استخراج از تصویر/ویدیو + کمک‌استخراج متنی.
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

import httpx

from peyja.config import get_settings


class BoxApiAiProvider:
    def __init__(self, token: str | None = None, base_url: str | None = None):
        settings = get_settings()
        self._token = token or settings.boxapi_ai_token
        self._base_url = (base_url or settings.boxapi_ai_base_url).rstrip("/")
        self._client = httpx.AsyncClient(timeout=120.0)

    async def aclose(self) -> None:
        await self._client.aclose()

    async def chat(
        self, messages: list[dict[str, Any]], model: str | None = None, **kwargs: Any
    ) -> str:
        settings = get_settings()
        payload = {"model": model or settings.extract_vlm_model, "messages": messages, **kwargs}
        headers = {"Authorization": f"Bearer {self._token}", "Content-Type": "application/json"}
        resp = await self._client.post(
            f"{self._base_url}/ai/v1/chat/completions", headers=headers, json=payload
        )
        resp.raise_for_status()
        data = resp.json()
        return data["choices"][0]["message"]["content"]

    async def extract_from_image(
        self, image_path: str | Path, prompt: str, model: str | None = None
    ) -> str:
        """تحلیل تصویر با VLM — data URI داخلی (بدون وابستگی به URL منقضی‌شونده)."""
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
                    {"type": "image_url", "image_url": {"url": f"data:image/{mime};base64,{b64}"}},
                ],
            }
        ]
        return await self.chat(messages, model=model)

    async def json_completion(
        self, system: str, user: str, model: str | None = None
    ) -> dict[str, Any]:
        """پاسخ ساخت‌یافته (JSON) از مدل — برای pipeline استخراج."""
        import json

        text = await self.chat(
            [{"role": "system", "content": system}, {"role": "user", "content": user}], model=model
        )
        # پاک‌سازی حصارهای کد احتمالی
        t = text.strip()
        if t.startswith("```"):
            t = t.split("```", 2)[1]
            if t.startswith("json"):
                t = t[4:]
        return json.loads(t)
