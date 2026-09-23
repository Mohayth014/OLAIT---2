/* ============================================================
   TRACE AI — Mobile Inspector client
   Talks to the same REST API as the desktop app.
   ============================================================ */
"use strict";

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];

const state = {
    mode: "single",
    dualFront: null,
    dualBack: null,
    catalog: [],
    current: null,
    resultOpen: false,
    evPanel: "front",
    evFilter: "all",
    evBoxes: [],
    evImg: null,
};

const FIELD_ORDER = [
    ["product_name", "Generic product name", "fa-tag"],
    ["brand", "Brand", "fa-certificate"],
    ["net_quantity", "Net quantity", "fa-weight-scale"],
    ["mrp", "Maximum retail price", "fa-indian-rupee-sign"],
    ["unit_sale_price", "Unit sale price · Rule 6(10)", "fa-calculator"],
    ["mfg_date", "Mfg / packing date", "fa-calendar-days"],
    ["best_before", "Best before / expiry", "fa-clock-rotate-left"],
    ["manufacturer", "Manufacturer & address", "fa-industry"],
    ["consumer_care", "Consumer care", "fa-headset"],
    ["fssai_license", "FSSAI licence", "fa-shield-halved"],
    ["country_of_origin", "Country of origin", "fa-earth-asia"],
];

const BOX_COLORS = {
    mrp: "#38bdf8", net_quantity: "#10b981", manufacturer: "#ec4899",
    date: "#f59e0b", consumer_care: "#8b5cf6", fssai: "#f472b6",
    country_of_origin: "#22d3ee", general: "#64748b",
};

/* ================= Auth / Session (two-portal RBAC) =================
   Shares the "traceSession" localStorage key with the desktop app (both
   are served from the same origin). Hiding nav/actions per role below is
   UX only -- the real boundary is enforced server-side, see backend/auth.py. */
const AUTH_STORAGE_KEY = "traceSession";
let currentUser = null; // { access_token, user_id, name, role }

function loadSession() {
    try { const raw = localStorage.getItem(AUTH_STORAGE_KEY); return raw ? JSON.parse(raw) : null; }
    catch (e) { return null; }
}
function saveSession(s) { try { localStorage.setItem(AUTH_STORAGE_KEY, JSON.stringify(s)); } catch (e) { } }
function clearSession() { try { localStorage.removeItem(AUTH_STORAGE_KEY); } catch (e) { } }

function showLoginScreen(message) {
    $("#m-login").hidden = false;
    const err = $("#m-auth-error");
    if (message) { err.textContent = message; err.hidden = false; } else { err.hidden = true; }
}
function hideLoginScreen() { $("#m-login").hidden = true; }

async function submitLogin() {
    const email = ($("#m-auth-email").value || "").trim();
    const password = $("#m-auth-password").value || "";
    const err = $("#m-auth-error");
    if (!email || !password) { err.textContent = "Enter both email and password."; err.hidden = false; return; }
    try {
        const r = await fetch("/api/auth/login", {
            method: "POST", headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ email, password }),
        });
        const body = await r.json().catch(() => ({}));
        if (!r.ok) throw new Error(body.detail || "Invalid email or password.");
        currentUser = body;
        saveSession(currentUser);
        hideLoginScreen();
        startMobileApp();
    } catch (e) {
        err.textContent = e.message;
        err.hidden = false;
    }
}

function logout() {
    clearSession();
    currentUser = null;
    location.hash = "";
    location.reload();
}

function applyRoleUI(role) {
    const isInspector = role === "inspector";
    const scanBtn = $('.mnav-btn[data-view="scan"]');
    const statsBtn = $('.mnav-btn[data-view="stats"]');
    if (scanBtn) scanBtn.hidden = !isInspector;      // scanning is inspector-only
    if (statsBtn) statsBtn.hidden = isInspector;      // stats = manager oversight dashboard
    const inspFilter = $("#m-inspector-filter");
    if (inspFilter) inspFilter.hidden = isInspector;
    const badge = $("#m-user-badge");
    if (badge && currentUser) { badge.hidden = false; badge.textContent = currentUser.name; }
    const logoutBtn = $("#m-logout-btn");
    if (logoutBtn) logoutBtn.hidden = false;
}

function startMobileApp() {
    applyRoleUI(currentUser.role);
    loadCatalog();
    loadRecent();
    switchView(currentUser.role === "manager" ? "stats" : "scan");
}

/* ---------------- helpers ---------------- */
async function api(path, opts) {
    const headers = Object.assign({}, (opts && opts.headers) || {});
    if (currentUser && currentUser.access_token) headers["Authorization"] = `Bearer ${currentUser.access_token}`;
    const r = await fetch(path, Object.assign({}, opts, { headers }));
    if (r.status === 401) {
        clearSession();
        currentUser = null;
        showLoginScreen("Your session expired. Please sign in again.");
        throw new Error("Session expired.");
    }
    if (!r.ok) {
        let msg = `Request failed (HTTP ${r.status})`;
        try { const j = await r.json(); if (j.detail) msg = j.detail; } catch (_) { }
        throw new Error(msg);
    }
    return r.json();
}
const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const pct = (v) => `${Math.round((Number(v) || 0) * 100)}%`;

function toast(msg) {
    $$(".mtoast").forEach((t) => t.remove());
    const el = document.createElement("div");
    el.className = "mtoast";
    el.textContent = msg;
    document.body.appendChild(el);
    setTimeout(() => el.remove(), 2800);
}

function statusClass(s) {
    return s === "COMPLIANT" ? "pass" : s === "NON_COMPLIANT" ? "fail" : "review";
}
function statusWord(s) {
    return s === "COMPLIANT" ? "COMPLIANT" : s === "NON_COMPLIANT" ? "NON-COMPLIANT" : "REVIEW REQUIRED";
}
// Human-readable labels for backend/pipeline/violation_diagnostics.py's root_cause codes.
const ROOT_CAUSE_LABELS = {
    GENUINELY_ABSENT: "Declaration Genuinely Absent",
    PRINT_QUALITY_DEGRADED: "Print Quality Degraded",
    WRONG_PANEL_LIKELY: "Wrong Panel Likely Scanned",
    NON_STANDARD_FORMAT: "Non-Standard Format",
    UNDERSIZED_TEXT: "Undersized Text",
};
function rootCauseLabel(rootCause) {
    return ROOT_CAUSE_LABELS[rootCause] || rootCause || "";
}
function relTime(ts) {
    if (!ts) return "";
    const d = new Date(String(ts).replace(" ", "T"));
    if (isNaN(d)) return String(ts);
    let s = Math.floor((Date.now() - d) / 1000);
    if (s < 60) return "just now";
    const m = Math.floor(s / 60); if (m < 60) return `${m}m ago`;
    const h = Math.floor(m / 60); if (h < 24) return `${h}h ago`;
    return `${Math.floor(h / 24)}d ago`;
}

/* ---------------- bottom nav ---------------- */
function switchView(name) {
    if (currentUser) {
        if (name === "scan" && currentUser.role !== "inspector") name = "stats";   // managers can't scan
        if (name === "stats" && currentUser.role !== "manager") name = "scan";     // inspectors have no dashboard
    }
    $$(".mview").forEach((v) => v.classList.toggle("active", v.id === `view-${name}`));
    $$(".mnav-btn").forEach((b) => b.classList.toggle("active", b.dataset.view === name));
    window.scrollTo({ top: 0 });
    if (name === "activity") loadActivity();
    if (name === "stats") loadStats();
}

/* ---------------- scan ---------------- */
function setMode(mode) {
    state.mode = mode;
    $$("#m-mode-seg .mseg-btn").forEach((b) => b.classList.toggle("active", b.dataset.mode === mode));
    $("#m-cap-single").hidden = mode !== "single";
    $("#m-cap-dual").hidden = mode !== "dual";
}

const PROC_STEPS = ["Preprocess", "CLIP vision", "Vector match", "Dual-OCR", "Rule audit"];
let procTimer = null;
function showProcessing(on) {
    const ov = $("#m-proc");
    if (!on) { ov.hidden = true; clearInterval(procTimer); return; }
    ov.hidden = false;
    const box = $("#m-proc-steps");
    box.innerHTML = PROC_STEPS.map((s) => `<span class="mproc-step">${s}</span>`).join("");
    $("#m-proc-title").textContent = "Preprocessing image…";
    let i = 0;
    const tick = () => {
        const chips = $$(".mproc-step", box);
        chips.forEach((c, idx) => c.classList.toggle("on", idx <= i));
        $("#m-proc-title").textContent = [
            "Preprocessing image…", "CLIP visual understanding…", "Vector similarity search…",
            "Dual-OCR text extraction…", "Legal Metrology rule audit…",
        ][Math.min(i, 4)];
        i++;
        if (i >= PROC_STEPS.length + 2) i = PROC_STEPS.length - 1;
    };
    tick();
    procTimer = setInterval(tick, 900);
}

async function runScan(formData) {
    if (!currentUser || currentUser.role !== "inspector") {
        toast("Only inspectors can scan packages.");
        return;
    }
    showProcessing(true);
    try {
        const data = await api("/api/scan", { method: "POST", body: formData });
        state.current = data;
        openResult(data);
        loadRecent();
    } catch (e) {
        toast(e.message || "Scan failed");
    } finally {
        showProcessing(false);
        state.dualFront = state.dualBack = null;
        syncDualTiles();
    }
}

function scanSingle(file) {
    if (!file) return;
    const fd = new FormData();
    fd.append("file", file, file.name || "capture.jpg");
    runScan(fd);
}
function scanDataset(filename) {
    const fd = new FormData();
    fd.append("dataset_filename", filename);
    runScan(fd);
}
function scanDual() {
    if (!state.dualFront && !state.dualBack) { toast("Add at least the front panel"); return; }
    const fd = new FormData();
    if (state.dualFront) fd.append("file", state.dualFront, "front.jpg");
    if (state.dualBack) fd.append("back_file", state.dualBack, "back.jpg");
    runScan(fd);
}
function syncDualTiles() {
    const f = $("#m-tile-front"), b = $("#m-tile-back");
    f.classList.toggle("filled", !!state.dualFront);
    b.classList.toggle("filled", !!state.dualBack);
    $("#m-tile-front-label").textContent = state.dualFront ? (state.dualFront.name || "Front ready") : "Add front panel";
    $("#m-tile-back-label").textContent = state.dualBack ? (state.dualBack.name || "Back ready") : "Add back panel";
    $("#m-run-dual").disabled = !state.dualFront && !state.dualBack;
}

/* ---------------- catalog + recent ---------------- */
async function loadCatalog() {
    try {
        const items = await api("/api/catalog");
        state.catalog = items;
        $("#m-chips").innerHTML = items.slice(0, 24).map((it) => `
            <button class="mchip" data-file="${esc(it.filename)}">
                <img src="${esc(it.thumbnail_url)}" alt="">
                <div class="mchip-name">${esc(it.name)}</div>
                <div class="mchip-cat">${esc(it.category)}</div>
            </button>`).join("") || `<div class="mrecent-empty">No indexed samples.</div>`;
        $$("#m-chips .mchip").forEach((c) => c.addEventListener("click", () => scanDataset(c.dataset.file)));
    } catch (e) {
        $("#m-chips").innerHTML = `<div class="mrecent-empty">Catalog unavailable.</div>`;
    }
}

function rowHTML(h) {
    const cls = statusClass(h.overall_status);
    const word = h.overall_status === "COMPLIANT" ? "PASS" : h.overall_status === "NON_COMPLIANT" ? "FAIL" : "REVIEW";
    const name = (h.extracted_data && h.extracted_data.product_name && h.extracted_data.product_name.value) || h.image_filename;
    return `<button class="mrow" data-id="${esc(h.inspection_id)}">
        <img src="${esc(h.thumbnail_url)}" alt="">
        <div class="mrow-mid">
            <div class="mrow-name">${esc(name)}</div>
            <div class="mrow-sub">${esc(h.inspection_id)} · ${esc(relTime(h.timestamp))}</div>
        </div>
        <span class="mpill ${cls}">${word}</span>
    </button>`;
}
async function loadRecent() {
    try {
        const rows = await api("/api/inspections?limit=6");
        $("#m-recent").innerHTML = rows.length
            ? rows.map(rowHTML).join("")
            : `<div class="mrecent-empty">No inspections yet — scan a package above.</div>`;
        bindRows("#m-recent");
    } catch (_) { }
}
function bindRows(sel) {
    $$(`${sel} .mrow`).forEach((r) => r.addEventListener("click", () => openInspectionById(r.dataset.id)));
}

/* ---------------- activity ---------------- */
let searchTimer = null;
async function loadActivity() {
    const q = $("#m-search").value.trim();
    const st = $("#m-filter .mf-chip.active").dataset.st;
    const inspSel = $("#m-inspector-filter");
    const inspectorId = (inspSel && !inspSel.hidden) ? inspSel.value : "";

    if (currentUser && currentUser.role === "manager") await loadInspectorFilterOptions();

    const params = new URLSearchParams({ limit: "40" });
    if (q) params.set("search", q);
    if (st) params.set("status", st);
    if (inspectorId) params.set("inspector_id", inspectorId);
    $("#m-list").innerHTML = `<div class="mlist-skel"></div><div class="mlist-skel"></div><div class="mlist-skel"></div>`;
    try {
        const rows = await api(`/api/inspections?${params}`);
        $("#m-list").innerHTML = rows.length
            ? rows.map(rowHTML).join("")
            : `<div class="mlist-empty">No inspections match.</div>`;
        bindRows("#m-list");
    } catch (e) {
        $("#m-list").innerHTML = `<div class="mlist-empty">Could not load activity.</div>`;
    }
}

// Populates the manager-only inspector dropdown from the full (unfiltered) list.
let inspectorOptionsLoaded = false;
async function loadInspectorFilterOptions() {
    if (inspectorOptionsLoaded) return;
    const sel = $("#m-inspector-filter");
    if (!sel) return;
    try {
        const rows = await api("/api/inspections?limit=1000");
        const ids = [...new Set(rows.map((r) => r.inspector_id).filter(Boolean))].sort();
        sel.innerHTML = `<option value="">All inspectors</option>` +
            ids.map((id) => `<option value="${esc(id)}">${esc(id)}</option>`).join("");
        inspectorOptionsLoaded = true;
    } catch (e) { /* leave default */ }
}

/* ---------------- stats ---------------- */
function barRows(arr, fmt) {
    const max = Math.max(1, ...arr.map((a) => a.v));
    return arr.map((a) => `
        <div class="mbar-row">
            <span class="mbar-lab">${esc(a.k)}</span>
            <span class="mbar-val">${fmt ? fmt(a.v) : a.v}</span>
            <span class="mbar-track"><span class="mbar-fill" style="width:${Math.round(a.v / max * 100)}%"></span></span>
        </div>`).join("");
}
async function loadStats() {
    try {
        const m = await api("/api/dashboard/metrics");
        $("#m-stat-grid").innerHTML = `
            <div class="mstat"><div class="mstat-k">Compliance rate</div><div class="mstat-v">${m.compliance_rate ?? "--"}<small>%</small></div></div>
            <div class="mstat"><div class="mstat-k">Avg AI confidence</div><div class="mstat-v">${m.avg_confidence ?? "--"}<small>%</small></div></div>
            <div class="mstat"><div class="mstat-k">Total inspections</div><div class="mstat-v">${m.total_inspections ?? "--"}</div></div>
            <div class="mstat"><div class="mstat-k">Pending review</div><div class="mstat-v">${m.review_required_count ?? "--"}</div></div>
            <div class="mstat full"><div class="mstat-k">Verdicts</div><div class="mstat-v" style="font-size:16px">
                <span style="color:var(--pass)">${m.compliant_count || 0}</span> ·
                <span style="color:var(--review)">${m.review_required_count || 0}</span> ·
                <span style="color:var(--fail)">${m.non_compliant_count || 0}</span>
                <small>compliant / review / failed</small></div></div>`;
        const cats = Object.entries(m.category_distribution || {}).map(([k, v]) => ({ k, v })).sort((a, b) => b.v - a.v).slice(0, 6);
        $("#m-cat").innerHTML = cats.length ? barRows(cats) : `<div class="mrecent-empty">No data.</div>`;
        $("#m-fields").innerHTML = (m.field_detection || []).map((f) => ({ k: f.label, v: f.pct })).length
            ? barRows((m.field_detection || []).map((f) => ({ k: f.label, v: f.pct })), (v) => v + "%")
            : `<div class="mrecent-empty">No data.</div>`;
        const vt = (m.violation_types || []).slice(0, 6).map((v) => ({ k: v.label, v: v.count }));
        $("#m-viol").innerHTML = vt.length ? barRows(vt) : `<div class="mrecent-empty">No violations flagged.</div>`;
    } catch (e) {
        $("#m-stat-grid").innerHTML = `<div class="mstat full"><div class="mstat-k">Stats unavailable</div></div>`;
    }
}

/* ---------------- result sheet ---------------- */
async function openInspectionById(id) {
    showProcessing(true);
    try {
        const d = await api(`/api/inspections/${id}`);
        state.current = d;
        openResult(d);
    } catch (e) {
        toast(e.message || "Could not open inspection");
    } finally {
        showProcessing(false);
    }
}

function openResult(d) {
    renderResult(d);
    const sheet = $("#m-result");
    sheet.hidden = false;
    if (!state.resultOpen) {
        state.resultOpen = true;
        history.pushState({ mResult: 1 }, "");
    }
}
function closeResult() {
    $("#m-result").hidden = true;
    state.resultOpen = false;
}

function ringSVG(p) {
    const r = 24, c = 2 * Math.PI * r, off = c * (1 - (p || 0));
    return `<svg width="58" height="58" viewBox="0 0 58 58">
        <circle cx="29" cy="29" r="${r}" fill="none" stroke="#2A2A42" stroke-width="6"/>
        <circle cx="29" cy="29" r="${r}" fill="none" stroke="var(--gold)" stroke-width="6" stroke-linecap="round"
            stroke-dasharray="${c.toFixed(1)}" stroke-dashoffset="${off.toFixed(1)}"/>
    </svg><span>${pct(p)}</span>`;
}

function renderResult(d) {
    const id = d.inspection_id;
    $("#m-result-title").textContent = id;
    const sc = statusClass(d.overall_status);
    const ext = d.extracted_data || {};
    // These open via plain <a href> navigation (no custom header possible), so the
    // token rides as a query param -- backend's get_current_user_flexible accepts it.
    const tok = currentUser && currentUser.access_token ? encodeURIComponent(currentUser.access_token) : "";
    const rep = (fmt) => fmt === "html" ? `/api/reports/${id}/html?token=${tok}`
        : fmt === "pdf" ? `/api/reports/${id}/pdf?token=${tok}`
            : fmt === "docx" ? `/api/reports/${id}/docx?token=${tok}`
                : `/api/reports/${id}/export?format=${fmt}&token=${tok}`;

    const cat = (d.clip_categories && d.clip_categories[0]) || null;

    const declHTML = FIELD_ORDER.map(([key, label, ico]) => {
        const f = ext[key] || {};
        const has = !!f.value;
        const stat = !has ? '<i class="fa-solid fa-circle-xmark r-dstat bad"></i>'
            : f.is_valid ? '<i class="fa-solid fa-circle-check r-dstat ok"></i>'
                : '<i class="fa-solid fa-circle-exclamation r-dstat warn"></i>';
        const detail = f.detected_text ? `<div class="r-ddetail" hidden>${esc(f.detected_text)}</div>` : "";
        return `<div class="r-drow">
            <div class="r-drow-head" data-toggle="${detail ? 1 : 0}">
                <span class="r-drow-ico"><i class="fa-solid ${ico}"></i></span>
                <span class="r-drow-main">
                    <span class="r-dk">${esc(label)}</span>
                    <span class="r-dv ${has ? "" : "miss"}">${has ? esc(f.value) : "Not detected"}</span>
                </span>
                ${stat}
            </div>${detail}</div>`;
    }).join("");

    const violHTML = (d.violations && d.violations.length)
        ? `<div class="r-sec"><div class="r-sec-h">Statutory violations <span class="r-count">${d.violations.length}</span></div>
           <ul class="r-viol">${d.violations.map((v) => `<li>${esc(v)}</li>`).join("")}</ul></div>` : "";

    const rulesHTML = (d.rule_results || []).map((r) => {
        const b = statusClass(r.status === "PASS" ? "COMPLIANT" : r.status === "FAIL" ? "NON_COMPLIANT" : "REVIEW_REQUIRED");
        const diag = (r.root_cause && r.rectification)
            ? `<div class="r-rule-diag"><div class="r-rule-diag-k">Root Cause: ${esc(rootCauseLabel(r.root_cause))}</div><div class="r-rule-diag-v"><b>Fix:</b> ${esc(r.rectification)}</div></div>`
            : "";
        return `<div class="r-rule">
            <div class="r-rule-top">
                <div><div class="r-rule-t">${esc(r.title)}</div><div class="r-rule-ref">${esc(r.legal_reference)}</div></div>
                <span class="r-badge ${b}">${esc(r.status)}</span>
            </div>
            <div class="r-rule-msg">${esc(r.message)}</div>${diag}
        </div>`;
    }).join("");

    const rd = d.readability || {};
    const readHTML = `<div class="r-metrics">
        <div class="r-metric"><div class="r-metric-k">Sharpness</div><div class="r-metric-v">${rd.blur_score ?? "--"}</div></div>
        <div class="r-metric"><div class="r-metric-k">RMS contrast</div><div class="r-metric-v">${rd.contrast_score ?? "--"}</div></div>
        <div class="r-metric"><div class="r-metric-k">Avg box height</div><div class="r-metric-v">${rd.avg_font_height_px != null ? rd.avg_font_height_px + "px" : "--"}</div></div>
        <div class="r-metric"><div class="r-metric-k">Text coverage</div><div class="r-metric-v">${rd.estimated_text_coverage != null ? (rd.estimated_text_coverage * 100).toFixed(1) + "%" : "--"}</div></div>
    </div>${(rd.warnings && rd.warnings.length) ? `<div class="r-warn">${rd.warnings.map(esc).join(" &nbsp;|&nbsp; ")}</div>` : ""}`;

    const sims = d.similar_products || [];
    const simHTML = sims.length ? `<div class="r-sec"><div class="r-sec-h">Similar packaging</div>
        <div class="r-sim">${sims.map((p) => `<div class="r-sim-item">
            <img src="${esc(p.thumbnail_url)}" alt="">
            <div class="r-sim-name">${esc(p.name)}</div>
            <div class="r-sim-pct">${p.similarity_percentage != null ? p.similarity_percentage + "%" : Math.round((p.similarity_score || 0) * 100) + "%"}</div>
        </div>`).join("")}</div></div>` : "";

    const dual = d.is_dual_panel && d.back_image_url;
    const evTags = ["all", "mrp", "net_quantity", "manufacturer", "date", "consumer_care"];

    // Two distinct actions: the inspector's own routine post-scan call (only ever
    // reachable on their own scan -- the server 403s a GET on anyone else's), vs a
    // manager overriding an already-recorded verdict (mandatory written reason,
    // logged as its own audit event). Never the same code path.
    let actionHTML = "";
    if (currentUser && currentUser.role === "inspector") {
        actionHTML = `<div class="r-sec">
            <div class="r-sec-h">Your decision</div>
            <div class="r-decision-row">
                <button class="r-dec-btn approve" onclick="recordDecision('APPROVED')"><i class="fa-solid fa-check"></i>Approve</button>
                <button class="r-dec-btn rescan" onclick="recordDecision('REJECTED_NON_COMPLIANT')"><i class="fa-solid fa-rotate-left"></i>Rescan</button>
                <button class="r-dec-btn hold" onclick="recordDecision('PENDING')"><i class="fa-solid fa-pause"></i>Hold</button>
            </div>
        </div>`;
    } else if (currentUser && currentUser.role === "manager") {
        const last = (d.override_history && d.override_history.length) ? d.override_history[d.override_history.length - 1] : null;
        actionHTML = `<div class="r-sec">
            <div class="r-sec-h">Override verdict</div>
            ${last ? `<div class="r-warn" style="margin-bottom:10px;">Last overridden by ${esc(last.manager_name || last.manager_id)}: "${esc(last.reason)}"</div>` : ""}
            <div class="r-override-box">
                <select id="r-override-status">
                    <option value="COMPLIANT" ${d.overall_status === "COMPLIANT" ? "selected" : ""}>COMPLIANT</option>
                    <option value="NON_COMPLIANT" ${d.overall_status === "NON_COMPLIANT" ? "selected" : ""}>NON-COMPLIANT</option>
                    <option value="REVIEW_REQUIRED" ${d.overall_status === "REVIEW_REQUIRED" ? "selected" : ""}>REVIEW REQUIRED</option>
                </select>
                <textarea id="r-override-reason" placeholder="Written reason (required)"></textarea>
                <button class="mbig-btn gold" onclick="submitOverrideMobile()"><i class="fa-solid fa-gavel"></i><span>Submit override</span></button>
            </div>
        </div>`;
    }

    $("#m-result-body").innerHTML = `
        ${cat ? `<div class="r-cat-tag"><i class="fa-solid fa-eye"></i> ${esc(cat.label)} · ${cat.percentage}%</div>` : ""}
        <div class="r-status ${sc}">
            <div class="r-status-top">
                <div class="r-verdict">${statusWord(d.overall_status)}</div>
                <div class="r-ring">${ringSVG(d.overall_confidence)}</div>
            </div>
            <div class="r-summary">${esc(d.summary)}</div>
            <div class="r-meta">${esc(d.image_filename)} · ${esc(d.timestamp)}${dual ? " · dual-panel" : ""}</div>
        </div>

        <div class="r-actions">
            <a class="r-act" href="${rep("html")}" target="_blank" rel="noopener"><i class="fa-solid fa-file-lines"></i>Cert</a>
            <a class="r-act" href="${rep("pdf")}" target="_blank" rel="noopener"><i class="fa-solid fa-file-pdf"></i>PDF</a>
            <a class="r-act" href="${rep("docx")}" target="_blank" rel="noopener"><i class="fa-solid fa-file-word"></i>Word</a>
            <a class="r-act" href="${rep("json")}" target="_blank" rel="noopener"><i class="fa-solid fa-file-code"></i>JSON</a>
            <a class="r-act" href="${rep("csv")}" target="_blank" rel="noopener"><i class="fa-solid fa-file-csv"></i>CSV</a>
        </div>

        ${actionHTML}

        ${violHTML}

        <div class="r-sec">
            <div class="r-sec-h">Mandatory declarations</div>
            <div class="r-decl">${declHTML}</div>
        </div>

        <div class="r-sec">
            <div class="r-sec-h">Visual evidence <span class="r-count">${(d.ocr_boxes || []).length} regions</span></div>
            ${dual ? `<div class="r-ev-panels">
                <button data-panel="front" class="active">Front</button>
                <button data-panel="back">Back</button></div>` : ""}
            <div class="r-ev-tabs">${evTags.map((t) => `<button class="r-ev-tab ${t === "all" ? "active" : ""}" data-tag="${t}">${t === "all" ? "All" : t.replace("_", " ")}</button>`).join("")}</div>
            <div class="r-canvas-wrap"><canvas id="r-canvas"></canvas><div class="r-canvas-tip" id="r-canvas-tip"></div></div>
        </div>

        <div class="r-sec"><div class="r-sec-h">Legibility &middot; Rule 7</div>${readHTML}</div>

        ${simHTML}

        <div class="r-sec"><div class="r-sec-h">Statutory rule audit</div>${rulesHTML}</div>
    `;

    // accordion
    $$("#m-result-body .r-drow-head").forEach((h) => {
        if (h.dataset.toggle !== "1") return;
        h.addEventListener("click", () => {
            const det = h.nextElementSibling;
            if (det && det.classList.contains("r-ddetail")) det.hidden = !det.hidden;
        });
    });

    // evidence canvas
    state.evBoxes = d.ocr_boxes || [];
    state.evFilter = "all";
    state.evPanel = "front";
    const frontUrl = d.image_url || d.thumbnail_url;
    setupEvidence(frontUrl, dual ? d.back_image_url : null);

    $$("#m-result-body .r-ev-tab").forEach((b) => b.addEventListener("click", () => {
        $$("#m-result-body .r-ev-tab").forEach((x) => x.classList.toggle("active", x === b));
        state.evFilter = b.dataset.tag;
        drawEvidence();
    }));
    $$("#m-result-body .r-ev-panels button").forEach((b) => b.addEventListener("click", () => {
        $$("#m-result-body .r-ev-panels button").forEach((x) => x.classList.toggle("active", x === b));
        state.evPanel = b.dataset.panel;
        setupEvidence(state.evPanel === "back" ? d.back_image_url : frontUrl, null);
    }));
}

// Inspector's own post-scan decision (approve / rescan / hold).
async function recordDecision(decision) {
    if (!state.current) return;
    try {
        const body = await api(`/api/inspections/${state.current.inspection_id}/review`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ decision }),
        });
        state.current = body.inspection;
        renderResult(state.current);
        toast("Decision recorded.");
        loadRecent();
    } catch (e) {
        toast(e.message || "Could not record decision.");
    }
}

// Manager overriding an already-recorded verdict -- distinct from the decision above.
async function submitOverrideMobile() {
    if (!state.current) return;
    const newStatus = $("#r-override-status").value;
    const reason = $("#r-override-reason").value.trim();
    if (!reason) { toast("A written reason is required."); return; }
    try {
        const body = await api(`/api/inspections/${state.current.inspection_id}/override`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ new_status: newStatus, reason }),
        });
        state.current = body.inspection;
        renderResult(state.current);
        toast("Verdict overridden.");
    } catch (e) {
        toast(e.message || "Override failed.");
    }
}

function setupEvidence(url, _back) {
    const canvas = $("#r-canvas");
    if (!canvas || !url) return;
    const img = new Image();
    img.onload = () => {
        const cap = 1100;
        const scale = Math.min(1, cap / img.naturalWidth);
        canvas.width = Math.round(img.naturalWidth * scale);
        canvas.height = Math.round(img.naturalHeight * scale);
        state.evImg = img;
        drawEvidence();
    };
    img.onerror = () => { state.evImg = null; };
    img.src = url;
}

function boxesForPanel() {
    const dual = (state.evBoxes || []).some((x) => x.panel === "back");
    return (state.evBoxes || []).filter((b) => {
        if (dual && (b.panel || "front") !== state.evPanel) return false;
        if (state.evFilter === "all") return true;
        return (b.field_tag || "general") === state.evFilter;
    });
}

function drawEvidence(hl) {
    const canvas = $("#r-canvas");
    if (!canvas || !state.evImg) return;
    const ctx = canvas.getContext("2d");
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    ctx.drawImage(state.evImg, 0, 0, canvas.width, canvas.height);
    boxesForPanel().forEach((b) => {
        const nb = b.normalized_bbox;
        if (!nb) return;
        const x = nb.x_min * canvas.width, y = nb.y_min * canvas.height;
        const w = (nb.x_max - nb.x_min) * canvas.width, h = (nb.y_max - nb.y_min) * canvas.height;
        const col = BOX_COLORS[b.field_tag || "general"] || BOX_COLORS.general;
        ctx.lineWidth = b === hl ? 4 : 2;
        ctx.strokeStyle = col;
        ctx.fillStyle = col + (b === hl ? "40" : "18"); // #RRGGBBAA
        ctx.strokeRect(x, y, w, h);
        ctx.fillRect(x, y, w, h);
    });
}

/* tap a box on the canvas -> show its text */
function canvasTap(e) {
    const canvas = $("#r-canvas");
    if (!canvas || !state.evImg) return;
    const rect = canvas.getBoundingClientRect();
    const p = e.touches ? e.touches[0] : e;
    const nx = (p.clientX - rect.left) / rect.width;
    const ny = (p.clientY - rect.top) / rect.height;
    let hit = null;
    for (const b of boxesForPanel()) {
        const nb = b.normalized_bbox;
        if (!nb) continue;
        if (nx >= nb.x_min && nx <= nb.x_max && ny >= nb.y_min && ny <= nb.y_max) { hit = b; break; }
    }
    const tip = $("#r-canvas-tip");
    if (hit) {
        tip.innerHTML = `<b style="color:${BOX_COLORS[hit.field_tag || "general"]}">[${(hit.field_tag || "general").toUpperCase()}]</b> ${Math.round((hit.confidence || 0) * 100)}%<br>${esc(hit.text)}`;
        tip.classList.add("on");
        drawEvidence(hit);
    } else {
        tip.classList.remove("on");
        drawEvidence();
    }
}

/* ---------------- init ---------------- */
function init() {
    // nav
    $$(".mnav-btn").forEach((b) => b.addEventListener("click", () => switchView(b.dataset.view)));
    $(".mbrand").addEventListener("click", () => switchView("scan"));

    // mode segmented
    $$("#m-mode-seg .mseg-btn").forEach((b) => b.addEventListener("click", () => setMode(b.dataset.mode)));

    // single capture inputs
    $("#m-file-cam").addEventListener("change", (e) => scanSingle(e.target.files[0]));
    $("#m-file-lib").addEventListener("change", (e) => scanSingle(e.target.files[0]));

    // dual capture inputs
    $$('#m-cap-dual input[type="file"]').forEach((inp) => {
        inp.addEventListener("change", (e) => {
            const f = e.target.files[0];
            if (!f) return;
            if (inp.dataset.dual === "front") state.dualFront = f; else state.dualBack = f;
            syncDualTiles();
        });
    });
    $("#m-run-dual").addEventListener("click", scanDual);

    // activity search / filter
    $("#m-search").addEventListener("input", () => {
        clearTimeout(searchTimer);
        searchTimer = setTimeout(loadActivity, 250);
    });
    $$("#m-filter .mf-chip").forEach((c) => c.addEventListener("click", () => {
        $$("#m-filter .mf-chip").forEach((x) => x.classList.toggle("active", x === c));
        loadActivity();
    }));
    const inspFilter = $("#m-inspector-filter");
    if (inspFilter) inspFilter.addEventListener("change", loadActivity);

    // login
    const pw = $("#m-auth-password");
    if (pw) pw.addEventListener("keydown", (e) => { if (e.key === "Enter") submitLogin(); });

    // result sheet
    $("#m-result-back").addEventListener("click", () => history.back());
    $("#m-result-share").addEventListener("click", async () => {
        if (!state.current) return;
        const tok = currentUser && currentUser.access_token ? encodeURIComponent(currentUser.access_token) : "";
        const url = location.origin + `/api/reports/${state.current.inspection_id}/html?token=${tok}`;
        if (navigator.share) {
            try { await navigator.share({ title: `Inspection ${state.current.inspection_id}`, url }); } catch (_) { }
        } else {
            window.open(url, "_blank");
        }
    });
    window.addEventListener("popstate", () => { if (state.resultOpen) closeResult(); });

    // canvas tap (delegated)
    $("#m-result-body").addEventListener("click", (e) => {
        if (e.target && e.target.id === "r-canvas") canvasTap(e);
    });

    // health dot (public endpoint, works pre-login too)
    fetch("/api/health").then((r) => r.json()).then((h) => {
        if (h && h.device) $("#m-engine-dot").title = `Engine online (${h.device})`;
    }).catch(() => $("#m-engine-dot").classList.add("off"));

    const session = loadSession();
    if (session && session.access_token) {
        currentUser = session;
        hideLoginScreen();
        startMobileApp();
    } else {
        showLoginScreen();
    }
}

document.addEventListener("DOMContentLoaded", init);
