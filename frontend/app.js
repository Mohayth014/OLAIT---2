// OLAI desktop client

const API = "/api";

const STATUS_BADGES = {
    uploaded: ["badge-review", "Uploaded"],
    processing: ["badge-review", "Processing"],
    review: ["badge-review", "Needs review"],
    verified: ["badge-pass", "Verified"],
    failed: ["badge-fail", "Failed"],
};

const SOURCE_LABELS = {
    modern_print: "Modern print",
    historical_print: "Historical print",
    handwritten: "Handwritten",
    palm_leaf: "Palm-leaf",
    inscription: "Inscription",
};

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

// Navigation

function switchView(name) {
    document.querySelectorAll(".view-section").forEach(s => s.classList.remove("active"));
    document.querySelectorAll(".nav-btn").forEach(b => b.classList.remove("active"));
    document.getElementById(`view-${name}`)?.classList.add("active");
    document.getElementById(`nav-${name}-btn`)?.classList.add("active");
    document.querySelector(".header-nav")?.classList.remove("open");
    if (name === "library") loadDocuments("library-documents", 1000);
}

function toggleMobileNav() {
    document.querySelector(".header-nav")?.classList.toggle("open");
}

// Engine status

async function loadHealth() {
    const dot = document.getElementById("engine-dot");
    const text = document.getElementById("engine-text");
    const badge = document.getElementById("engine-badge");
    try {
        const health = await getJson("/health");
        const missing = health.engines.filter(e => !e.available).map(e => e.name);
        dot.classList.toggle("online", missing.length === 0);
        text.textContent = missing.length
            ? `Missing: ${missing.join(", ")}`
            : `${health.language} engines ready`;
        badge.title = health.engines.map(e => `${e.available ? "✓" : "✗"} ${e.name}: ${e.detail}`).join("\n");
    } catch (e) {
        dot.classList.remove("online");
        text.textContent = "Server offline";
    }
}

// Stats + documents

async function loadStats() {
    try {
        const s = await getJson("/stats");
        document.getElementById("stat-documents").textContent = s.documents;
        document.getElementById("stat-pages").textContent = s.pages;
        document.getElementById("stat-verified").textContent = s.pages_verified;
        document.getElementById("stat-review").textContent = s.lines_needing_review;
    } catch (e) {
        console.error(e);
    }
}

function renderDocuments(docs) {
    if (!docs.length) {
        return `<div class="empty-note"><i class="fa-regular fa-folder-open"></i>
            No documents yet. Upload a Tamil book or page to get started.</div>`;
    }
    const rows = docs.map(d => {
        const [cls, label] = STATUS_BADGES[d.status] || ["badge-review", d.status];
        const verified = d.page_count ? `${d.verified_pages} / ${d.page_count}` : "–";
        return `<tr>
            <td class="mono">${escapeHtml(d.id)}</td>
            <td>${escapeHtml(d.filename)}</td>
            <td>${escapeHtml(SOURCE_LABELS[d.source_type] || "–")}</td>
            <td>${verified}</td>
            <td><span class="badge ${cls}">${label}</span></td>
            <td>${escapeHtml(d.created_at)}</td>
        </tr>`;
    }).join("");
    return `<div class="history-table-wrapper"><table class="history-table">
        <thead><tr><th>ID</th><th>File</th><th>Source type</th><th>Verified pages</th><th>Status</th><th>Added</th></tr></thead>
        <tbody>${rows}</tbody></table></div>`;
}

async function loadDocuments(targetId, limit) {
    const el = document.getElementById(targetId);
    try {
        const { documents } = await getJson(`/documents?limit=${limit}`);
        el.innerHTML = renderDocuments(documents);
    } catch (e) {
        el.innerHTML = `<div class="empty-note">Could not load documents: ${escapeHtml(e.message)}</div>`;
    }
}

document.addEventListener("DOMContentLoaded", () => {
    loadHealth();
    loadStats();
    loadDocuments("recent-documents", 5);
});
