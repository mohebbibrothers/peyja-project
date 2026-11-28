/**
 * کرالر بسیار سبک و خودتطبیق‌شونده‌ی اینستاگرام درون مرورگر (نسخه‌ی ۲۰۲۶ — GraphQL Relay + HTML data-sjs + SPA Spy)
 *
 * روش اجرا:
 * ۱) در مرورگر Chrome/Edge با اکانت تستی وارد https://www.instagram.com شوید.
 * ۲) کلید F12 (یا Ctrl+Shift+I) ← تب Console.
 *    (اگر Chrome برای امنیت پیام "allow pasting" داد، یک بار تایپ کنید: allow pasting + Enter)
 * ۳) کل این کد را Paste و Enter کنید و ~۳۵ ثانیه صبر کنید.
 * ۴) فایل `ig-batch2-raw.json` خودکار دانلود می‌شود!
 */
(async () => {
  const SHOPS = [
    "dokhtaronee_shop",
    "isatiis_cosmetics",
    "meisamatr",
    "rozhashop.ir",
    "rozhia.shop",
    "safirstores",
    "rojashop02",
  ];
  const PER_SHOP = 8; // سبک و ایمن برای اکانت تستی (~۵۶ پست از ۷ فروشگاه)
  const IG_APP_ID = "936619743392459";

  // شناسه‌های شناخته‌شده‌ی Relay GraphQL اینستاگرام وب (پشتیبانِ کشف زنده)
  const FALLBACK_OPS = [
    { op: "PolarisProfilePostsQuery", docId: "39902305416081839" },
    { op: "PolarisProfilePostsTabContentQuery_connection", docId: "29218603981099852" },
    { op: "PolarisProfilePostsQuery", docId: "29156678094015645" },
    { op: "PolarisProfilePostsTabContentQuery_connection", docId: "29305871032413073" },
    { op: "PolarisProfilePostsQuery", docId: "29658130283778926" },
    { op: "PolarisProfilePostsQuery", docId: "27014165781614298" },
    { op: "PolarisProfilePostsQuery", docId: "28975909992013618" },
    { op: "PolarisProfilePostsQuery", docId: "9310670392322965" },
  ];

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const jitter = () => sleep(3500 + Math.floor(Math.random() * 3000));

  function getCookie(name) {
    const m = document.cookie.match(new RegExp(`(?:^|;\\s*)${name}=([^;]+)`));
    return m ? decodeURIComponent(m[1]) : "";
  }

  function safeJsonParse(rawText) {
    if (!rawText || typeof rawText !== "string") return null;
    const cleaned = rawText.trim().replace(/^for\s*\(\s*;\s*;\s*\)\s*;?/, "");
    if (!cleaned || cleaned.startsWith("<")) return null;
    try {
      return JSON.parse(cleaned);
    } catch {
      return null;
    }
  }

  function extractTokensFromHtml(htmlText) {
    const tokens = { fb_dtsg: "", lsd: "", csrftoken: "", jazoest: "", docIds: [] };
    if (!htmlText) return tokens;

    const dtsgMatch =
      htmlText.match(/"DTSGInitialData"\s*,\s*\[\]\s*,\s*\{"token":"([^"]+)"/) ||
      htmlText.match(/"dtsg"\s*:\s*\{"token":"([^"]+)"/) ||
      htmlText.match(/name="fb_dtsg"\s+value="([^"]+)"/);
    if (dtsgMatch) tokens.fb_dtsg = dtsgMatch[1];

    const lsdMatch =
      htmlText.match(/"LSD"\s*,\s*\[\]\s*,\s*\{"token":"([^"]+)"/) ||
      htmlText.match(/\["LSD",\[\],\{"token":"([^"]+)"\}/);
    if (lsdMatch) tokens.lsd = lsdMatch[1];

    const csrfMatch = htmlText.match(/"csrf_token":"([^"]+)"/);
    if (csrfMatch) tokens.csrftoken = csrfMatch[1];

    const jazMatch =
      htmlText.match(/"jazoest"\s*:\s*"?(\d{4,6})"?/) ||
      htmlText.match(/jazoest=(\d{4,6})/);
    if (jazMatch) tokens.jazoest = jazMatch[1];

    const opPatterns = [
      /"(PolarisProfilePosts(?:TabContent)?Query(?:_connection)?)"[^]{0,220}?"(\d{12,22})"/g,
      /"id":"(\d{12,22})"[^]{0,180}?"(PolarisProfilePosts(?:TabContent)?Query(?:_connection)?)"/g,
    ];
    for (const rx of opPatterns) {
      for (const m of htmlText.matchAll(rx)) {
        const isIdFirst = /^\d+$/.test(m[1]);
        tokens.docIds.push({ op: isIdFirst ? m[2] : m[1], docId: isIdFirst ? m[1] : m[2] });
      }
    }
    return tokens;
  }

  function discoverRelayOpsFromWindow() {
    const out = [];
    const modNames = [
      "PolarisProfilePostsQuery$Parameters",
      "PolarisProfilePostsTabContentQuery_connection$Parameters",
      "PolarisProfilePostsQuery_instagramRelayOperation",
      "PolarisProfilePostsTabContentQuery_connection_instagramRelayOperation",
    ];
    for (const name of modNames) {
      try {
        if (typeof window.require === "function") {
          const mod = window.require(name);
          const id = typeof mod === "string" ? mod : mod?.params?.id || mod?.id || "";
          if (/^\d{10,25}$/.test(String(id))) {
            out.push({
              op: name.replace(/(\$Parameters|_instagramRelayOperation)$/, ""),
              docId: String(id),
            });
          }
        }
      } catch {}
    }
    try {
      const dtsg = typeof window.require === "function" ? window.require("DTSGInitialData")?.token : "";
      const lsd = typeof window.require === "function" ? window.require("LSD")?.token : "";
      return { ops: out, fb_dtsg: dtsg || "", lsd: lsd || "" };
    } catch {
      return { ops: out, fb_dtsg: "", lsd: "" };
    }
  }

  const SKIP_WALK_KEYS = new Set([
    "carousel_media",
    "edge_sidecar_to_children",
    "clips_metadata",
    "coauthor_producers",
    "usertags",
    "edge_media_to_tagged_user",
  ]);

  function collectMediaNodesFromTree(root, out = [], seen = new Set()) {
    const stack = [root];
    const visited = new Set();
    while (stack.length) {
      const cur = stack.pop();
      if (!cur || typeof cur !== "object" || visited.has(cur)) continue;
      visited.add(cur);

      if (Array.isArray(cur)) {
        for (let i = cur.length - 1; i >= 0; i--) stack.push(cur[i]);
        continue;
      }

      const looksLikePost =
        (cur.code || cur.shortcode) &&
        (cur.caption !== undefined ||
          cur.edge_media_to_caption !== undefined ||
          cur.image_versions2 !== undefined ||
          cur.display_url !== undefined ||
          cur.media_type !== undefined);
      if (looksLikePost) {
        const key = String(cur.pk || cur.id || cur.code || cur.shortcode);
        if (key && !seen.has(key)) {
          seen.add(key);
          out.push(cur);
        }
        continue;
      }

      for (const [k, v] of Object.entries(cur)) {
        if (SKIP_WALK_KEYS.has(k)) continue;
        if (v && typeof v === "object") stack.push(v);
      }
    }
    return out;
  }

  function normalizeItem(raw, fallbackUsername) {
    const node = raw?.node || raw;
    if (!node || typeof node !== "object") return null;

    const capText =
      typeof node.caption === "string"
        ? node.caption
        : node.caption?.text ??
          node.edge_media_to_caption?.edges?.[0]?.node?.text ??
          "";

    const userObj = node.user || node.owner || {};
    const username = String(
      userObj.username || node.username || fallbackUsername || ""
    )
      .trim()
      .replace(/^@/, "")
      .toLowerCase();

    let pk = String(node.pk || node.id || "").trim();
    if (pk.includes("_") && !node.pk) pk = pk.split("_")[0];
    const code = String(node.code || node.shortcode || "").trim();
    if ((!pk && !code) || !username) return null;
    if (!pk) pk = code;

    let candidates = node.image_versions2?.candidates || [];
    if (!candidates.length) {
      const urls = [];
      if (node.display_url) urls.push(node.display_url);
      if (node.thumbnail_src) urls.push(node.thumbnail_src);
      if (node.image_url) urls.push(node.image_url);
      const sidecar = node.edge_sidecar_to_children?.edges || [];
      for (const e of sidecar) {
        if (e?.node?.display_url) urls.push(e.node.display_url);
      }
      candidates = [...new Set(urls)].map((url) => ({ url }));
    }

    return {
      pk,
      id: String(node.id || pk),
      code,
      media_type: node.media_type || (node.carousel_media?.length ? 8 : 1),
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

  function buildVariables(uname, opName, customTemplate = null) {
    if (customTemplate && typeof customTemplate === "object") {
      const cloned = JSON.parse(JSON.stringify(customTemplate));
      cloned.username = uname;
      if (cloned.data && typeof cloned.data === "object") cloned.data.count = PER_SHOP;
      if ("first" in cloned) cloned.first = PER_SHOP;
      if ("after" in cloned) cloned.after = null;
      return cloned;
    }
    const base = {
      data: {
        count: PER_SHOP,
        include_reel_media_seen_timestamp: true,
        include_relationship_info: true,
        latest_besties_reel_media: true,
        latest_reel_media: true,
      },
      username: uname,
      __relay_internal__pv__PolarisIsLoggedInrelayprovider: true,
      __relay_internal__pv__PolarisShareSheetV3relayprovider: true,
      __relay_internal__pv__PolarisMultiCaptionCarouselEnabledrelayprovider: true,
      __relay_internal__pv__PolarisShortDramaEnabledrelayprovider: false,
      __relay_internal__pv__PolarisSEOIsCrawlerrelayprovider: false,
      __relay_internal__pv__PolarisThreadsStyleCommentsEnabledrelayprovider: false,
      __relay_internal__pv__PolarisReelsRecoDebugOverlayEnabledrelayprovider: false,
      __relay_internal__pv__PolarisProfileInformTreatmentBannerEnabledrelayprovider: false,
      __relay_internal__pv__PolarisProfileRingsGradientEnabledrelayprovider: false,
      __relay_internal__pv__PolarisDSA16EntrypointEnabledrelayprovider: false,
    };
    if (opName.includes("connection")) {
      return { after: null, before: null, first: PER_SHOP, last: null, ...base };
    }
    return base;
  }

  const winDisc = discoverRelayOpsFromWindow();
  const domTokens = extractTokensFromHtml(document.documentElement.innerHTML);
  const state = {
    fb_dtsg:
      winDisc.fb_dtsg ||
      document.querySelector('input[name="fb_dtsg"]')?.value ||
      domTokens.fb_dtsg ||
      "",
    lsd: winDisc.lsd || domTokens.lsd || "",
    csrftoken: getCookie("csrftoken") || domTokens.csrftoken || "",
    jazoest: domTokens.jazoest || "",
    winning: null,
    opsPool: [],
    rateLimited: false,
    lastError: "",
  };

  function pushOpCandidate(op, docId) {
    if (!docId || state.opsPool.some((x) => x.docId === docId && x.op === op)) return;
    state.opsPool.push({ op, docId });
  }
  for (const x of [...winDisc.ops, ...domTokens.docIds, ...FALLBACK_OPS]) {
    pushOpCandidate(x.op, x.docId);
  }

  async function queryGraphQL(endpoint, op, docId, varsObj) {
    const form = new URLSearchParams();
    if (state.fb_dtsg) form.set("fb_dtsg", state.fb_dtsg);
    if (state.lsd) form.set("lsd", state.lsd);
    if (state.jazoest) form.set("jazoest", state.jazoest);
    form.set("fb_api_caller_class", "RelayModern");
    form.set("fb_api_req_friendly_name", op);
    form.set("doc_id", docId);
    form.set("variables", JSON.stringify(varsObj));
    form.set("server_timestamps", "true");

    const hdrs = {
      "Content-Type": "application/x-www-form-urlencoded",
      "X-IG-App-ID": IG_APP_ID,
      "X-FB-Friendly-Name": op,
      "X-Requested-With": "XMLHttpRequest",
      ...(state.csrftoken ? { "X-CSRFToken": state.csrftoken } : {}),
      ...(state.lsd ? { "X-FB-LSD": state.lsd } : {}),
    };

    const res = await fetch(endpoint, {
      method: "POST",
      credentials: "include",
      headers: hdrs,
      body: form.toString(),
    });
    if (res.status === 429) {
      state.rateLimited = true;
      state.lastError = "HTTP 429 rate_limit";
      return { nodes: [], status: 429, err: "429_rate_limit" };
    }
    const text = await res.text();
    const json = safeJsonParse(text);
    if (!json) {
      state.lastError = `HTTP ${res.status} non_json`;
      return { nodes: [], status: res.status, err: "non_json" };
    }
    if (json.errors?.length) {
      const msg = json.errors[0]?.message || json.errors[0]?.summary || "graphql_error";
      state.lastError = `GQL(${docId}): ${msg}`;
      return { nodes: collectMediaNodesFromTree(json), status: res.status, err: msg };
    }
    return { nodes: collectMediaNodesFromTree(json), status: res.status, err: null };
  }

  async function fetchHtmlDataSjs(uname) {
    const res = await fetch(`https://www.instagram.com/${encodeURIComponent(uname)}/`, {
      credentials: "include",
      headers: { Accept: "text/html,application/xhtml+xml" },
    });
    if (res.status === 429) {
      state.rateLimited = true;
      state.lastError = "HTTP 429 on HTML profile";
      return [];
    }
    if (!res.ok) {
      state.lastError = `HTML profile HTTP ${res.status}`;
      return [];
    }
    const html = await res.text();
    const fresh = extractTokensFromHtml(html);
    if (fresh.fb_dtsg && !state.fb_dtsg) state.fb_dtsg = fresh.fb_dtsg;
    if (fresh.lsd && !state.lsd) state.lsd = fresh.lsd;
    if (fresh.jazoest && !state.jazoest) state.jazoest = fresh.jazoest;
    for (const x of fresh.docIds.reverse()) {
      if (!state.opsPool.some((y) => y.docId === x.docId && y.op === x.op)) {
        state.opsPool.unshift(x);
      }
    }

    // استخراج مستقیم <script type="application/json"> بدون DOMParser (سازگار ۱۰۰٪ با Trusted Types CSP اینستاگرام)
    const nodes = [];
    const scRe = /<script\b[^>]*type=["']application\/json["'][^>]*>([\s\S]*?)<\/script>/gi;
    for (const m of html.matchAll(scRe)) {
      const parsed = safeJsonParse(m[1]);
      if (parsed) collectMediaNodesFromTree(parsed, nodes);
    }
    if (nodes.length) return nodes;

    // پشتیبان سریع JSON هم‌دامنه (?__a=1&__d=dis)
    const rA1 = await fetch(
      `https://www.instagram.com/${encodeURIComponent(uname)}/?__a=1&__d=dis`,
      {
        credentials: "include",
        headers: { "X-IG-App-ID": IG_APP_ID, "X-Requested-With": "XMLHttpRequest" },
      }
    );
    if (rA1.ok) {
      const pA1 = safeJsonParse(await rA1.text());
      if (pA1) return collectMediaNodesFromTree(pA1);
    }
    return [];
  }

  async function captureViaSpaSpy(uname) {
    const origFetch = window.fetch;
    const origXhrOpen = XMLHttpRequest.prototype.open;
    const origXhrSend = XMLHttpRequest.prototype.send;
    const origUrl = window.location.pathname + window.location.search;
    const capturedNodes = [];

    const inspectBodyParams = (bodyStr) => {
      if (typeof bodyStr !== "string" || !bodyStr.includes("PolarisProfilePosts")) return null;
      const p = new URLSearchParams(bodyStr);
      const docId = p.get("doc_id");
      const op = p.get("fb_api_req_friendly_name") || "PolarisProfilePostsQuery";
      const vars = safeJsonParse(p.get("variables") || "");
      return docId && vars ? { op, docId, vars } : null;
    };

    window.fetch = async function (...args) {
      const reqInfo = args[0];
      const init = args[1] || {};
      const url = typeof reqInfo === "string" ? reqInfo : reqInfo?.url || "";
      const meta = inspectBodyParams(typeof init.body === "string" ? init.body : "");
      const resp = await origFetch.apply(this, args);
      if (url.includes("graphql")) {
        try {
          const parsed = safeJsonParse(await resp.clone().text());
          if (parsed) {
            const nodes = collectMediaNodesFromTree(parsed);
            if (nodes.length) {
              capturedNodes.push(...nodes);
              if (meta) {
                state.winning = {
                  endpoint: url.startsWith("http")
                    ? url
                    : "https://www.instagram.com/graphql/query",
                  op: meta.op,
                  docId: meta.docId,
                  varsTemplate: meta.vars,
                };
              }
            }
          }
        } catch {}
      }
      return resp;
    };

    XMLHttpRequest.prototype.open = function (method, url, ...rest) {
      this.__igLiteUrl = String(url || "");
      return origXhrOpen.call(this, method, url, ...rest);
    };
    XMLHttpRequest.prototype.send = function (body) {
      const meta = inspectBodyParams(typeof body === "string" ? body : "");
      this.addEventListener("load", () => {
        if ((this.__igLiteUrl || "").includes("graphql")) {
          const parsed = safeJsonParse(this.responseText || "");
          if (parsed) {
            const nodes = collectMediaNodesFromTree(parsed);
            if (nodes.length) {
              capturedNodes.push(...nodes);
              if (meta) {
                state.winning = {
                  endpoint: this.__igLiteUrl.startsWith("http")
                    ? this.__igLiteUrl
                    : "https://www.instagram.com/graphql/query",
                  op: meta.op,
                  docId: meta.docId,
                  varsTemplate: meta.vars,
                };
              }
            }
          }
        }
      });
      return origXhrSend.call(this, body);
    };

    try {
      window.history.pushState({}, "", `/${uname}/`);
      window.dispatchEvent(new PopStateEvent("popstate"));
      for (let t = 0; t < 12 && !capturedNodes.length; t++) {
        await sleep(400);
      }
    } finally {
      window.fetch = origFetch;
      XMLHttpRequest.prototype.open = origXhrOpen;
      XMLHttpRequest.prototype.send = origXhrSend;
      try {
        window.history.replaceState({}, "", origUrl || "/");
      } catch {}
    }
    return capturedNodes;
  }

  async function fetchShopNodes(uname) {
    if (state.winning) {
      const w = state.winning;
      const vars = buildVariables(uname, w.op, w.varsTemplate);
      const r = await queryGraphQL(w.endpoint, w.op, w.docId, vars);
      if (r.nodes.length) return { nodes: r.nodes, via: `graphql(${w.docId})` };
      if (state.rateLimited) return { nodes: [], via: "rate_limited" };
    }

    if (!state.fb_dtsg) {
      const sjsNodes = await fetchHtmlDataSjs(uname);
      if (sjsNodes.length) return { nodes: sjsNodes, via: "html_data_sjs" };
      if (state.rateLimited) return { nodes: [], via: "rate_limited" };
    }

    const endpoints = [
      "https://www.instagram.com/graphql/query",
      "https://www.instagram.com/api/graphql",
    ];
    for (const candidate of state.opsPool.slice(0, 10)) {
      const vars = buildVariables(uname, candidate.op);
      for (const ep of endpoints) {
        const r = await queryGraphQL(ep, candidate.op, candidate.docId, vars);
        if (state.rateLimited) return { nodes: [], via: "rate_limited" };
        if (r.nodes.length) {
          state.winning = {
            endpoint: ep,
            op: candidate.op,
            docId: candidate.docId,
            varsTemplate: vars,
          };
          return { nodes: r.nodes, via: `graphql(${candidate.docId})` };
        }
        if (r.err && r.err !== "non_json") break;
      }
    }

    const preOpsCount = state.opsPool.length;
    const htmlNodes = await fetchHtmlDataSjs(uname);
    if (htmlNodes.length) return { nodes: htmlNodes, via: "html_data_sjs" };
    if (state.rateLimited) return { nodes: [], via: "rate_limited" };

    // اگر HTML پیج doc_id تازه‌ای کشف کرد، فوراً همان را تست کن
    if (state.opsPool.length > preOpsCount) {
      const added = state.opsPool.slice(0, state.opsPool.length - preOpsCount);
      for (const candidate of added) {
        const vars = buildVariables(uname, candidate.op);
        for (const ep of endpoints) {
          const r = await queryGraphQL(ep, candidate.op, candidate.docId, vars);
          if (state.rateLimited) return { nodes: [], via: "rate_limited" };
          if (r.nodes.length) {
            state.winning = { endpoint: ep, op: candidate.op, docId: candidate.docId, varsTemplate: vars };
            return { nodes: r.nodes, via: `graphql(${candidate.docId})` };
          }
          if (r.err && r.err !== "non_json") break;
        }
      }
    }

    const spaNodes = await captureViaSpaSpy(uname);
    if (spaNodes.length) return { nodes: spaNodes, via: "spa_observer" };

    return { nodes: [], via: `empty(${state.lastError || "no_posts"})` };
  }

  console.log(
    `🚀 شروع کرال سبک ۲۰۲۶ روی ${SHOPS.length} فروشگاه (fb_dtsg=${Boolean(state.fb_dtsg)}, ops=${state.opsPool.length})...`
  );

  const collected = [];
  for (let i = 0; i < SHOPS.length; i++) {
    const uname = SHOPS[i];
    if (i > 0) await jitter();
    console.log(`[${i + 1}/${SHOPS.length}] دریافت @${uname} ...`);

    const { nodes, via } = await fetchShopNodes(uname);
    if (state.rateLimited) {
      console.warn(`⚠️ توقف محترمانه روی @${uname} (${via}) — داده‌های قبلی حفظ شد.`);
      break;
    }

    let kept = 0;
    const seenCodes = new Set();
    for (const rawNode of nodes) {
      const norm = normalizeItem(rawNode, uname);
      if (!norm) continue;
      if (norm.user.username !== uname) continue;
      const key = norm.pk || norm.code;
      if (seenCodes.has(key)) continue;
      seenCodes.add(key);
      collected.push(norm);
      kept++;
      if (kept >= PER_SHOP) break;
    }
    console.log(`  ✅ @${uname}: ${kept} پست ذخیره شد [${via}] (جمع کل: ${collected.length})`);
  }

  if (!collected.length) {
    console.error(
      "❌ هیچ پستی دریافت نشد (فایل خالی دانلود نشد). آخرین خطا:",
      state.lastError || "نامشخص",
      "\n💡 کافی است خروجی همین کنسول را بفرستید تا دقیقاً بر اساس لاگ صفحه‌تان تنظیمش کنم."
    );
    return;
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
  console.log(`🎉 تمام! ${collected.length} پست واقعی در فایل ig-batch2-raw.json دانلود شد.`);
})();
