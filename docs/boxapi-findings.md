# BOXAPI-FINDINGS — گزارش فنی یافته‌های BoxAPI.ir / Sendbox

**تاریخ:** 2026-11-19 · **وضعیت:** به‌روزرسانی با پروب زنده‌ی API (کشف‌های عملی)

## ⚡ کشف‌های کلیدی از پروب زنده (۲۰۲۶-۱۱)

| کشف | جزئیات |
|---|---|
| **میزبان واقعی خط ①** | `https://api.sendbox.chat/api/v1` (نه boxapi.ir) |
| **فهرست اکانت‌ها** | `GET /service/accounts` + `X-Api-Key` → `{success, data:[{id, username, instagram_user_id, is_active, is_echo, expires_at}]}` |
| **اکشن‌ها async** | `POST /service/actions/{action}` body=`{"data":{...}}` → **202** `{accepted:true, action, event_id}` — نتیجه از وب‌هوک با `event_type: action.*` می‌آید |
| **اکشن ناشناخته** | 404 `{"message":"Unknown action"}` — اعتبارسنجی سمت سرور |
| **PATCH اکانت** | `PATCH /service/accounts/{id}` مجاز (PUT/PATCH/DELETE) — `is_echo` از همین‌جا روشن می‌شود ✅ |
| **قالب داده‌ی Data API** | `Authorization: Bearer` درست است؛ خطای `Invalid credentials format` = توکن متفاوت می‌خواهد (توکن جدا از dashboard boxapi.ir) |
| **پاکت پاسخ sendbox** | `{success, message, status_code, data}` — سبک Laravel |

## صفحه‌ی تست (فعال)
- **@mmohebbi111** · `account_id=01a1f2e8-ef75-72e6-8c43-5e68c1e7db3f` · `ig_user_id=17841429538161969`
- `is_active=true` · `is_echo=true` (توسط ما روشن شد — بازخورد فروشنده = سیگنال طلایی)
- اعتبار تا **2027-01-18**

## ۱. محصولات (سه خط)

| خط | آدرس | کاربرد ما | قیمت |
|---|---|---|---|
| ① Official DM/Comment API | `POST /service/actions/{action}` + `X-Api-Key` | حلقه‌ی رسمی: DM، کامنت، قالب محصول، وب‌هوک | ۷۹۰K/پیج → ۳۰۰K (۳۰+) + ۷ روز تست |
| ② Instagram Data API | `POST https://boxapi.ir/api/instagram/{cat}/{action}` + Bearer | ایندکس بازار، کرال عمومی | ۱۴ روز رایگان → ۶۰۰K/۲هزار |
| ③ AI API | `https://ai.boxapi.ir/ai/v1/chat/completions` (سازگار OpenAI) | VLM استخراج | ۱۴ روز رایگان → شارژ از ۱ دلار |

## ۲. جزئیات حیاتی ادغام

### وب‌هوک
- بدنه: `{event_id, event_type, account_id, data}` · `event_type`: `messaging` | `messaging_postbacks` | `comments` | `mentions` | `action.*`
- **HMAC-SHA256**: `HMAC(secret, "{timestamp}.{raw_body}")` → `X-BoxApi-Signature` (+ `-Timestamp`, `-Event-Id`)
- پنجره‌ی replay: **۳۰۰ ثانیه** · ⚠️ **raw body** قبل از parse · retry ۳×۶۰s · HTTPS الزامی
- فرمت کلید: `whsec_...` (پیشوند در HMAC پذیرفته می‌شود)
- `is_echo=true` ⇒ پیام‌های خود فروشنده هم در وب‌هوک (بازخورد طلایی برای seller_echo)

### اکشن‌های شناخته‌شده (خط ①)
`send_message` · `reply_comment` · `send_generic_template` (کارت محصول) · `send_attachment` ·
`private_reply` (کامنت → DM) · `conversation_conversations` · `get-post-comments` · `follow-status` · `list_posts`
- همه async: 202 + event_id → نتیجه در وب‌هوک
- خطای ۱۹۰ = انقضای توکن · پنجره‌ی ۲۴ ساعته / پاسخ ۷ روزه / Human-Agent

### خط ② — اندپوینت‌ها (۳۵ عدد، توکن جدا)
`user/(get_info_by_username|get_media{id,count≤12,max_id}|get_media_by_username|get_clips|get_stories{ids}|get_highlights|get_followers|get_similar_accounts|search…)` ·
`media/(get_info_by_shortcode|get_comments|get_likes_by_shortcode)` · `hashtag/*` · `location/*` · `comment/*` · `audio/*`
- rate limit: **۱۰ req/s** · ⚠️ `media_url` منقضی‌شونده → دانلود فوری (MediaArchiver)

## ۳. نکات حقوقی و ریسک
- Data API فقط صفحات عمومی — استفاده‌ی محافظه‌کارانه با BudgetGuard
- Official API بدون ریسک بن (توکن رسمی Meta) — مسیر اصلی بلندمدت
- طرح رایگان توسعه‌دهندگان: `/contact-us`

## ۴. نقشه‌ی ما به BoxAPI
| نیاز ما | خط | کد |
|---|---|---|
| کرال بازار، ایندکس | ② Data | `provider/boxapi_data.py` |
| حلقه‌ی رسمی DM/کامنت | ① Official | `provider/boxapi_official.py` |
| استخراج VLM | ③ AI | `provider/boxapi_ai.py` |
| تأیید وب‌هوک | ① | `ingest/webhook.py` |

## ۵. یافته‌های تکمیلی (placeholder)
- [ ] نرخ واقعی مصرف/شکست در عملیات
- [ ] نتایج حل حلقه‌ی رسمی با وب‌هوک واقعی
- [ ] مستندسازی event_typeهای مشاهده‌شده در عمل
