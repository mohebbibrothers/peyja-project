"""کرالرِ بسیار سبک و تک‌پرسونای اینستاگرام برای جمع‌آوری داده‌ی واقعیِ بچ ۲ (`ig_lite`).

طراحی‌شده صرفاً برای مرحله‌ی ارزیابیِ سبک با یک اکانت تستی (بدون کرال سنگین یا چندپرسونا):
- پشتیبانی از مسیرهای فعالِ ۲۰۲۶ وب اینستاگرام:
  ۱) Relay GraphQL (`/graphql/query` و `/api/graphql` با `fb_dtsg`، `lsd` و کشفِ زنده/پشتیبان `doc_id`)
  ۲) استخراج JSON توکار پروفایل (`<script type="application/json" data-sjs>` + `?__a=1&__d=dis`)
  ۳) پشتیبانِ REST قدیمی (`feed/user` و `web_profile_info`) در صورت باز بودن روی برخی نشست‌ها
- مکثِ شبه‌انسانی بین فروشگاه‌ها (۳.۵ تا ۶.۵ ثانیه) و توقف محترمانه روی 429 / checkpoint
- نرمال‌سازیِ دقیق به همان قالب `RawItem` اینستاگرامیِ `@farmashop_aras` (`pk`, `code`, `caption.text`, `user`, `image_versions2`, ...)
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import random
import re
import time
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from peyja.config import get_settings
from peyja.ingest.holdout import build_holdout_from_raw
from peyja.ingest.media import MediaArchiver, media_urls_from_raw
from peyja.models import MediaFile, Page, RawItem, utcnow

DEFAULT_IG_SHOPS: tuple[str, ...] = (
    "dokhtaronee_shop",
    "isatiis_cosmetics",
    "meisamatr",
    "rozhashop.ir",
    "rozhia.shop",
    "safirstores",
    "rojashop02",
)

_IG_APP_ID = "936619743392459"
_DESKTOP_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
_FALLBACK_GQL_OPS: tuple[tuple[str, str], ...] = (
    ("PolarisProfilePostsQuery", "39902305416081839"),
    ("PolarisProfilePostsTabContentQuery_connection", "29218603981099852"),
    ("PolarisProfilePostsQuery", "29156678094015645"),
    ("PolarisProfilePostsTabContentQuery_connection", "29305871032413073"),
    ("PolarisProfilePostsQuery", "29658130283778926"),
    ("PolarisProfilePostsQuery", "27014165781614298"),
)
_SKIP_WALK_KEYS = frozenset(
    {
        "carousel_media",
        "edge_sidecar_to_children",
        "clips_metadata",
        "coauthor_producers",
        "usertags",
        "edge_media_to_tagged_user",
    }
)
_JSON_SCRIPT_RE = re.compile(
    r'<script\b[^>]*type=["\']application/json["\'][^>]*>([\s\S]*?)</script>',
    re.IGNORECASE,
)


class IgLiteError(Exception):
    """خطای عمومی کرالر سبک اینستاگرام."""


class IgLiteAuthError(IgLiteError):
    """نیاز به ورود، 2FA یا checkpoint از سمت اینستاگرام."""


class IgLiteBlockedError(IgLiteError):
    """محدودیت نرخ (429) یا مسدودشدن موقت نشست."""


def safe_json_loads(raw_text: str) -> Any:
    """تجزیه‌ی JSON با حذف پیشوند ضدXSSI متا (`for (;;);`)."""
    if not raw_text:
        return None
    cleaned = re.sub(r"^\s*for\s*\(\s*;\s*;\s*\)\s*;?", "", raw_text).strip()
    if not cleaned or cleaned.startswith("<"):
        return None
    try:
        return json.loads(cleaned)
    except ValueError:
        return None


def extract_tokens_from_html(html: str) -> dict[str, Any]:
    """استخراج `fb_dtsg`، `lsd`، `csrftoken`، `jazoest` و `doc_id` از HTML اینستاگرام."""
    tokens: dict[str, Any] = {
        "fb_dtsg": "",
        "lsd": "",
        "csrftoken": "",
        "jazoest": "",
        "doc_ids": [],
    }
    if not html:
        return tokens
    for pat in (
        r'"DTSGInitialData"\s*,\s*\[\]\s*,\s*\{"token":"([^"]+)"',
        r'"dtsg"\s*:\s*\{"token":"([^"]+)"',
        r'name="fb_dtsg"\s+value="([^"]+)"',
    ):
        m = re.search(pat, html)
        if m:
            tokens["fb_dtsg"] = m.group(1)
            break
    m_lsd = re.search(r'"LSD"\s*,\s*\[\]\s*,\s*\{"token":"([^"]+)"', html)
    if m_lsd:
        tokens["lsd"] = m_lsd.group(1)
    m_csrf = re.search(r'"csrf_token":"([^"]+)"', html)
    if m_csrf:
        tokens["csrftoken"] = m_csrf.group(1)
    m_jaz = re.search(r'"jazoest"\s*:\s*"?(\d{4,6})"?|jazoest=(\d{4,6})', html)
    if m_jaz:
        tokens["jazoest"] = m_jaz.group(1) or m_jaz.group(2) or ""

    seen_ops: set[tuple[str, str]] = set()
    for pat in (
        r'"(PolarisProfilePosts(?:TabContent)?Query(?:_connection)?)"[\s\S]{0,220}?"(\d{12,22})"',
        r'"id":"(\d{12,22})"[\s\S]{0,180}?"(PolarisProfilePosts(?:TabContent)?Query(?:_connection)?)"',
    ):
        for m in re.finditer(pat, html):
            g1, g2 = m.group(1), m.group(2)
            op, doc_id = (g2, g1) if g1.isdigit() else (g1, g2)
            if (op, doc_id) not in seen_ops:
                seen_ops.add((op, doc_id))
                tokens["doc_ids"].append((op, doc_id))
    return tokens


def collect_media_nodes_from_tree(root: Any) -> list[dict[str, Any]]:
    """استخراج یکدست گره‌های پست از هر ساختار تودرتوی GraphQL / `data-sjs` / REST."""
    out: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    visited_ids: set[int] = set()
    stack: list[Any] = [root]

    while stack:
        cur = stack.pop()
        if not isinstance(cur, (dict, list)) or id(cur) in visited_ids:
            continue
        visited_ids.add(id(cur))

        if isinstance(cur, list):
            for item in reversed(cur):
                stack.append(item)
            continue

        has_code = bool(cur.get("code") or cur.get("shortcode"))
        has_media_fields = any(
            k in cur
            for k in (
                "caption",
                "edge_media_to_caption",
                "image_versions2",
                "display_url",
                "media_type",
            )
        )
        if has_code and has_media_fields:
            key = str(cur.get("pk") or cur.get("id") or cur.get("code") or cur.get("shortcode"))
            if key and key not in seen_keys:
                seen_keys.add(key)
                out.append(cur)
            continue

        for k, v in cur.items():
            if k in _SKIP_WALK_KEYS:
                continue
            if isinstance(v, (dict, list)):
                stack.append(v)
    return out


def _first_image_url_from_node(node: dict[str, Any]) -> list[str]:
    urls: list[str] = []
    for k in ("display_url", "thumbnail_src"):
        val = node.get(k)
        if isinstance(val, str) and val.startswith("http"):
            urls.append(val)
    sidecar = (node.get("edge_sidecar_to_children") or {}).get("edges") or []
    for edge in sidecar:
        child = (edge or {}).get("node") or {}
        if isinstance(child.get("display_url"), str):
            urls.append(child["display_url"])
    return list(dict.fromkeys(urls))


def normalize_ig_web_item(raw: dict[str, Any], username: str = "") -> dict[str, Any] | None:
    """تبدیل پاسخِ خام اینستاگرام (GraphQL v2/v1، `data-sjs`، REST یا خروجی مرورگر)
    به قالب استاندارد `RawItem.raw` هم‌سازگار با `@farmashop_aras`.
    """
    node_raw = raw.get("node")
    if isinstance(node_raw, dict):
        node: dict[str, Any] = node_raw
    elif isinstance(raw, dict):
        node = raw
    else:
        return None

    cap_raw = node.get("caption")
    if isinstance(cap_raw, dict):
        caption_text = str(cap_raw.get("text") or "").strip()
    elif isinstance(cap_raw, str):
        caption_text = cap_raw.strip()
    else:
        cap_edges = (node.get("edge_media_to_caption") or {}).get("edges") or []
        caption_text = (
            str(((cap_edges[0] or {}).get("node") or {}).get("text") or "").strip()
            if cap_edges
            else ""
        )

    user_val = node.get("user")
    user_raw: dict[str, Any] = user_val if isinstance(user_val, dict) else {}
    owner_val = node.get("owner")
    owner_raw: dict[str, Any] = owner_val if isinstance(owner_val, dict) else {}
    uname = (
        str(
            user_raw.get("username")
            or owner_raw.get("username")
            or node.get("username")
            or username
            or ""
        )
        .strip()
        .lstrip("@")
        .lower()
    )
    if not uname:
        return None

    pk = str(node.get("pk") or node.get("id") or node.get("ig_media_id") or "").strip()
    if "_" in pk and not node.get("pk"):
        pk = pk.split("_", 1)[0]
    code = str(node.get("code") or node.get("shortcode") or "").strip()
    if not pk and not code:
        return None
    if not pk:
        pk = code

    user_pk = user_raw.get("pk") or owner_raw.get("id") or ""
    full_name = str(user_raw.get("full_name") or owner_raw.get("full_name") or "")

    iv2 = node.get("image_versions2")
    if not isinstance(iv2, dict) or not (iv2.get("candidates") or []):
        cand_urls: list[str] = []
        if isinstance(node.get("image_url"), str) and node["image_url"].startswith("http"):
            cand_urls.append(node["image_url"])
        cand_urls.extend(_first_image_url_from_node(node))
        iv2 = {"candidates": [{"url": u} for u in dict.fromkeys(cand_urls)]}

    out: dict[str, Any] = {
        "pk": pk,
        "id": str(node.get("id") or (f"{pk}_{user_pk}" if user_pk else pk)),
        "code": code,
        "media_type": node.get("media_type") or (8 if node.get("carousel_media") else 1),
        "product_type": node.get("product_type") or "feed",
        "taken_at": node.get("taken_at") or node.get("taken_at_timestamp"),
        "like_count": node.get("like_count")
        if node.get("like_count") is not None
        else (
            (node.get("edge_liked_by") or node.get("edge_media_preview_like") or {}).get("count")
        ),
        "comment_count": node.get("comment_count")
        if node.get("comment_count") is not None
        else (node.get("edge_media_to_comment") or {}).get("count"),
        "caption": {"text": caption_text},
        "user": {"pk": user_pk, "username": uname, "full_name": full_name},
        "image_versions2": iv2,
        "carousel_media": node.get("carousel_media") or [],
        "_tag": f"ig_{uname}",
    }
    if isinstance(node.get("image_data_url"), str) and node["image_data_url"].startswith(
        "data:image/"
    ):
        out["image_data_url"] = node["image_data_url"]
    return out


class IgLiteCrawler:
    """کرالر سبک تک‌نشستِ اینستاگرام (GraphQL Relay + HTML `data-sjs` + REST fallback)."""

    def __init__(
        self,
        *,
        sessionid: str = "",
        csrftoken: str = "",
        ds_user_id: str = "",
        client: httpx.AsyncClient | None = None,
        delay_range: tuple[float, float] = (3.5, 6.5),
    ) -> None:
        self._sessionid = sessionid.strip()
        self._csrftoken = csrftoken.strip()
        self._ds_user_id = ds_user_id.strip()
        self._delay_range = delay_range
        self._owns_client = client is None
        self._fb_dtsg = ""
        self._lsd = ""
        self._jazoest = ""
        self._discovered_ops: list[tuple[str, str]] = list(_FALLBACK_GQL_OPS)
        self._winning_gql: tuple[str, str, str] | None = None  # (endpoint, op, doc_id)
        self._bootstrapped = False

        cookies: dict[str, str] = {}
        if self._sessionid:
            cookies["sessionid"] = self._sessionid
        if self._csrftoken:
            cookies["csrftoken"] = self._csrftoken
        if self._ds_user_id:
            cookies["ds_user_id"] = self._ds_user_id
        self._client = client or httpx.AsyncClient(
            timeout=25.0,
            follow_redirects=True,
            cookies=cookies,
            headers={
                "User-Agent": _DESKTOP_UA,
                "Accept": "*/*",
                "Accept-Language": "fa-IR,fa;q=0.9,en-US;q=0.8,en;q=0.7",
                "X-IG-App-ID": _IG_APP_ID,
                "X-Requested-With": "XMLHttpRequest",
            },
        )

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    def _request_headers(self, username: str = "", *, friendly_name: str = "") -> dict[str, str]:
        hdrs: dict[str, str] = {
            "X-IG-App-ID": _IG_APP_ID,
            "X-Requested-With": "XMLHttpRequest",
            "Referer": f"https://www.instagram.com/{username}/"
            if username
            else "https://www.instagram.com/",
        }
        csrf = self._csrftoken or str(self._client.cookies.get("csrftoken") or "")
        if csrf:
            hdrs["X-CSRFToken"] = csrf
        if self._lsd:
            hdrs["X-FB-LSD"] = self._lsd
        if friendly_name:
            hdrs["X-FB-Friendly-Name"] = friendly_name
        return hdrs

    def _ingest_tokens(self, tokens: dict[str, Any]) -> None:
        if tokens.get("fb_dtsg") and not self._fb_dtsg:
            self._fb_dtsg = str(tokens["fb_dtsg"])
        if tokens.get("lsd") and not self._lsd:
            self._lsd = str(tokens["lsd"])
        if tokens.get("csrftoken") and not self._csrftoken:
            self._csrftoken = str(tokens["csrftoken"])
        if tokens.get("jazoest") and not self._jazoest:
            self._jazoest = str(tokens["jazoest"])
        for op, doc_id in reversed(tokens.get("doc_ids") or []):
            if (op, doc_id) not in self._discovered_ops:
                self._discovered_ops.insert(0, (op, doc_id))

    @staticmethod
    def _build_gql_variables(username: str, op_name: str, per_shop: int) -> dict[str, Any]:
        base: dict[str, Any] = {
            "data": {
                "count": per_shop,
                "include_reel_media_seen_timestamp": True,
                "include_relationship_info": True,
                "latest_besties_reel_media": True,
                "latest_reel_media": True,
            },
            "username": username,
            "__relay_internal__pv__PolarisIsLoggedInrelayprovider": True,
            "__relay_internal__pv__PolarisShareSheetV3relayprovider": True,
            "__relay_internal__pv__PolarisMultiCaptionCarouselEnabledrelayprovider": True,
            "__relay_internal__pv__PolarisShortDramaEnabledrelayprovider": False,
            "__relay_internal__pv__PolarisSEOIsCrawlerrelayprovider": False,
            "__relay_internal__pv__PolarisThreadsStyleCommentsEnabledrelayprovider": False,
            "__relay_internal__pv__PolarisReelsRecoDebugOverlayEnabledrelayprovider": False,
            "__relay_internal__pv__PolarisProfileInformTreatmentBannerEnabledrelayprovider": False,
            "__relay_internal__pv__PolarisProfileRingsGradientEnabledrelayprovider": False,
            "__relay_internal__pv__PolarisDSA16EntrypointEnabledrelayprovider": False,
        }
        if "connection" in op_name:
            return {"after": None, "before": None, "first": per_shop, "last": None, **base}
        return base

    async def login_web(self, username: str, password: str) -> dict[str, Any]:
        """ورود سبک وب‌اپ به اینستاگرام (فقط وقتی `sessionid` آماده ندارید)."""
        boot = await self._client.get("https://www.instagram.com/accounts/login/")
        self._ingest_tokens(extract_tokens_from_html(boot.text))
        ts = int(time.time())
        enc_pw = f"#PWD_INSTAGRAM_BROWSER:0:{ts}:{password}"
        resp = await self._client.post(
            "https://www.instagram.com/api/v1/web/accounts/login/ajax/",
            data={
                "enc_password": enc_pw,
                "username": username.strip().lstrip("@"),
                "queryParams": "{}",
                "optIntoOneTap": "false",
            },
            headers={
                **self._request_headers(),
                "X-Instagram-AJAX": "1",
                "Origin": "https://www.instagram.com",
                "Referer": "https://www.instagram.com/accounts/login/",
            },
        )
        try:
            data = resp.json()
        except ValueError as e:
            raise IgLiteAuthError(
                f"پاسخ غیرJSON از ورود اینستاگرام (HTTP {resp.status_code})"
            ) from e

        if data.get("two_factor_required"):
            raise IgLiteAuthError(
                "اینستاگرام تأیید دومرحله‌ای (2FA) خواست؛ از `scripts/ig_lite_browser.js` در کنسول مرورگر یا کوکی `sessionid` استفاده کنید."
            )
        if data.get("checkpoint_url") or data.get("message") == "checkpoint_required":
            raise IgLiteAuthError(
                "اینستاگرام روی این نشست checkpoint امنیتی گذاشت؛ اکانت را در مرورگر تأیید کنید یا از `scripts/ig_lite_browser.js` استفاده کنید."
            )
        if not data.get("authenticated"):
            raise IgLiteAuthError(
                f"ورود ناموفق بود: {data.get('message') or 'نام کاربری/رمز یا محدودیت IP'}"
            )
        sid = str(self._client.cookies.get("sessionid") or "")
        if sid:
            self._sessionid = sid
        return {"ok": True, "user_id": data.get("userId"), "sessionid_ready": bool(sid)}

    async def _query_graphql(
        self, endpoint: str, op_name: str, doc_id: str, username: str, per_shop: int
    ) -> tuple[list[dict[str, Any]], str | None]:
        form: dict[str, str] = {
            "fb_api_caller_class": "RelayModern",
            "fb_api_req_friendly_name": op_name,
            "doc_id": doc_id,
            "variables": json.dumps(
                self._build_gql_variables(username, op_name, per_shop), separators=(",", ":")
            ),
            "server_timestamps": "true",
        }
        if self._fb_dtsg:
            form["fb_dtsg"] = self._fb_dtsg
        if self._lsd:
            form["lsd"] = self._lsd
        if self._jazoest:
            form["jazoest"] = self._jazoest

        resp = await self._client.post(
            endpoint,
            data=form,
            headers={
                **self._request_headers(username, friendly_name=op_name),
                "Origin": "https://www.instagram.com",
            },
        )
        if resp.status_code == 429:
            raise IgLiteBlockedError(f"اینستاگرام نرخ درخواست را محدود کرد (429) روی @{username}")
        if resp.status_code in (401, 403):
            raise IgLiteAuthError(
                f"نشست معتبر نیست یا نیاز به ورود مجدد دارد (HTTP {resp.status_code} روی @{username})"
            )
        parsed = safe_json_loads(resp.text)
        if not isinstance(parsed, dict):
            return [], "non_json"
        if parsed.get("errors"):
            msg = str((parsed["errors"][0] or {}).get("message") or "graphql_error")
            return collect_media_nodes_from_tree(parsed), msg
        return collect_media_nodes_from_tree(parsed), None

    async def _fetch_profile_html_and_sjs(self, username: str) -> list[dict[str, Any]]:
        resp = await self._client.get(
            f"https://www.instagram.com/{username}/",
            headers={
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Sec-Fetch-Dest": "document",
                "Sec-Fetch-Mode": "navigate",
                "Sec-Fetch-Site": "same-origin",
            },
        )
        if resp.status_code == 429:
            raise IgLiteBlockedError(f"محدودیت نرخ (429) روی صفحه‌ی @{username}")
        if resp.status_code in (401, 403):
            raise IgLiteAuthError(f"نیاز به نشست معتبر روی @{username} (HTTP {resp.status_code})")
        if resp.status_code != 200:
            return []

        self._ingest_tokens(extract_tokens_from_html(resp.text))
        self._bootstrapped = True

        nodes: list[dict[str, Any]] = []
        for match in _JSON_SCRIPT_RE.finditer(resp.text):
            parsed = safe_json_loads(match.group(1))
            if parsed is not None:
                nodes.extend(collect_media_nodes_from_tree(parsed))
        return nodes

    async def fetch_shop_posts(self, username: str, *, per_shop: int = 8) -> list[dict[str, Any]]:
        """دریافت ۱ صفحه‌ی سبک (حداکثر `per_shop` پست) از یک پیج اینستاگرام."""
        uname = username.strip().lstrip("@").lower()
        if not uname:
            return []

        items_raw: list[dict[str, Any]] = []

        # ۱) اگر پیش‌تر ترکیب برنده‌ی GraphQL کشف شده، ابتدا همان را بپرس
        if self._winning_gql is not None:
            ep, op_name, doc_id = self._winning_gql
            gql_nodes, _ = await self._query_graphql(ep, op_name, doc_id, uname, per_shop)
            items_raw = [n for n in gql_nodes if _node_matches_seller(n, uname)]

        # ۲) دریافت HTML پروفایل + داده‌ی توکار `data-sjs` (همزمان توکن‌های fb_dtsg/lsd/doc_id را هم برداشت می‌کند)
        if not items_raw and not self._bootstrapped:
            sjs_nodes = await self._fetch_profile_html_and_sjs(uname)
            items_raw = [n for n in sjs_nodes if _node_matches_seller(n, uname)]

        # ۳) جست‌وجو بین مسیرهای Relay GraphQL با توکن‌های برداشت‌شده
        if not items_raw and self._fb_dtsg:
            for op_name, doc_id in self._discovered_ops[:8]:
                for ep in (
                    "https://www.instagram.com/graphql/query",
                    "https://www.instagram.com/api/graphql",
                ):
                    gql_nodes, err = await self._query_graphql(ep, op_name, doc_id, uname, per_shop)
                    matched = [n for n in gql_nodes if _node_matches_seller(n, uname)]
                    if matched:
                        self._winning_gql = (ep, op_name, doc_id)
                        items_raw = matched
                        break
                    if err and err != "non_json":
                        break
                if items_raw:
                    break

        # ۴) در پیج‌های بعدی اگر هنوز خالی بود، یک بار `data-sjs` را بخوان و هر doc_id تازه را امتحان کن
        if not items_raw and self._bootstrapped:
            pre_ops = list(self._discovered_ops)
            sjs_nodes = await self._fetch_profile_html_and_sjs(uname)
            items_raw = [n for n in sjs_nodes if _node_matches_seller(n, uname)]
            if not items_raw:
                new_ops = [op for op in self._discovered_ops if op not in pre_ops]
                for op_name, doc_id in new_ops:
                    for ep in (
                        "https://www.instagram.com/graphql/query",
                        "https://www.instagram.com/api/graphql",
                    ):
                        gql_nodes, err = await self._query_graphql(
                            ep, op_name, doc_id, uname, per_shop
                        )
                        matched = [n for n in gql_nodes if _node_matches_seller(n, uname)]
                        if matched:
                            self._winning_gql = (ep, op_name, doc_id)
                            items_raw = matched
                            break
                        if err and err != "non_json":
                            break
                    if items_raw:
                        break

        # ۵) پشتیبانِ REST قدیمی (در صورت پاسخگو بودن روی نشست‌های خاص یا تست‌ها)
        if not items_raw:
            items_raw = await self._fetch_legacy_rest(uname, per_shop)

        normalized: list[dict[str, Any]] = []
        seen_keys: set[str] = set()
        for raw in items_raw:
            norm = normalize_ig_web_item(raw, username=uname)
            if norm is None or norm["user"]["username"] != uname:
                continue
            key = str(norm.get("pk") or norm.get("code") or "")
            if key in seen_keys:
                continue
            seen_keys.add(key)
            normalized.append(norm)
            if len(normalized) >= per_shop:
                break
        return normalized

    async def _fetch_legacy_rest(self, uname: str, per_shop: int) -> list[dict[str, Any]]:
        url_v1 = f"https://www.instagram.com/api/v1/feed/user/{uname}/username/"
        resp = await self._client.get(
            url_v1,
            params={"count": per_shop},
            headers=self._request_headers(uname),
        )
        if resp.status_code == 429:
            raise IgLiteBlockedError(f"اینستاگرام نرخ درخواست را محدود کرد (429) روی @{uname}")
        if resp.status_code in (401, 403):
            raise IgLiteAuthError(
                f"نشست معتبر نیست یا نیاز به ورود مجدد دارد (HTTP {resp.status_code} روی @{uname})"
            )

        items_raw: list[dict[str, Any]] = []
        if resp.status_code == 200:
            body = safe_json_loads(resp.text)
            if isinstance(body, dict):
                if body.get("message") == "checkpoint_required":
                    raise IgLiteAuthError("اینستاگرام checkpoint خواست — با مرورگر وارد شوید")
                if body.get("require_login") or body.get("message") == "login_required":
                    raise IgLiteAuthError("نشست نیاز به ورود (sessionid معتبر) دارد")
                if isinstance(body.get("items"), list):
                    items_raw = body["items"]

        if not items_raw and resp.status_code in (200, 404):
            prof = await self._client.get(
                "https://www.instagram.com/api/v1/users/web_profile_info/",
                params={"username": uname},
                headers=self._request_headers(uname),
            )
            if prof.status_code == 429:
                raise IgLiteBlockedError(f"محدودیت نرخ (429) در web_profile_info روی @{uname}")
            pdata = safe_json_loads(prof.text) if prof.status_code == 200 else None
            if isinstance(pdata, dict):
                user_obj = (pdata.get("data") or {}).get("user") or {}
                edges = (user_obj.get("edge_owner_to_timeline_media") or {}).get("edges") or []
                for edge in edges:
                    if isinstance(edge, dict):
                        node = dict(edge.get("node") or {})
                        if "owner" not in node and user_obj.get("username"):
                            node["owner"] = {
                                "id": user_obj.get("id"),
                                "username": user_obj.get("username"),
                                "full_name": user_obj.get("full_name") or "",
                            }
                        items_raw.append(node)
        return items_raw

    async def crawl_shops(
        self,
        usernames: list[str] | tuple[str, ...],
        *,
        per_shop: int = 8,
        max_total: int = 60,
        exclude_usernames: set[str] | None = None,
    ) -> dict[str, Any]:
        """کرال سبک روی فهرست فروشگاه‌های هدف — امن، کم‌درخواست و با حفظ نتایج تا لحظه‌ی خطا."""
        excluded = {u.lower() for u in (exclude_usernames or {"farmashop_aras"})}
        collected: list[dict[str, Any]] = []
        per_seller: dict[str, int] = {}
        stopped_reason: str | None = None

        targets = [u.strip().lstrip("@").lower() for u in usernames if u.strip()]
        targets = [u for u in dict.fromkeys(targets) if u not in excluded]

        for idx, uname in enumerate(targets):
            if len(collected) >= max_total:
                break
            if idx > 0 and self._delay_range[1] > 0:
                await asyncio.sleep(random.uniform(*self._delay_range))
            try:
                posts = await self.fetch_shop_posts(uname, per_shop=per_shop)
            except IgLiteError as e:
                stopped_reason = str(e)
                break
            except httpx.HTTPError as e:
                stopped_reason = f"خطای شبکه روی @{uname}: {type(e).__name__}"
                continue

            take = posts[: max(0, max_total - len(collected))]
            collected.extend(take)
            per_seller[uname] = len(take)

        return {
            "ok": len(collected) > 0,
            "count": len(collected),
            "sellers": per_seller,
            "stopped_reason": stopped_reason,
            "items": collected,
        }


def _node_matches_seller(node: dict[str, Any], target_username: str) -> bool:
    u = ((node.get("user") or node.get("owner") or {}).get("username") or "").strip().lower()
    return not u or u == target_username


async def _archive_data_url(
    session: AsyncSession, raw_item: RawItem, data_url: str, media_dir: Path
) -> MediaFile | None:
    m = re.match(r"^data:image/([a-zA-Z0-9+]+);base64,(.+)$", data_url, re.DOTALL)
    if not m:
        return None
    ext = m.group(1).lower().replace("jpeg", "jpg")
    try:
        data = base64.b64decode(m.group(2))
    except Exception:
        return None
    sha = hashlib.sha256(data).hexdigest()
    existing = (
        await session.execute(select(MediaFile).where(MediaFile.sha256 == sha))
    ).scalar_one_or_none()
    if existing:
        return existing
    sub = media_dir / sha[:2]
    sub.mkdir(parents=True, exist_ok=True)
    path = sub / f"{sha}.{ext}"
    path.write_bytes(data)
    mf = MediaFile(
        raw_item_id=raw_item.id,
        url_original=f"inline://{raw_item.ig_id}",
        sha256=sha,
        local_path=str(path),
    )
    session.add(mf)
    await session.flush()
    return mf


async def ingest_ig_raw_items(
    session: AsyncSession,
    raw_items: list[dict[str, Any]],
    *,
    download_media: bool = True,
    client: httpx.AsyncClient | None = None,
    media_dir: str | Path | None = None,
    build_holdout: bool = False,
    gold_path: Path = Path("evals/gold-100/samples.json"),
    out_candidates_path: Path = Path("evals/gold-100/batch2-candidates.json"),
    out_sheet_path: Path = Path("evals/gold-100/batch2-verification-sheet.html"),
    holdout_target: int = 30,
    max_per_seller: int = 6,
) -> dict[str, Any]:
    """واردکردن پست‌های خام اینستاگرامی به DB (`Page` + `RawItem` + `MediaFile`) + ساخت اختیاری holdout بچ ۲."""
    mdir = Path(media_dir or get_settings().media_dir)
    archiver = MediaArchiver(session, client=client, media_dir=mdir) if download_media else None
    normalized_all: list[dict[str, Any]] = []
    pages_cache: dict[str, Page] = {}
    new_media_files = 0

    try:
        for raw in raw_items:
            norm = normalize_ig_web_item(raw)
            if norm is None:
                continue
            uname = norm["user"]["username"]
            page = pages_cache.get(uname)
            if page is None:
                page = (
                    await session.execute(select(Page).where(Page.username == uname))
                ).scalar_one_or_none()
                if page is None:
                    user_pk = str(norm["user"].get("pk") or "") or None
                    page = Page(username=uname, ig_user_id=user_pk, source="ig_lite")
                    session.add(page)
                    await session.flush()
                pages_cache[uname] = page

            ig_id = str(norm["pk"])
            existing = (
                await session.execute(
                    select(RawItem).where(
                        RawItem.page_id == page.id,
                        RawItem.kind == "media",
                        RawItem.ig_id == ig_id,
                    )
                )
            ).scalar_one_or_none()
            if existing is not None:
                existing.raw = norm
                existing.fetched_at = utcnow()
                raw_item = existing
            else:
                raw_item = RawItem(page_id=page.id, kind="media", ig_id=ig_id, raw=norm)
                session.add(raw_item)
                await session.flush()

            local_img: str | None = None
            data_url = norm.get("image_data_url")
            if download_media and isinstance(data_url, str):
                mf = await _archive_data_url(session, raw_item, data_url, mdir)
                if mf is not None:
                    local_img = mf.local_path
                    new_media_files += 1
            elif archiver is not None:
                urls = media_urls_from_raw(norm)
                saved = await archiver.archive_urls(raw_item, urls, max_per_item=1)
                if saved:
                    local_img = saved[0].local_path
                    new_media_files += len(saved)

            if not local_img:
                first_mf = (
                    await session.execute(
                        select(MediaFile).where(MediaFile.raw_item_id == raw_item.id).limit(1)
                    )
                ).scalar_one_or_none()
                if first_mf is not None:
                    local_img = first_mf.local_path

            norm_copy = dict(norm)
            if local_img:
                norm_copy["image_path"] = local_img
            normalized_all.append(norm_copy)

        for p in pages_cache.values():
            p.last_synced_at = utcnow()
        await session.commit()
    finally:
        if archiver is not None and client is None:
            await archiver.aclose()

    result: dict[str, Any] = {
        "ok": len(normalized_all) > 0,
        "ingested_posts": len(normalized_all),
        "pages": sorted(pages_cache.keys()),
        "media_saved": new_media_files,
    }
    if build_holdout and normalized_all:
        holdout_res = build_holdout_from_raw(
            normalized_all,
            gold_path=gold_path,
            out_candidates_path=out_candidates_path,
            out_sheet_path=out_sheet_path,
            target=holdout_target,
            max_per_seller=max_per_seller,
            exclude_usernames={"farmashop_aras"},
        )
        result["holdout"] = holdout_res
    return result


def dump_raw_json(items: list[dict[str, Any]], out_path: Path) -> Path:
    """ذخیره‌ی فایل خام یکدست برای `peyja ingest-ig-raw` / `peyja import-holdout`."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(items, ensure_ascii=False, indent=2), encoding="utf-8")
    return out_path
