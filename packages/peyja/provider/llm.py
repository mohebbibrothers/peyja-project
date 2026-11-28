"""کلاینتِ یکپارچه‌ی LLM/VLM — «مغزِ هوشمندِ» پیجا.

قرارداد: هر endpointِ سازگار با OpenAI (`{base_url}/chat/completions`) —
vLLM / SGLang / LiteLLM / one-api / BoxAPI. مغزِ اصلی: مدلِ محلیِ **Qwen**
روی سرورِ خودمان (`qwen-local` — رایگان، داخلی، بدون وابستگیِ بیرونی)؛
BoxAPI AI = پشتیبان.

اصولِ مهندسی (قانون ۱):
- timeout سخت + retry با backoff نمایی برای 408/429/5xx/قطعی شبکه + احترام به Retry-After
- ضدِ تله‌ی پراکسی/WireGuard: برای IPهای داخلی/محلی `trust_env=False` تا HTTP_PROXY
  سیستمی ترافیکِ تونلِ WireGuard را نرباید
- خروجیِ JSONِ مقاومِ مدل‌های تفکرمحور (`<think>...</think>` در Qwen3.6): پاک‌سازیِ
  بلاکِ تفکر، حصارِ کد، متنِ حاشیه و جست‌وجوی شیءِ متوازن با `JSONDecoder.raw_decode`
- **شکستِ مغز ≠ شکستِ خطِ استخراج**: LlmError بالا می‌آید و pipeline با مسیرِ
  regex ادامه می‌دهد (fault isolation)
- تصویر: data URI داخلی (بدونِ URL منقضی‌شونده) — برای مدل‌های vision
"""

from __future__ import annotations

import asyncio
import base64
import ipaddress
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, runtime_checkable
from urllib.parse import urlparse

import httpx

from peyja.config import get_settings


class LlmError(RuntimeError):
    """خطای غیرقابل‌بازگشتِ لایه‌ی مغز (پس از همه‌ی retryها)."""


@dataclass
class ChatResult:
    """پاسخِ غنیِ مدل به همراه تله‌متریِ زمان، توکن و تفکر."""

    content: str
    latency_ms: float = 0.0
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    finish_reason: str | None = None
    had_thinking: bool = False


@runtime_checkable
class AiProvider(Protocol):
    """قراردادِ حداقلیِ مغزِ هوشمند — هر provider باید این را رعایت کند."""

    async def chat(
        self, messages: list[dict[str, Any]], model: str | None = None, **kwargs: Any
    ) -> str: ...

    async def extract_from_image(
        self, image_path: str | Path, prompt: str, model: str | None = None
    ) -> str: ...


_THINK_CLOSED_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_FENCED_BLOCK_RE = re.compile(r"```(?:[a-zA-Z0-9_-]+)?\s*([\s\S]*?)```")


def _has_think_trace(text: str) -> bool:
    low = text.lower()
    return "<think>" in low or "</think>" in low


def _extract_balanced_dicts(text: str) -> list[dict[str, Any]]:
    """استخراجِ همه‌ی شیءهای JSONِ متوازنِ معتبر از داخل متن/حاشیه."""
    decoder = json.JSONDecoder()
    found: list[dict[str, Any]] = []
    idx = 0
    n = len(text)
    while idx < n:
        pos = text.find("{", idx)
        if pos == -1:
            break
        try:
            obj, end = decoder.raw_decode(text, pos)
        except json.JSONDecodeError:
            idx = pos + 1
            continue
        if isinstance(obj, dict):
            found.append(obj)
        idx = max(end, pos + 1)
    return found


def parse_json_loose(text: str) -> dict[str, Any]:
    """JSONِ مقاوم: حذف `<think>...</think>`، حصارِ کد، متنِ اضافه، شیءِ متوازن.

    مهم برای مدل‌های خانواده Qwen3/3.6: این مدل‌ها پیش از پاسخِ نهایی ممکن است
    بخش `<think>...</think>` تولید کنند که خودش حاوی `{...}` است — روشِ ساده‌ی
    `find("{")..rfind("}")` آنجا می‌شکند، ولی اینجا ابتدا `<think>` حذف می‌شود و
    سپس با `JSONDecoder.raw_decode` شیءِ معتبر پیدا می‌شود.
    """
    raw = text.strip()
    if not raw:
        raise ValueError("پاسخِ مدل خالی بود")

    had_unclosed_think = "<think>" in raw.lower() and "</think>" not in raw.lower()
    cleaned = _THINK_CLOSED_RE.sub("", raw).strip()
    # اگر فقط </think> در متن مانده (پیشوندِ تفکر بدون <think> باز)، پس از آخرین </think> را بردار
    if "</think>" in cleaned.lower():
        last_end = cleaned.lower().rfind("</think>")
        cleaned = cleaned[last_end + len("</think>") :].strip()

    candidates: list[dict[str, Any]] = []
    # ۱. بلاک‌های fenced (ممکن است چند بلاک در متن باشد)
    for m in _FENCED_BLOCK_RE.finditer(cleaned):
        inner = m.group(1).strip()
        try:
            obj = json.loads(inner)
            if isinstance(obj, dict):
                candidates.append(obj)
        except json.JSONDecodeError:
            candidates.extend(_extract_balanced_dicts(inner))

    # ۲. مستقیم کلِ متنِ پاک‌شده (یا بخش‌به‌بخشِ شیءهای متوازن)
    if not candidates:
        try:
            obj = json.loads(cleaned)
            if isinstance(obj, dict):
                candidates.append(obj)
            else:
                raise ValueError(f"JSON خروجی شیء نیست: {type(obj).__name__}")
        except json.JSONDecodeError:
            candidates.extend(_extract_balanced_dicts(cleaned))

    if not candidates:
        if had_unclosed_think:
            raise ValueError(
                "پاسخِ مدل وسطِ <think> قطع شد و به JSON نرسید — max_tokens را افزایش دهید یا --no-think بزنید"
            )
        raise ValueError(f"شیءِ JSONِ معتبری در پاسخ پیدا نشد: {cleaned[:160]}")

    for c in reversed(candidates):
        if "extraction_successful" in c:
            return c
    return candidates[-1]


_parse_json_loose = parse_json_loose  # سازگاری با ایمپورت‌های داخلیِ قبلی


def _is_private_or_local_url(base_url: str) -> bool:
    """تشخیصِ آدرسِ محلی/شبکه‌ی خصوصی (مثل WireGuard LAN) برای دورزدنِ HTTP_PROXY."""
    try:
        host = (urlparse(base_url).hostname or "").strip().lower()
    except ValueError:
        return False
    if not host:
        return False
    if host in {"localhost", "127.0.0.1", "::1"} or host.endswith(".local"):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_private or ip.is_loopback or ip.is_link_local


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
        max_tokens: int | None = None,
        enable_thinking: bool | None = None,
        trust_env: bool | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._api_key = api_key
        self._timeout = timeout
        self._max_retries = max(1, max_retries)
        self._json_mode = json_mode
        self._max_tokens = max_tokens if (max_tokens and max_tokens > 0) else None
        self._enable_thinking = enable_thinking
        effective_trust_env = (
            (not _is_private_or_local_url(self._base_url)) if trust_env is None else trust_env
        )
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(timeout=timeout, trust_env=effective_trust_env)

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    # ── سلامت ────────────────────────────────────────────────────────────
    async def health(self) -> dict[str, Any]:
        """GET /models — پیش‌آزمونِ doctor و بنچ‌مارک (هرگز exception نمی‌دهد)."""
        t0 = time.perf_counter()
        try:
            resp = await self._client.get(f"{self._base_url}/models", headers=self._headers())
            resp.raise_for_status()
            data = resp.json()
            raw_list = data.get("data", []) if isinstance(data, dict) else data
            ids = [str(m.get("id")) for m in raw_list if isinstance(m, dict) and m.get("id")]
            return {
                "ok": True,
                "models": ids,
                "count": len(ids),
                "latency_ms": round((time.perf_counter() - t0) * 1000, 1),
            }
        except Exception as e:
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    # ── گفت‌وگو ───────────────────────────────────────────────────────────
    async def chat_detailed(
        self, messages: list[dict[str, Any]], model: str | None = None, **kwargs: Any
    ) -> ChatResult:
        """ارسالِ پیام و بازگرداندنِ پاسخ همراه با متادیتای زمان و توکن."""
        payload: dict[str, Any] = {
            "model": model or self._model,
            "messages": messages,
            **kwargs,
        }
        if self._max_tokens is not None and "max_tokens" not in payload:
            payload["max_tokens"] = self._max_tokens
        if self._json_mode and "response_format" not in payload:
            payload["response_format"] = {"type": "json_object"}
        if self._enable_thinking is not None and "chat_template_kwargs" not in payload:
            payload["chat_template_kwargs"] = {"enable_thinking": self._enable_thinking}

        t0 = time.perf_counter()
        last: Exception | None = None
        for attempt in range(self._max_retries):
            try:
                resp = await self._client.post(
                    f"{self._base_url}/chat/completions",
                    headers=self._headers(),
                    json=payload,
                )
                if resp.status_code in {408, 429, 500, 502, 503, 504}:
                    last = LlmError(f"HTTP {resp.status_code}: {resp.text[:240]}")
                    if attempt + 1 < self._max_retries:
                        await asyncio.sleep(_retry_delay(resp, attempt))
                    continue
                if resp.status_code >= 400:
                    raise LlmError(f"HTTP {resp.status_code}: {resp.text[:300]}")
                data = resp.json()
                if not isinstance(data, dict) or "choices" not in data:
                    err = data.get("error") if isinstance(data, dict) else data
                    raise LlmError(f"پاسخِ غیرمنتظره‌ی سرور: {str(err)[:240]}")
                choice = data["choices"][0]
                msg = choice.get("message") or {}
                raw_content = msg.get("content")
                reasoning = msg.get("reasoning_content") or msg.get("reasoning")
                finish_reason = choice.get("finish_reason")
                if raw_content is None or not str(raw_content).strip():
                    hint = (
                        " (تمام بودجه‌ی توکن صرفِ reasoning_content شد؛ max_tokens را بالا ببرید یا --no-think بزنید)"
                        if reasoning
                        else ""
                    )
                    raise LlmError(f"فیلد content خالی است (finish_reason={finish_reason}){hint}")
                content = str(raw_content)
                usage = data.get("usage") or {}
                return ChatResult(
                    content=content,
                    latency_ms=round((time.perf_counter() - t0) * 1000, 1),
                    prompt_tokens=usage.get("prompt_tokens"),
                    completion_tokens=usage.get("completion_tokens"),
                    finish_reason=finish_reason,
                    had_thinking=bool(reasoning) or _has_think_trace(content),
                )
            except LlmError:
                raise
            except (httpx.TimeoutException, httpx.TransportError) as e:
                last = e
                if attempt + 1 < self._max_retries:
                    await asyncio.sleep(min(0.5 * (2**attempt), 10.0))
            except (ValueError, KeyError, IndexError) as e:
                raise LlmError(f"خطای ساختارِ پاسخ JSON: {e}") from e
        raise LlmError(f"مغزِ هوشمند پس از {self._max_retries} تلاش پاسخ نداد: {last}")

    async def chat(
        self, messages: list[dict[str, Any]], model: str | None = None, **kwargs: Any
    ) -> str:
        res = await self.chat_detailed(messages, model=model, **kwargs)
        return res.content

    async def json_completion(
        self, system: str, user: str, model: str | None = None
    ) -> dict[str, Any]:
        """پاسخِ ساخت‌یافته‌ی JSON — برای pipeline استخراج."""
        text = await self.chat(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            model=model,
        )
        return parse_json_loose(text)

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
        max_tokens: int | None = None,
        enable_thinking: bool | None = None,
        trust_env: bool | None = None,
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
            max_tokens=s.qwen_max_tokens if max_tokens is None else max_tokens,
            enable_thinking=s.qwen_enable_thinking if enable_thinking is None else enable_thinking,
            trust_env=trust_env,
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
