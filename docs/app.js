/* Arbitrage Scanner — static SPA over PostgREST.
 *
 * Read-only by design: the anon key can SELECT and nothing else (RLS), so this
 * file being public is fine. Every listener is guarded — one missing element
 * must not take the whole board down.
 */
const CFG = window.CFG || {};
const $ = (id) => document.getElementById(id);
const on = (id, ev, fn) => { const el = $(id); if (el) el.addEventListener(ev, fn); };

const state = {
  rows: [],
  sort: "net_gap",
  minGap: 500,
  minRoi: 15,
  conf: "high",
  q: "",
  loading: false,
};

const money = (c) => (c == null ? "—" : "$" + (c / 100).toLocaleString(undefined,
  { minimumFractionDigits: 2, maximumFractionDigits: 2 }));
const num = (v) => (v == null ? "—" : Number(v).toLocaleString());

function headers() {
  const h = { apikey: CFG.SUPABASE_ANON_KEY, "Content-Type": "application/json" };
  if (CFG.SUPABASE_ANON_KEY) h.Authorization = "Bearer " + CFG.SUPABASE_ANON_KEY;
  return h;
}

async function api(path) {
  const r = await fetch(`${CFG.SUPABASE_URL}/rest/v1/${path}`, { headers: headers() });
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
  return r.json();
}

function setStatus(text) { const s = $("status"); if (s) s.textContent = text; }

function showError(msg) {
  const e = $("error");
  if (!e) return;
  e.textContent = msg;
  e.hidden = !msg;
}

/* ---------------- query ---------------- */
function queryString() {
  const p = new URLSearchParams();
  p.set("select", "*");
  p.set("net_gap", `gte.${state.minGap}`);
  if (state.minRoi > 0) p.set("roi_pct", `gte.${state.minRoi}`);
  if (state.conf) p.set("evidence->>confidence", `eq.${state.conf}`);
  if (state.q.trim()) {
    const q = state.q.trim().replace(/[*(),]/g, "");
    p.set("or", `(ebay_title.ilike.*${q}*,amazon_title.ilike.*${q}*,asin.ilike.*${q}*)`);
  }
  p.set("order", `${state.sort}.desc`);
  p.set("limit", "500");
  return p.toString();
}

/* ---------------- render ---------------- */
function roiClass(v) { return v >= 40 ? "s1" : v >= 20 ? "s2" : "s3"; }

function rowHTML(r, i) {
  const conf = (r.evidence && r.evidence.confidence) || (r.method === "model" ? "high" : "low");
  const ebay = money(r.ebay_price), amz = money(r.amazon_price);
  const gap = r.net_gap == null ? 0 : r.net_gap;
  const roi = r.roi_pct == null ? 0 : Number(r.roi_pct);
  const pct = Math.max(0, Math.min(100, roi));
  return `<tr data-i="${i}">
    <td class="w-img"><img loading="lazy" src="${r.ebay_image || ""}" alt=""
        onerror="this.style.visibility='hidden'"></td>
    <td class="p-cell">
      <div class="p-title">${escapeHTML(r.ebay_title || "")}</div>
      <div class="p-meta">
        <span class="chip ${conf}">${conf}</span>
        <span>ASIN ${r.asin || "—"}</span>
        <span>·</span>
        <span>${escapeHTML((r.amazon_title || "").slice(0, 62))}</span>
      </div>
    </td>
    <td class="num">${amz}</td>
    <td class="num">${ebay}</td>
    <td class="num" style="color:var(--faint)">−${money(r.ebay_fee)}</td>
    <td class="num ${gap >= 0 ? "gap-pos" : "gap-neg"}">${gap >= 0 ? "+" : ""}${money(gap)}</td>
    <td class="num roi ${roiClass(roi)}">${roi.toFixed(0)}%
      <div class="bar"><i style="width:${pct}%"></i></div></td>
    <td class="num" style="color:var(--faint)">${Number(r.score || 0).toFixed(2)}</td>
  </tr>`;
}

function escapeHTML(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;",
    '"': "&quot;", "'": "&#39;" }[c]));
}

function render() {
  const tbody = $("rows");
  if (!tbody) return;
  tbody.innerHTML = state.rows.map(rowHTML).join("");
  const empty = $("empty");
  if (empty) empty.hidden = state.rows.length > 0;
  const count = $("count");
  if (count) {
    count.textContent = state.rows.length
      ? `showing ${state.rows.length} of the top matches`
      : "no rows match these filters";
  }
  tbody.querySelectorAll("tr").forEach((tr) => {
    tr.addEventListener("click", () => openDrawer(state.rows[Number(tr.dataset.i)]));
  });
}

function renderStats(s) {
  const el = $("stats");
  if (!el || !s) return;
  const box = (v, label, cls = "") =>
    `<div class="stat ${cls}"><b>${v}</b><span>${label}</span></div>`;
  el.innerHTML = [
    box(num(s.opps_5), "gaps ≥ $5", "good"),
    box(num(s.opps_25), "gaps ≥ $25"),
    box(num(s.matches), "matches"),
    box(num(s.listings), "listings"),
    box(num(s.keywords), "keywords"),
    box(`${num(s.pages_today)}/${num(s.page_budget)}`, "pages today"),
    box(s.last_run ? timeAgo(s.last_run) : "—", "last crawl"),
  ].join("");
}

function timeAgo(iso) {
  const d = (Date.now() - new Date(iso).getTime()) / 1000;
  if (d < 90) return "just now";
  if (d < 3600) return `${Math.round(d / 60)}m ago`;
  if (d < 86400) return `${Math.round(d / 3600)}h ago`;
  return `${Math.round(d / 86400)}d ago`;
}

/* ---------------- drawer ---------------- */
function openDrawer(r) {
  if (!r) return;
  const d = $("drawer"), c = $("drawerContent");
  if (!d || !c) return;
  const ev = r.evidence || {};
  const toks = (ev.shared_tokens || []).map((t) => `<span class="tok">${escapeHTML(t)}</span>`).join("");
  const models = (ev.matched_models || []).map((t) => `<span class="tok">${escapeHTML(t)}</span>`).join("");
  c.innerHTML = `
    <div class="d-title">${escapeHTML(r.ebay_title || "")}</div>
    <div class="d-grid">
      <div class="d-card"><div class="k">Amazon cost</div>
        <div class="v">${money(r.amazon_price)}</div>
        <div class="s">ASIN ${r.asin}</div></div>
      <div class="d-card"><div class="k">eBay resale</div>
        <div class="v">${money(r.ebay_price)}</div>
        <div class="s">item ${r.ebay_item_id}</div></div>
    </div>
    <div class="d-row"><span>Gross gap</span><span>${money(r.gross_gap)}</span></div>
    <div class="d-row"><span>eBay fees (13.25% + $0.30)</span><span>−${money(r.ebay_fee)}</span></div>
    <div class="d-row"><span><b>Net gap</b></span><span><b>${money(r.net_gap)}</b></span></div>
    <div class="d-row"><span>ROI on Amazon cost</span><span>${Number(r.roi_pct || 0).toFixed(1)}%</span></div>
    <div class="d-row"><span>Gap %</span><span>${Number(r.gap_pct || 0).toFixed(1)}%</span></div>
    <div class="d-row"><span>Match method / score</span><span>${r.method} · ${Number(r.score).toFixed(3)}</span></div>
    <div class="d-row"><span>Observed</span><span>${r.as_of ? new Date(r.as_of).toLocaleString() : "—"}</span></div>
    <div class="d-h">Why these were paired</div>
    <div class="evidence">${models || '<span class="tok">no model number</span>'}${toks}</div>
    <div class="d-h">Amazon listing</div>
    <div style="font-size:13px;line-height:1.45">${escapeHTML(r.amazon_title || "")}</div>
    <div class="links">
      <a href="${r.ebay_url}" target="_blank" rel="noopener">Open eBay listing ↗</a>
      <a href="${r.amazon_url}" target="_blank" rel="noopener">Open Amazon listing ↗</a>
    </div>`;
  d.hidden = false;
}

on("close", "click", () => { const d = $("drawer"); if (d) d.hidden = true; });
on("drawer", "click", (e) => { if (e.target.classList.contains("drawer-bg")) $("drawer").hidden = true; });
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") { const d = $("drawer"); if (d) d.hidden = true; }
});

/* ---------------- data ---------------- */
async function load() {
  if (state.loading) return;
  state.loading = true;
  setStatus("loading…");
  showError("");
  try {
    const [rows, stats] = await Promise.all([
      api(`ar_opportunities?${queryString()}`),
      api("ar_queue_stats?select=*").then((r) => r[0]).catch(() => null),
    ]);
    state.rows = rows || [];
    renderStats(stats);
    render();
    setStatus(`updated ${new Date().toLocaleTimeString()}`);
    if (stats && !state.rows.length && stats.listings === 0) {
      const hint = $("emptyHint");
      if (hint) hint.textContent = `${stats.keywords} keywords queued · ${stats.pages_today} pages crawled today`;
    }
  } catch (e) {
    showError("Could not load data: " + e.message);
    setStatus("error");
  } finally {
    state.loading = false;
  }
}

function syncHash() {
  const p = new URLSearchParams();
  if (state.q) p.set("q", state.q);
  p.set("gap", state.minGap);
  p.set("roi", state.minRoi);
  if (state.conf) p.set("conf", state.conf);
  p.set("sort", state.sort);
  history.replaceState(null, "", "#" + p.toString());
}

function readHash() {
  const p = new URLSearchParams(location.hash.slice(1));
  if (p.get("q")) state.q = p.get("q");
  if (p.get("gap")) state.minGap = Number(p.get("gap"));
  if (p.get("roi")) state.minRoi = Number(p.get("roi"));
  if (p.has("conf")) state.conf = p.get("conf");
  if (p.get("sort")) state.sort = p.get("sort");
}

function bind() {
  on("q", "input", (e) => { state.q = e.target.value; syncHash(); debounce(load)(); });
  on("minGap", "change", (e) => { state.minGap = Number(e.target.value); syncHash(); load(); });
  on("minRoi", "change", (e) => { state.minRoi = Number(e.target.value); syncHash(); load(); });
  on("conf", "change", (e) => { state.conf = e.target.value; syncHash(); load(); });
  on("sort", "change", (e) => { state.sort = e.target.value; syncHash(); load(); });
  on("refresh", "click", load);
  on("export", "click", exportCSV);
}

let t;
function debounce(fn) { return () => { clearTimeout(t); t = setTimeout(fn, 320); }; }

function exportCSV() {
  if (!state.rows.length) return;
  const cols = ["ebay_item_id", "ebay_title", "ebay_price", "asin", "amazon_title",
    "amazon_price", "gross_gap", "ebay_fee", "net_gap", "gap_pct", "roi_pct", "score", "as_of"];
  const head = cols.join(",");
  const body = state.rows.map((r) => cols.map((c) => {
    let v = r[c];
    if (["ebay_price", "amazon_price", "gross_gap", "ebay_fee", "net_gap"].includes(c) && v != null)
      v = (v / 100).toFixed(2);
    return `"${String(v == null ? "" : v).replace(/"/g, '""')}"`;
  }).join(",")).join("\n");
  const blob = new Blob([head + "\n" + body], { type: "text/csv" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `arbitrage-${new Date().toISOString().slice(0, 10)}.csv`;
  a.click();
  URL.revokeObjectURL(a.href);
}

function boot() {
  try {
    readHash();
    const set = (id, v) => { const el = $(id); if (el) el.value = v; };
    set("q", state.q); set("minGap", String(state.minGap));
    set("minRoi", String(state.minRoi)); set("conf", state.conf); set("sort", state.sort);
    bind();
    if (!CFG.SUPABASE_URL || !CFG.SUPABASE_ANON_KEY) {
      showError("config.js is missing SUPABASE_URL / SUPABASE_ANON_KEY");
      return;
    }
    load();
    setInterval(load, 60000);
  } catch (e) {
    showError("page failed to start: " + e.message);
  }
}

window.addEventListener("error", (e) => showError("script error: " + (e.message || "unknown")));
document.addEventListener("DOMContentLoaded", boot);
