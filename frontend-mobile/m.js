// OLAI mobile client

const API = "/api";

function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>"']/g, c => ({
        "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[c]));
}

async function getJson(path) {
    const res = await fetch(API + path);
    if (!res.ok) throw new Error(`${path}: HTTP ${res.status}`);
    return res.json();
}

function switchView(name) {
    document.querySelectorAll(".mview").forEach(v => v.classList.toggle("active", v.id === `view-${name}`));
    document.querySelectorAll(".mnav-btn").forEach(b => b.classList.toggle("active", b.dataset.view === name));
}

async function loadHealth() {
    const dot = document.getElementById("m-engine-dot");
    try {
        const health = await getJson("/health");
        const ok = health.engines.every(e => e.available);
        dot.classList.toggle("off", !ok);
        dot.title = ok ? "Engines ready" : "Some engines missing";
    } catch (e) {
        dot.classList.add("off");
        dot.title = "Server offline";
    }
}

async function loadRecent() {
    const el = document.getElementById("m-recent");
    try {
        const { documents } = await getJson("/documents?limit=10");
        el.innerHTML = documents.length
            ? documents.map(d => `<div class="mrow"><div class="mrow-mid">
                <div class="mrow-name">${escapeHtml(d.filename)}</div>
                <div class="mrow-sub">${escapeHtml(d.id)} &middot; ${escapeHtml(d.status)}</div>
              </div></div>`).join("")
            : `<div class="mrecent-empty">No documents yet.</div>`;
    } catch (e) {
        el.innerHTML = `<div class="mrecent-empty">Could not load documents.</div>`;
    }
}

async function loadStats() {
    const el = document.getElementById("m-stat-grid");
    try {
        const s = await getJson("/stats");
        const tiles = [
            ["Documents", s.documents], ["Pages", s.pages],
            ["Verified pages", s.pages_verified], ["Awaiting review", s.lines_needing_review],
        ];
        el.innerHTML = tiles.map(([k, v]) =>
            `<div class="mstat"><div class="mstat-k">${k}</div><div class="mstat-v">${v}</div></div>`).join("");
    } catch (e) {
        el.innerHTML = `<div class="mrecent-empty">Could not load stats.</div>`;
    }
}

document.addEventListener("DOMContentLoaded", () => {
    document.querySelectorAll(".mnav-btn").forEach(b => b.addEventListener("click", () => switchView(b.dataset.view)));
    loadHealth();
    loadRecent();
    loadStats();
});
