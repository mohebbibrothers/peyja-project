"""تست‌های مغزِ هوشمند (Qwen محلی) — قراردادِ سیم با سرورِ واقعیِ سازگار با OpenAI.

منتقدِ بی‌رحم (قانون ۵): نه mockِ ساده‌ی تابع، بلکه **HTTP سرورِ زنده** در مقیاسِ
کوچک تا همه‌چیزِ مسیر — URL، هدرها، payload، retry، timeout، JSON خراب، تصویر —
دقیقاً مثلِ شبکه‌ی واقعی سنجیده شود؛ بدونِ نیاز به شبکه‌ی سرورِ مدل در CI.
"""

from __future__ import annotations

import asyncio
import base64
import json
import threading
import time
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from peyja.extract.pipeline import ExtractionPipeline
from peyja.provider.llm import LlmError, OpenAICompatProvider, QwenLocalProvider, get_ai_provider

PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)

Behavior = Callable[[dict[str, Any], int], tuple[int, dict[str, Any]]]


class MockOpenAIServer:
    """سرورِ سازگار با OpenAI در مقیاسِ کوچک — رفتارش با callable برنامه‌ریزی می‌شود."""

    def __init__(self, behavior: Behavior | None = None, slow_s: float = 0.0) -> None:
        self.requests: list[dict[str, Any]] = []
        self.behavior = behavior
        self.slow_s = slow_s
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:  # سکوتِ لاگِ HTTP
                pass

            def do_GET(self) -> None:
                if self.path.endswith("/models"):
                    body = json.dumps(
                        {"data": [{"id": "qwen3.6-35b-a3b"}, {"id": "qwen-local"}]}
                    ).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                else:
                    self.send_response(404)
                    self.end_headers()

            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length", "0"))
                payload = json.loads(self.rfile.read(length) or b"{}")
                outer.requests.append({"path": self.path, "payload": payload})
                if outer.slow_s:
                    time.sleep(outer.slow_s)
                n = len(outer.requests)
                status, body = 200, {"choices": [{"message": {"content": '{"ok": true}'}}]}
                if outer.behavior is not None:
                    status, body = outer.behavior(payload, n)
                raw = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        self.url = f"http://127.0.0.1:{self.port}/v1"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self) -> MockOpenAIServer:
        self.thread.start()
        return self

    def __exit__(self, *args: Any) -> None:
        self.server.shutdown()
        self.server.server_close()


def _ok(content: str) -> tuple[int, dict[str, Any]]:
    return 200, {"choices": [{"message": {"content": content}}]}


# ── سلامت و قراردادِ سیم ──────────────────────────────────────────────────────


def test_health_lists_models() -> None:
    with MockOpenAIServer() as srv:
        p = OpenAICompatProvider(srv.url, "qwen3.6-35b-a3b")
        h = asyncio.run(p.health())
        assert h["ok"] and "qwen3.6-35b-a3b" in h["models"]
        asyncio.run(p.aclose())


def test_chat_wire_format() -> None:
    """URL، نامِ مدل، نقش‌ها و temperature باید دقیقاً روی سیم بنشینند."""
    with MockOpenAIServer() as srv:
        p = OpenAICompatProvider(srv.url, "qwen3.6-35b-a3b")
        out = asyncio.run(
            p.chat(
                [{"role": "system", "content": "سیستم"}, {"role": "user", "content": "کاربر"}],
                temperature=0,
            )
        )
        assert out == '{"ok": true}'
        req = srv.requests[0]
        assert req["path"] == "/v1/chat/completions"
        assert req["payload"]["model"] == "qwen3.6-35b-a3b"
        assert req["payload"]["temperature"] == 0
        assert req["payload"]["messages"][0] == {"role": "system", "content": "سیستم"}
        asyncio.run(p.aclose())


def test_json_completion_parses_fenced_json() -> None:
    def behavior(payload: dict[str, Any], n: int) -> tuple[int, dict[str, Any]]:
        return _ok('```json\n{"price_toman": 550000, "category": "makeup"}\n```')

    with MockOpenAIServer(behavior) as srv:
        p = OpenAICompatProvider(srv.url, "m")
        data = asyncio.run(p.json_completion("sys", "user"))
        assert data == {"price_toman": 550000, "category": "makeup"}
        asyncio.run(p.aclose())


def test_json_mode_flag_sends_response_format() -> None:
    with MockOpenAIServer(lambda payload, n: _ok('{"x": 1}')) as srv:
        p = OpenAICompatProvider(srv.url, "m", json_mode=True)
        asyncio.run(p.json_completion("s", "u"))
        assert srv.requests[0]["payload"]["response_format"] == {"type": "json_object"}
        asyncio.run(p.aclose())


# ── پایداری: retry / timeout / خرابی ──────────────────────────────────────────


def test_retry_on_500_then_success() -> None:
    def behavior(payload: dict[str, Any], n: int) -> tuple[int, dict[str, Any]]:
        if n == 1:
            return 500, {"error": "boom"}
        return _ok('{"ok": 1}')

    with MockOpenAIServer(behavior) as srv:
        p = OpenAICompatProvider(srv.url, "m", max_retries=3)
        out = asyncio.run(p.chat([{"role": "user", "content": "x"}]))
        assert out == '{"ok": 1}'
        assert len(srv.requests) == 2  # یک 500 + یک موفق
        asyncio.run(p.aclose())


def test_retries_exhausted_raises_llm_error() -> None:
    with MockOpenAIServer(lambda payload, n: (503, {"error": "down"})) as srv:
        p = OpenAICompatProvider(srv.url, "m", max_retries=2)
        with pytest.raises(LlmError):
            asyncio.run(p.chat([{"role": "user", "content": "x"}]))
        assert len(srv.requests) == 2
        asyncio.run(p.aclose())


def test_timeout_raises_llm_error() -> None:
    with MockOpenAIServer(slow_s=0.6) as srv:
        p = OpenAICompatProvider(srv.url, "m", timeout=0.15, max_retries=1)
        with pytest.raises(LlmError):
            asyncio.run(p.chat([{"role": "user", "content": "x"}]))
        asyncio.run(p.aclose())


def test_health_failure_is_soft() -> None:
    p = OpenAICompatProvider("http://127.0.0.1:1/v1", "m", timeout=0.2, max_retries=1)
    h = asyncio.run(p.health())
    assert h["ok"] is False and "error" in h
    asyncio.run(p.aclose())


# ── تصویر (برای مدل‌های vision) ────────────────────────────────────────────────


def test_extract_from_image_uses_data_uri(tmp_path: Path) -> None:
    img = tmp_path / "x.png"
    img.write_bytes(PNG_1PX)
    with MockOpenAIServer(lambda payload, n: _ok("توضیحِ تصویر")) as srv:
        p = OpenAICompatProvider(srv.url, "m")
        out = asyncio.run(p.extract_from_image(img, "این چیست؟"))
        assert out == "توضیحِ تصویر"
        content = srv.requests[0]["payload"]["messages"][0]["content"]
        assert content[0] == {"type": "text", "text": "این چیست؟"}
        assert content[1]["type"] == "image_url"
        assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")
        asyncio.run(p.aclose())


# ── مغز در pipeline: شکستِ مغز ≠ شکستِ استخراج ─────────────────────────────────


class _FakeAi:
    def __init__(self, replies: list[str | Exception]) -> None:
        self.replies = list(replies)
        self.calls = 0

    async def chat(
        self, messages: list[dict[str, Any]], model: str | None = None, **kw: Any
    ) -> str:
        self.calls += 1
        r = self.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return r

    async def extract_from_image(
        self, image_path: Any, prompt: str, model: str | None = None
    ) -> str:
        return ""


_GOOD = json.dumps(
    {
        "extraction_successful": True,
        "title_fa": "ریمل اسنس",
        "product_name": "ریمل",
        "category": "makeup",
        "brand": "اسنس",
        "price_toman": 550000,
        "colors": [],
        "shades": [],
        "sizes": [],
        "stock_state": "in_stock",
        "confidence": 0.9,
    },
    ensure_ascii=False,
)


def test_pipeline_vlm_extract_success() -> None:
    ai = _FakeAi([_GOOD])
    pipeline = ExtractionPipeline(session=None, ai=ai)  # type: ignore[arg-type]
    r = asyncio.run(pipeline._vlm_extract_safe("ریمل اسنس موجود شد ۵۵۰💸"))
    assert r is not None and r.category == "makeup" and r.price_toman == 550000
    assert ai.calls == 1


def test_pipeline_vlm_retry_on_invalid_json() -> None:
    ai = _FakeAi(["این JSON نیست", _GOOD])
    pipeline = ExtractionPipeline(session=None, ai=ai)  # type: ignore[arg-type]
    r = asyncio.run(pipeline._vlm_extract_safe("کپشن"))
    assert r is not None and r.brand == "اسنس"
    assert ai.calls == 2  # تلاشِ اول نامعتبر ← بازخورد ← تلاشِ دوم


def test_pipeline_brain_failure_degrades_to_regex() -> None:
    ai = _FakeAi([RuntimeError("شبکه قطع است"), LlmError("down"), LlmError("down")])
    pipeline = ExtractionPipeline(session=None, ai=ai)  # type: ignore[arg-type]
    r = asyncio.run(pipeline._vlm_extract_safe("کپشن"))
    assert r is None  # مغز خاموش ← مسیرِ regex ادامه می‌دهد؛ بدون crash


# ── کارخانه و تنظیمات ─────────────────────────────────────────────────────────


def test_factory_defaults_to_qwen_local() -> None:
    p = get_ai_provider()
    assert isinstance(p, QwenLocalProvider)
    assert p._model == "qwen3.6-35b-a3b"
    assert p._base_url == "http://10.1.231.22:8000/v1"
    asyncio.run(p.aclose())


def test_factory_boxapi_fallback() -> None:
    p = get_ai_provider("boxapi")
    assert type(p).__name__ == "BoxApiAiProvider"
    asyncio.run(p.aclose())


def test_qwen_overrides() -> None:
    p = QwenLocalProvider(base_url="http://127.0.0.1:9/v1", model="m2")
    assert p._base_url == "http://127.0.0.1:9/v1" and p._model == "m2"
    asyncio.run(p.aclose())


# ── بنچ‌مارک: ازسرگیری‌پذیری + گزارش ────────────────────────────────────────────


def test_vlm_benchmark_scores_and_resumes(tmp_path: Path) -> None:
    from peyja.vlm_bench import run_vlm_benchmark

    samples = [
        {
            "id": "gold-001",
            "caption": "ریمل اسنس موجود شد ۵۵۰💸",
            "price_toman": 550000,
            "category": "makeup",
            "brand": "اسنس",
        },
        {"id": "gold-002", "caption": "کرم ضد آفتاب", "category": "skincare"},
    ]
    ai = _FakeAi(
        [
            _GOOD,
            json.dumps(
                {
                    "extraction_successful": True,
                    "title_fa": "کرم ضد آفتاب",
                    "product_name": "کرم ضد افتاب",
                    "category": "skincare",
                    "brand": None,
                    "price_toman": None,
                    "colors": [],
                    "shades": [],
                    "sizes": [],
                    "stock_state": "unknown",
                    "confidence": 0.8,
                },
                ensure_ascii=False,
            ),
        ]
    )
    out = tmp_path / "vlm-pred.json"

    rep1 = asyncio.run(run_vlm_benchmark(samples, out, ai))
    assert rep1["ok"] is True
    assert rep1["n_scored"] == 2 and ai.calls == 2
    assert rep1["vlm_score"]["category_accuracy"] == 1.0
    assert out.exists()

    # اجرای دوم: از کش — هیچ صدای تازه‌ای به مدل نمی‌رود (ازسرگیری)
    rep2 = asyncio.run(run_vlm_benchmark(samples, out, ai))
    assert rep2["n_scored"] == 2 and ai.calls == 2


def test_vlm_benchmark_fails_soft_when_brain_down() -> None:
    from peyja.vlm_bench import run_vlm_benchmark

    class _DeadAi:
        async def chat(self, *a: Any, **k: Any) -> str:
            raise LlmError("down")

        async def health(self) -> dict[str, Any]:
            return {"ok": False, "error": "connection refused"}

        async def extract_from_image(self, *a: Any, **k: Any) -> str:
            return ""

    rep = asyncio.run(
        run_vlm_benchmark([{"id": "x", "caption": "y"}], Path("/tmp/never.json"), _DeadAi())
    )
    assert rep["ok"] is False and "در دسترس نیست" in rep["error"]
