/**
 * کرالر بسیار سبک اینستاگرام درون مرورگر (بدون نصب هیچ ابزار اضافه، بدون ریسک checkpoint ورود)
 *
 * روش اجرا:
 * ۱) در مرورگر Chrome/Edge با اکانت تستی وارد https://www.instagram.com شوید.
 * ۲) کلید F12 (یا Ctrl+Shift+I) را بزنید ← تب Console.
 *    (اگر Chrome برای امنیت پیام "allow pasting" داد، یک بار تایپ کنید: allow pasting + Enter)
 * ۳) کل این کد را Paste و Enter کنید و ~۳۵ ثانیه صبر کنید.
 * ۴) فایل `ig-batch2-raw.json` خودکار دانلود می‌شود! همان فایل را در پیجا آپلود کنید
 *    یا دستور `peyja ingest-ig-raw ig-batch2-raw.json --build-holdout` را اجرا کنید.
 */
(async () => {
  const SHOPS = [
    "dokhtaronee_shop",
    "isatiis_cosmetics",
    "meisamatr",
    "rozhashop.ir",
    "yona_.cosmetic",
    "safirstores",
    "celine.shops",
  ];
  const PER_SHOP = 8; // سبک و ایمن برای اکانت تستی (در مجموع ~۵۶ پست)
  const IG_APP_ID = "936619743392459";

  const csrf = (document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/) || [])[1] || "";
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const jitter = () => sleep(3500 + Math.floor(Math.random() * 3000));

  const headers = {
    "X-IG-App-ID": IG_APP_ID,
    "X-Requested-With": "XMLHttpRequest",
    ...(csrf ? { "X-CSRFToken": csrf } : {}),
  };

  function normalizeItem(raw, uname) {
    const node = raw?.node || raw;
    if (!node) return null;
    const capText =
      typeof node.caption === "string"
        ? node.caption
        : node.caption?.text ??
          node.edge_media_to_caption?.edges?.[0]?.node?.text ??
          "";
    const userObj = node.user || node.owner || {};
    const username = (userObj.username || uname || "").toLowerCase();
    const pk = String(node.pk || node.id || "").split("_")[0];
    if (!pk || !username) return null;

    let candidates = node.image_versions2?.candidates || [];
    if (!candidates.length) {
      const u = node.display_url || node.thumbnail_src || node.image_url;
      if (u) candidates = [{ url: u }];
    }
    return {
      pk,
      id: String(node.id || pk),
      code: node.code || node.shortcode || "",
      media_type: node.media_type || 1,
      product_type: node.product_type || "feed",
      taken_at: node.taken_at || node.taken_at_timestamp || null,
      like_count:
        node.like_count ??
        node.edge_liked_by?.count ??
        node.edge_media_preview_like?.count ??
        null,
      comment_count:
        node.comment_count ?? node.edge_media_to_comment?.count ?? null,
      caption: { text: String(capText || "").trim() },
      user: {
        pk: userObj.pk || userObj.id || "",
        username,
        full_name: userObj.full_name || "",
      },
      image_versions2: { candidates },
      carousel_media: node.carousel_media || [],
      _tag: `ig_${username}`,
    };
  }

  const collected = [];
  for (let i = 0; i < SHOPS.length; i++) {
    const uname = SHOPS[i];
    if (i > 0) await jitter();
    console.log(`[${i + 1}/${SHOPS.length}] دریافت @${uname} ...`);

    let rawItems = [];
    const r1 = await fetch(
      `https://www.instagram.com/api/v1/feed/user/${encodeURIComponent(uname)}/username/?count=${PER_SHOP}`,
      { credentials: "include", headers }
    );
    if (r1.status === 429 || r1.status === 401) {
      console.warn(`⚠️ توقف محترمانه روی @${uname} (HTTP ${r1.status}) — داده‌های قبلی حفظ شد.`);
      break;
    }
    if (r1.ok) {
      const d1 = await r1.json().catch(() => ({}));
      if (Array.isArray(d1.items)) rawItems = d1.items;
    }

    if (!rawItems.length) {
      const r2 = await fetch(
        `https://www.instagram.com/api/v1/users/web_profile_info/?username=${encodeURIComponent(uname)}`,
        { credentials: "include", headers }
      );
      if (r2.status === 429 || r2.status === 401) {
        console.warn(`⚠️ توقف محترمانه روی @${uname} (HTTP ${r2.status})`);
        break;
      }
      if (r2.ok) {
        const d2 = await r2.json().catch(() => ({}));
        const uObj = d2?.data?.user || {};
        const edges = uObj?.edge_owner_to_timeline_media?.edges || [];
        rawItems = edges.map((e) => ({ ...(e?.node || {}), owner: uObj }));
      }
    }

    let kept = 0;
    for (const it of rawItems) {
      const norm = normalizeItem(it, uname);
      if (norm) {
        collected.push(norm);
        kept++;
        if (kept >= PER_SHOP) break;
      }
    }
    console.log(`  ✅ @${uname}: ${kept} پست ذخیره شد (جمع: ${collected.length})`);
  }

  const blob = new Blob([JSON.stringify(collected, null, 2)], {
    type: "application/json;charset=utf-8",
  });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = "ig-batch2-raw.json";
  document.body.appendChild(a);
  a.click();
  a.remove();
  console.log(`🎉 تمام! ${collected.length} پست در فایل ig-batch2-raw.json دانلود شد.`);
})();
