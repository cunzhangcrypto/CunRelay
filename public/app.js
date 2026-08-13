/* CunRelay console front-end logic.
   数据源统一：优先 fetch('data.json')（GitHub Actions 生成、随 Pages 部署），
   本地开发时回退到 /api/* 接口。 */
"use strict";

const $ = (id) => document.getElementById(id);

const PLATFORM_LABEL = {
  telegram: { text: "Telegram", cls: "plat-tg", short: "TG" },
  x:        { text: "X (Twitter)", cls: "plat-x", short: "X" },
  threads:  { text: "Threads", cls: "plat-th", short: "TH" },
};

const STATUS_LABEL = {
  queued:    { text: "待发送", cls: "queued" },
  published: { text: "已发布", cls: "published" },
  failed:    { text: "失败",   cls: "failed" },
};

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

function pill(cls, label, dot = true) {
  return `<span class="pill ${cls}">${dot ? "<i></i>" : ""}${esc(label)}</span>`;
}

function videoId(itemId) {
  return String(itemId || "").split(":")[1] || itemId || "";
}

function youtubeThumb(itemId) {
  const v = videoId(itemId);
  return v ? `https://img.youtube.com/vi/${v}/hqdefault.jpg` : null;
}

function shortTime(iso) {
  if (!iso) return "—";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

/* ── 底部版权：XOR 混淆，运行时解码注入，源码无明文 ─────────────── */
// 密钥仅用于还原；明文与链接均不直接出现在 HTML/JS 源码中
const FOOTER_KEY = "CunRelay@2026#";
const FOOTER_ENC = [234, 85, 92, 98, 87, 90, 65, 206, 96, 14, 81, 18, 94, 81, 38, 19, 83, 112, 13, 24, 21, 9, 51, 8, 31, 29, 85, 89, 47, 20, 12, 124, 1, 9, 23, 91, 96, 70, 81, 64, 81, 70, 55, 72, 76, 13, 7, 0, 0, 23, 43, 16, 16, 64, 83, 79, 126, 87, 0, 61, 10, 28, 4, 23, 37, 64, 18, 12, 26471, 38236, 23517, 39673, 23498, 114, 6, 22, 13, 24, 34, 28, 84, 87, 64, 31, 108, 20, 80, 114, 210, 76, 93, 24, 96, 90, 66, 87, 80, 30, 97, 29, 26, 38, 21, 31, 91, 86, 111, 69, 71, 69, 24, 64, 54, 27, 20, 58, 4, 2, 6, 27, 44, 93, 87, 28, 85, 76, 46, 87, 78, 38, 4, 30, 6, 28, 52, 15, 18, 109, 84, 79, 34, 27, 5, 112, 69, 30, 4, 21, 125, 16, 94, 93, 89, 83, 38, 27, 11, 32, 71, 82, 26416, 38150, 21274, 23440, 16, 81, 67, 77, 57, 29, 15, 60, 2, 14, 13, 22, 39, 28, 83, 93, 91, 31, 108, 20, 80];

function renderFooter() {
  const key = FOOTER_KEY;
  $("foot-copy").innerHTML = FOOTER_ENC
    .map((c, i) => String.fromCharCode(c ^ key.charCodeAt(i % key.length)))
    .join("");
}

/* ── 数据加载：data.json（线上静态）→ /api/*（本地回退） ────── */
async function getData() {
  try {
    const r = await fetch("data.json", { cache: "no-store" });
    if (r.ok) return await r.json();
  } catch (e) { /* fall through to API */ }

  const [st, src, sh, cfg, posts, logs] = await Promise.all([
    fetch("/api/stats").then((r) => r.json()),
    fetch("/api/sources").then((r) => r.json()),
    fetch("/api/sheets").then((r) => r.json()),
    fetch("/api/config").then((r) => r.json()),
    fetch("/api/posts?limit=5000").then((r) => r.json()),
    fetch("/api/logs?limit=5000").then((r) => r.json()),
  ]);
  return {
    generated_at: new Date().toISOString(),
    sources: src,
    sheets: sh,
    auto_refresh: cfg.auto_refresh,
    stats: st.posts,
    breakdown: st.breakdown,
    success_rate: st.success_rate,
    posts: posts.map((p) => ({ ...p, thumb_url: youtubeThumb(p.video_id) })),
    logs,
  };
}

/* ── 渲染 ───────────────────────────────────────────────────── */
function renderStats(d) {
  $("st-queued").textContent = d.stats.queued;
  $("st-published").textContent = d.stats.published;
  $("st-failed").textContent = d.stats.failed;
  $("st-rate").textContent = d.success_rate === null || d.success_rate === undefined
    ? "—" : `${d.success_rate}%`;

  const chips = (elId, status) => {
    const per = (d.breakdown && d.breakdown[status]) || {};
    const entries = Object.entries(per);
    const el = $(elId);
    if (!entries.length) {
      el.innerHTML = `<span class="stat-chip empty">—</span>`;
      return;
    }
    el.innerHTML = entries.map(([p, n]) => {
      const short = (PLATFORM_LABEL[p] || {}).short || p;
      return `<span class="stat-chip">${short} ×${n}</span>`;
    }).join("");
  };
  chips("ch-queued", "queued");
  chips("ch-published", "published");
  chips("ch-failed", "failed");
}

function renderSources(d) {
  const names = (d.sources || []).map((s) => s.name).filter(Boolean);
  $("src-names").textContent = names.length ? names.join(" · ") : "未配置";
  const tag = $("source-tag");
  const first = (d.sources || []).find((s) => s.url);
  if (first && first.url) {
    tag.href = first.url;
    tag.classList.add("linkable");
  } else {
    tag.removeAttribute("href");
    tag.classList.remove("linkable");
  }
}

function renderSheets(d) {
  const link = $("sheets-link");
  const s = d.sheets || {};
  if (s.enabled && s.url) {
    link.href = s.url;
    link.classList.remove("disabled");
  } else {
    link.removeAttribute("href");
    link.classList.add("disabled");
    link.textContent = "同步 Google Sheets 未配置";
  }
}

/* ── 分页：每页 8 条，前端切片，智能页码 ────────────────────── */
const PAGE_SIZE = 8;
const pageState = { posts: 1, logs: 1 };

function pagerNumbers(current, total) {
  if (total <= 7) return Array.from({ length: total }, (_, i) => i + 1);
  const set = new Set([1, total, current]);
  for (let p = current - 2; p <= current + 2; p++) {
    if (p >= 1 && p <= total) set.add(p);
  }
  return [...set].sort((a, b) => a - b);
}

function renderPager(elId, key, total) {
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  let page = pageState[key];
  if (page > totalPages) page = totalPages;
  if (page < 1) page = 1;
  pageState[key] = page;

  let html = `<button class="pg" data-key="${key}" data-pg="${page - 1}"${page <= 1 ? " disabled" : ""}>‹</button>`;
  let last = 0;
  for (const p of pagerNumbers(page, totalPages)) {
    if (last && p - last > 1) html += `<span class="pg-ellipsis">…</span>`;
    html += `<button class="pg${p === page ? " active" : ""}" data-key="${key}" data-pg="${p}">${p}</button>`;
    last = p;
  }
  html += `<button class="pg" data-key="${key}" data-pg="${page + 1}"${page >= totalPages ? " disabled" : ""}>›</button>`;
  html += `<span class="pg-info">第 ${page} / ${totalPages} 页 · 共 ${total} 条</span>`;
  $(elId).innerHTML = html;
}

function renderPosts(d) {
  const platform = $("f-platform").value;
  const status = $("f-status").value;
  let rows = (d.posts || []);
  if (platform !== "all") rows = rows.filter((p) => p.platform === platform);
  if (status !== "all") rows = rows.filter((p) => p.status === status);

  const total = rows.length;
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  if (pageState.posts > totalPages) pageState.posts = totalPages;
  const start = (pageState.posts - 1) * PAGE_SIZE;
  rows = rows.slice(start, start + PAGE_SIZE);

  const body = $("posts-body");
  if (!rows.length) {
    body.innerHTML = `<tr><td colspan="7" class="empty">暂无记录</td></tr>`;
    renderPager("pager-posts", "posts", total);
    return;
  }

  body.innerHTML = rows.map((r) => {
    const pl = PLATFORM_LABEL[r.platform] || { text: r.platform, cls: "" };
    const st = STATUS_LABEL[r.status] || { text: r.status, cls: "" };
    const thumb = r.thumb_url || "no-thumb.svg";
    const err = r.error
      ? `<span class="msg err">${esc(r.error)}</span>`
      : `<span class="msg ok">${esc(r.published_at ? "发布成功" : "等待中")}</span>`;
    return `<tr>
      <td class="thumb-cell"><img class="thumb" loading="lazy"
        src="${esc(thumb)}" alt=""
        onerror="this.style.visibility='hidden'"></td>
      <td class="video-cell">
        <div class="video-title">${esc(r.video_title)}</div>
        <div class="video-sub"><a href="${esc(r.video_url)}" target="_blank">YouTube ↗</a></div>
      </td>
      <td>${pill(pl.cls, pl.text)}</td>
      <td>${pill(st.cls, st.text)}</td>
      <td class="time">${shortTime(r.send_at)}</td>
      <td class="time">${shortTime(r.published_at)}</td>
      <td>${err}</td>
    </tr>`;
  }).join("");
  renderPager("pager-posts", "posts", total);
}

function renderLogs(d) {
  let rows = (d.logs || []).slice();
  const total = rows.length;
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
  if (pageState.logs > totalPages) pageState.logs = totalPages;
  const start = (pageState.logs - 1) * PAGE_SIZE;
  rows = rows.slice(start, start + PAGE_SIZE);

  const body = $("logs-body");
  if (!rows.length) {
    body.innerHTML = `<tr><td colspan="5" class="empty">暂无发布日志</td></tr>`;
    renderPager("pager-logs", "logs", total);
    return;
  }
  body.innerHTML = rows.map((r) => {
    const pl = PLATFORM_LABEL[r.platform] || { text: r.platform, cls: "" };
    const ok = r.status === "success";
    const st = { text: ok ? "成功" : "失败", cls: ok ? "published" : "failed" };
    const msgCls = ok ? "msg ok" : "msg err";
    return `<tr>
      <td class="time">${shortTime(r.created_at)}</td>
      <td>${pill(pl.cls, pl.text)}</td>
      <td>${pill(st.cls, st.text)}</td>
      <td class="video-cell"><span class="video-title">${esc(r.video_title)}</span></td>
      <td class="msg-cell"><span class="${msgCls}">${esc(r.message)}</span></td>
    </tr>`;
  }).join("");
  renderPager("pager-logs", "logs", total);
}

function renderUpdated(d) {
  const t = d.generated_at || new Date().toISOString();
  $("last-update").textContent = `更新于 ${shortTime(t)}`;
}

/* ── refresh ────────────────────────────────────────────────── */
let DATA = null;

async function refresh() {
  const btn = $("btn-refresh");
  btn.classList.add("loading");
  try {
    DATA = await getData();
    renderStats(DATA);
    renderSources(DATA);
    renderSheets(DATA);
    renderLogs(DATA);
    renderPosts(DATA);
    renderUpdated(DATA);
    // 轮询中：今天的快照已生成 → 立即停止
    if (autoTimer && gotToday(DATA)) stopAuto();
    // 数据到位后立即评估是否需要启动/停止轮询
    // （避免页面打开时首个心跳在 DATA 加载前空转）
    heartbeat();
  } catch (e) {
    $("posts-body").innerHTML =
      `<tr><td colspan="7" class="empty">数据加载失败：${esc(e.message || e)}</td></tr>`;
  } finally {
    btn.classList.remove("loading");
  }
}

/* ── 智能自动刷新（结果驱动，不依赖 Actions 准点开始）───────── */
// 只判断"今天的数据快照生成了没"：Actions 当天只要跑完一次，
// data.json 的 generated_at 就是今天。前端在监测窗口内轮询，
// 一旦检测到今天的快照 → 刷新并停止。窗口可跨天（20:00 → 次日 00:00），
// 因此 Actions 延迟几小时也能自动捕获。配置来自 data.json 的 auto_refresh
// （config.yaml → web.auto_refresh）；拿不到配置时完全不自动刷新。
let autoTimer = null;

function parseAutoCfg(d) {
  const ar = (d && d.auto_refresh) || {};
  const fmt = (s) => (/^\d{1,2}:\d{2}$/.test(String(s || "").trim()) ? String(s).trim() : null);
  const ws = fmt(ar.watch_start);
  if (!ws) return null; // 未配置/非法 → 不自动刷新
  const toMin = (t) => { const [h, m] = t.split(":").map(Number); return h * 60 + m; };
  const start = toMin(ws);
  const dur = Math.max(1, Number(ar.watch_duration_hours) || 6) * 60; // 缺省 6 小时
  return {
    start,
    end: (start + dur) % 1440, // 归一化，跨天自然进位
    int: Math.max(10, ar.interval_seconds || 30),
  };
}

// 当前时间是否在监测窗口内（支持跨天：start > end 表示跨到次日）
function inWatch(cfg, now) {
  const cur = now.getHours() * 60 + now.getMinutes();
  if (cfg.start < cfg.end) return cur >= cfg.start && cur < cfg.end;
  return cur >= cfg.start || cur < cfg.end;
}

// 是否已生成"今天"的快照（Actions 只要当天跑完，generated_at 必为今天）
function gotToday(d) {
  const t = d && d.generated_at;
  if (!t) return false;
  const dt = new Date(t);
  if (Number.isNaN(dt.getTime())) return false;
  const now = new Date();
  return dt.getFullYear() === now.getFullYear()
    && dt.getMonth() === now.getMonth()
    && dt.getDate() === now.getDate();
}

function stopAuto() {
  if (autoTimer) { clearInterval(autoTimer); autoTimer = null; }
}

function startAuto(cfg) {
  if (autoTimer) return;
  autoTimer = setInterval(refresh, cfg.int * 1000);
}

// 60s 本地时钟心跳：进入监测窗口且今天未生成 → 启动轮询；
// 今天快照已生成或已出窗口 → 停止。心跳不产生任何网络请求。
function heartbeat() {
  const cfg = parseAutoCfg(DATA);
  if (!cfg) { stopAuto(); return; }
  const now = new Date();
  if (autoTimer) {
    if (gotToday(DATA) || !inWatch(cfg, now)) stopAuto();
  } else if (inWatch(cfg, now) && !gotToday(DATA)) {
    startAuto(cfg);
    refresh();
  }
}

/* ── init ───────────────────────────────────────────────────── */
renderFooter();
$("btn-refresh").addEventListener("click", refresh);
$("f-platform").addEventListener("change", () => { if (!DATA) return; pageState.posts = 1; renderPosts(DATA); });
$("f-status").addEventListener("change", () => { if (!DATA) return; pageState.posts = 1; renderPosts(DATA); });
// 分页按钮（事件委托）
document.addEventListener("click", (e) => {
  const btn = e.target.closest(".pg");
  if (!btn || btn.disabled || !DATA) return;
  const key = btn.dataset.key;
  const pg = Number(btn.dataset.pg);
  if (!key || !pg || pg < 1) return;
  pageState[key] = pg;
  if (key === "posts") renderPosts(DATA);
  else if (key === "logs") renderLogs(DATA);
});
setInterval(heartbeat, 60000); // 60s 本地时钟心跳（无网络请求）
heartbeat();                    // 页面打开即在窗口内则立即开始轮询
refresh();
