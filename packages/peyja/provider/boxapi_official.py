"""کلاینت خط ① — Official DM/Comment API (Meta-verified) روی میزبان واقعی Sendbox.

واقعیت‌های کشف‌شده با پروب زنده (۲۰۲۶-۱۱):
- میزبان: https://api.sendbox.chat/api/v1
- GET  /service/accounts                      ← فهرست پیج‌های متصل (X-Api-Key)
- POST /service/actions/{action}  body={...فیلدها در سطح بالا — تخت}
        ← 202 {"success":true,"data":{"accepted":true,"action","event_id"}}
        ⚠️ 202 فقط یعنی «در صف رفت»؛ اعتبارسنجی payload در ورکر async است.
        پیچیدن در {"data":...} = 202ِ دروغین ⇒ شکست با «recipient_id and message
        are required» در پنل (تجربه‌ی واقعی ۱۴۰۵/۰۹/۰۵ — مستند + پروب A/B).
        اکشن‌ها async هستند؛ نتیجه از وب‌هوک با event_type «action.*» می‌آید
- پاکت پاسخ: {"success": bool, "message": str, "status_code": int, "data": ...}
- اکشن‌های ناشناخته: 404 «Unknown action» (اعتبارسنجی سمت سرور)
"""

from __future__ import annotations

from typing import Any

import httpx

from peyja.config import get_settings


class OfficialApiError(Exception):
    def __init__(self, code: Any, message: str, errors: Any = None):
        self.code = code
        self.errors = errors
        detail = f"{message} | errors={errors}" if errors else message
        super().__init__(f"[{code}] {detail}")


class BoxApiOfficialProvider:
    """کلاینت اکشن‌های رسمی — همیشه از طریق این کلاس، هرگز مستقیم."""

    def __init__(self, api_key: str | None = None, base_url: str | None = None):
        settings = get_settings()
        self._api_key = api_key or settings.boxapi_api_key
        self._base_url = (base_url or settings.boxapi_base_url).rstrip("/")
        self._client = httpx.AsyncClient(timeout=30.0)

    async def aclose(self) -> None:
        await self._client.aclose()

    def _headers(self) -> dict[str, str]:
        return {"X-Api-Key": self._api_key, "Content-Type": "application/json"}

    # ----- هسته -----
    async def _action(self, action: str, data: dict[str, Any]) -> dict[str, Any]:
        """یک اکشن صف می‌شود (202). نتیجه‌ی نهایی از وب‌هوک می‌آید.

        بدنه «تخت» است — curl رسمی boxapi.ir فیلدها را در سطح بالای JSON می‌خواهد؛
        پیچیدن در {"data": ...} = 202ِ دروغین (صف می‌شود ولی ورکر async با
        «recipient_id and message are required» رد می‌کند).
        """
        resp = await self._client.post(
            f"{self._base_url}/service/actions/{action}",
            headers=self._headers(),
            json=data,
        )
        body = resp.json()
        if resp.status_code == 202 and body.get("success"):
            d = body.get("data") or {}
            return {
                "accepted": True,
                "action": d.get("action"),
                "event_id": d.get("event_id"),
            }
        if body.get("success"):
            return body.get("data") or {}
        raise OfficialApiError(
            body.get("status_code", resp.status_code),
            body.get("message", str(body)[:300]),
            errors=body.get("errors"),
        )

    async def _get(self, path: str) -> Any:
        resp = await self._client.get(f"{self._base_url}{path}", headers=self._headers())
        body = resp.json()
        if body.get("success"):
            return body.get("data")
        raise OfficialApiError(
            body.get("status_code", resp.status_code),
            body.get("message", str(body)[:300]),
        )

    # ----- اکانت‌ها -----
    async def get_accounts(self) -> list[dict[str, Any]]:
        """فهرست پیج‌های متصل — منبع account_id (UUID)."""
        data = await self._get("/service/accounts")
        return data if isinstance(data, list) else []

    # ----- پیام -----
    async def send_message(
        self, account_id: str, recipient_id: str, message: str
    ) -> dict[str, Any]:
        return await self._action(
            "send_message",
            {
                "account_id": account_id,
                "recipient_id": recipient_id,
                "message": message,
            },
        )

    async def send_attachment(
        self, account_id: str, recipient_id: str, attachment: dict[str, Any]
    ) -> dict[str, Any]:
        return await self._action(
            "send_attachment",
            {
                "account_id": account_id,
                "recipient_id": recipient_id,
                "attachment": attachment,
            },
        )

    async def send_generic_template(
        self, account_id: str, recipient_id: str, elements: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """کارت محصول — «گالری قیمت‌دار» اینستاگرام در قلب معماری کاتالوگ."""
        return await self._action(
            "send_generic_template",
            {
                "account_id": account_id,
                "recipient_id": recipient_id,
                "elements": elements,
            },
        )

    # ----- کامنت -----
    async def reply_comment(self, account_id: str, comment_id: str, message: str) -> dict[str, Any]:
        return await self._action(
            "reply_comment",
            {"account_id": account_id, "comment_id": comment_id, "message": message},
        )

    async def get_post_comments(self, account_id: str, ig_media_id: str) -> dict[str, Any]:
        return await self._action(
            "get-post-comments", {"account_id": account_id, "media_id": ig_media_id}
        )

    async def private_reply(self, account_id: str, comment_id: str, message: str) -> dict[str, Any]:
        """تبدیل کامنت عمومی به DM — «کانال اینباکس مخفی»."""
        return await self._action(
            "private_reply",
            {"account_id": account_id, "comment_id": comment_id, "message": message},
        )

    # ----- گفتگو -----
    async def get_conversations(
        self, account_id: str, user_id: str | None = None
    ) -> dict[str, Any]:
        data: dict[str, Any] = {"account_id": account_id}
        if user_id:
            data["user_id"] = user_id
        return await self._action("conversation_conversations", data)

    # ----- رفتارنگاری / پست -----
    async def follow_status(self, account_id: str, user_id: str) -> dict[str, Any]:
        return await self._action("follow-status", {"account_id": account_id, "user_id": user_id})

    async def list_posts(self, account_id: str) -> dict[str, Any]:
        """پست‌های پیج متصل (async ⇒ نتیجه از وب‌هوک action.list_posts)."""
        return await self._action("list_posts", {"account_id": account_id})
