// OLAI desktop client

const API = "/api";
let cameraStream = null;
let capturedPhoto = null;

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

async function uploadPhoto(file) {
    const status = document.getElementById("upload-status");
    status.hidden = false;
    status.textContent = `Uploading ${file.name}...`;
    const form = new FormData();
    form.append("file", file);
    try {
        const response = await fetch(`${API}/documents/upload`, { method: "POST", body: form });
        const body = await response.json();
        if (!response.ok) throw new Error(body.detail || "Upload failed");
        await pollDocument(body.id, status);
        await Promise.all([loadStats(), loadDocuments("recent-documents", 5)]);
    } catch (error) {
        status.textContent = `Could not process photo: ${error.message}`;
    }
}

function closeCamera() {
    cameraStream?.getTracks().forEach(track => track.stop());
    cameraStream = null;
    capturedPhoto = null;
    const modal = document.getElementById("camera-modal");
    if (modal) modal.hidden = true;
    const video = document.getElementById("camera-preview");
    if (video) video.srcObject = null;
}

async function openCamera() {
    const modal = document.getElementById("camera-modal");
    const video = document.getElementById("camera-preview");
    const error = document.getElementById("camera-error");
    modal.hidden = false;
    error.hidden = true;
    document.getElementById("camera-snap").disabled = true;
    try {
        if (!navigator.mediaDevices?.getUserMedia) throw new Error("Live camera is not supported in this browser.");
        const cameraRequest = navigator.mediaDevices.getUserMedia({ video: true, audio: false });
        cameraStream = await Promise.race([
            cameraRequest,
            new Promise((_, reject) => setTimeout(() => reject(new Error("Camera permission timed out.")), 8000)),
        ]);
        video.srcObject = cameraStream;
        await new Promise(resolve => {
            if (video.readyState >= HTMLMediaElement.HAVE_METADATA && video.videoWidth > 0) return resolve();
            video.addEventListener("loadedmetadata", resolve, { once: true });
        });
        await video.play();
        document.getElementById("camera-snap").disabled = false;
    } catch (cameraError) {
        cameraStream?.getTracks().forEach(track => track.stop());
        cameraStream = null;
        error.hidden = false;
        error.textContent = `${cameraError.message} Opening the photo chooser instead.`;
        setTimeout(() => {
            closeCamera();
            document.getElementById("camera-upload")?.click();
        }, 700);
    }
}

function snapCamera() {
    const video = document.getElementById("camera-preview");
    const canvas = document.getElementById("camera-canvas");
    if (!video.videoWidth || !video.videoHeight || video.readyState < HTMLMediaElement.HAVE_CURRENT_DATA) {
        const error = document.getElementById("camera-error");
        error.hidden = false;
        error.textContent = "Camera is still starting. Wait for the preview, then try Capture again.";
        return;
    }
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    canvas.getContext("2d").drawImage(video, 0, 0);
    canvas.toBlob(blob => {
        capturedPhoto = new File([blob], `camera-${Date.now()}.jpg`, { type: "image/jpeg" });
        video.hidden = true;
        document.getElementById("camera-snap").hidden = true;
        document.getElementById("camera-retake").hidden = false;
        document.getElementById("camera-use").hidden = false;
    }, "image/jpeg", 0.92);
}

async function pollDocument(documentId, status) {
    for (let attempt = 0; attempt < 120; attempt += 1) {
        const document = await getJson(`/documents/${encodeURIComponent(documentId)}`);
        if (document.status === "review") {
            status.textContent = `OCR complete. ${document.page_count} page ready for human review.`;
            return;
        }
        if (document.status === "failed") throw new Error(document.error || "OCR failed");
        status.textContent = `Processing ${document.filename}... (${document.status})`;
        await new Promise(resolve => setTimeout(resolve, 1000));
    }
    throw new Error("Processing is taking longer than expected; check the Library for updates.");
}

// Navigation

function switchView(name) {
    document.querySelectorAll(".view-section").forEach(s => s.classList.remove("active"));
    document.querySelectorAll(".nav-btn").forEach(b => b.classList.remove("active"));
    document.getElementById(`view-${name}`)?.classList.add("active");
    document.getElementById(`nav-${name}-btn`)?.classList.add("active");
    document.querySelector(".header-nav")?.classList.remove("open");
    if (name === "library") loadDocuments("library-documents", 1000);
    if (name === "dashboard") loadDashboard();
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

function metricCard(label, value, detail, tone) {
    return `<div class="dashboard-metric ${tone || ""}">
        <div class="dashboard-metric-label">${label}</div>
        <div class="dashboard-metric-value">${value}</div>
        <div class="dashboard-metric-detail">${detail}</div>
    </div>`;
}

function renderBars(items, labelKey, title, color) {
    if (!items.length) return `<div class="chart-empty">No data yet</div>`;
    const max = Math.max(...items.map(item => Number(item.count)), 1);
    return `<div class="dashboard-chart"><div class="dashboard-chart-title">${title}</div>` + items.map(item => {
        const label = escapeHtml(item[labelKey]);
        const width = Math.max(3, (Number(item.count) / max) * 100);
        return `<div class="bar-row"><span class="bar-label">${label}</span><span class="bar-track"><span class="bar-fill" style="width:${width}%;background:${color}"></span></span><strong>${item.count}</strong></div>`;
    }).join("") + `</div>`;
}

async function loadDashboard() {
    const target = document.getElementById("dashboard-content");
    if (!target) return;
    target.innerHTML = `<div class="empty-note">Loading dashboard metrics...</div>`;
    try {
        const metrics = await getJson("/dashboard");
        const confidence = metrics.average_confidence == null ? "—" : `${metrics.average_confidence}%`;
        const throughput = metrics.pages_per_minute == null ? "—" : metrics.pages_per_minute;
        target.innerHTML = `<div class="dashboard-metrics">
            ${metricCard("Documents", metrics.documents, `${metrics.pages} pages captured`, "gold")}
            ${metricCard("Auto-accepted", `${metrics.auto_accepted_percent}%`, "accepted without correction", "green")}
            ${metricCard("Reviewed", `${metrics.reviewed_percent}%`, `${metrics.lines} total OCR lines`, "blue")}
            ${metricCard("Average confidence", confidence, "PaddleOCR line confidence", "amber")}
            ${metricCard("Pages / minute", throughput, metrics.pages_per_minute == null ? "Need 2+ timestamped pages" : "measured from completed pages", "cyan")}
            ${metricCard("Tesseract sent", `${metrics.tesseract_percent}%`, "confidence gate + audit sample", "purple")}
        </div>
        <div class="dashboard-chart-grid">
            ${renderBars(metrics.confidence_buckets, "bucket", "Confidence distribution", "#06b6d4")}
            ${renderBars(metrics.status_breakdown, "status", "Document status", "#f59e0b")}
            ${renderBars(metrics.source_breakdown, "source_type", "Source types", "#10b981")}
        </div>`;
    } catch (error) {
        target.innerHTML = `<div class="empty-note">Could not load dashboard: ${escapeHtml(error.message)}</div>`;
    }
}

async function deleteDocument(documentId, filename) {
    if (!window.confirm(`Delete ${filename}? This removes the original photo, OCR, and review data.`)) return;
    const response = await fetch(`${API}/documents/${encodeURIComponent(documentId)}`, { method: "DELETE" });
    if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        throw new Error(body.detail || "Could not delete document");
    }
    await Promise.all([loadStats(), loadDocuments("recent-documents", 5), loadDocuments("library-documents", 1000)]);
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
            <td><button class="nav-btn" onclick="openReview('${escapeHtml(d.id)}')">${escapeHtml(d.filename)}</button></td>
            <td>${escapeHtml(SOURCE_LABELS[d.source_type] || "–")}</td>
            <td>${verified}</td>
            <td><span class="badge ${cls}">${label}</span></td>
            <td>${escapeHtml(d.created_at)}</td>
            <td><button class="delete-doc-btn" title="Delete document" aria-label="Delete ${escapeHtml(d.filename)}"
                onclick="event.stopPropagation(); deleteDocument('${escapeHtml(d.id)}', '${escapeHtml(d.filename)}')"><i class="fa-solid fa-trash"></i></button></td>
        </tr>`;
    }).join("");
    return `<div class="history-table-wrapper"><table class="history-table">
        <thead><tr><th>ID</th><th>File</th><th>Source type</th><th>Verified pages</th><th>Status</th><th>Added</th><th>Action</th></tr></thead>
        <tbody>${rows}</tbody></table></div>`;
}

async function openReview(documentId) {
    switchView("review");
    const target = document.getElementById("review-content");
    target.innerHTML = "Loading OCR lines...";
    try {
        const document = await getJson(`/documents/${encodeURIComponent(documentId)}/review`);
        const lines = document.pages.flatMap(page => page.lines.map(line => ({
            ...line,
            page_number: page.page_number,
            page_width: page.width || 1,
            page_height: page.height || 1,
        })));
        const reviewer = localStorage.getItem("olaiReviewer") || prompt("Reviewer name");
        if (!reviewer?.trim()) throw new Error("A reviewer name is required.");
        localStorage.setItem("olaiReviewer", reviewer.trim());
        target.innerHTML = `<div class="drop-sub" style="margin-bottom: 16px;">${escapeHtml(document.filename)} &middot; ${lines.length} OCR lines. Select a blue box or line to cross-reference.</div>` +
            (lines.length ? document.pages.map(page => {
                const pageLines = lines.filter(line => line.page_number === page.page_number);
                const imageUrl = page.image_path ? `/storage/${page.image_path}` : "";
                return `<section class="review-page">
                    <div class="review-page-title">Page ${page.page_number}</div>
                    <div class="review-layout">
                        <div class="ocr-image-frame">
                            ${imageUrl ? `<img class="ocr-page-image" src="${escapeHtml(imageUrl)}" alt="${escapeHtml(document.filename)} page ${page.page_number}">` : ""}
                            <div class="ocr-box-layer">${pageLines.map(line => renderOcrBox(line)).join("")}</div>
                        </div>
                        <div class="ocr-line-list">${pageLines.map(line => renderOcrLine(line)).join("")}</div>
                    </div>
                </section>`;
            }).join("") : "<div class='empty-note'>No OCR lines require review.</div>");
    } catch (error) {
        target.innerHTML = `<div class="empty-note">Could not load review: ${escapeHtml(error.message)}</div>`;
    }
}

function renderOcrBox(line) {
    const [x0, y0, x1, y1] = [line.x0, line.y0, line.x1, line.y1].map(Number);
    const left = Math.max(0, (x0 / line.page_width) * 100);
    const top = Math.max(0, (y0 / line.page_height) * 100);
    const width = Math.max(0.5, ((x1 - x0) / line.page_width) * 100);
    const height = Math.max(0.5, ((y1 - y0) / line.page_height) * 100);
    return `<button class="ocr-box" id="box-${line.id}" type="button" title="${escapeHtml(line.ocr_text)}"
        style="left:${left}%;top:${top}%;width:${width}%;height:${height}%;" onclick="selectOcrLine(${line.id})"></button>`;
}

function renderOcrLine(line) {
    return `<article class="ocr-line-card" id="line-card-${line.id}" onclick="selectOcrLine(${line.id})">
        <div class="drop-sub">Line ${line.line_order + 1} &middot; Confidence ${Math.round((line.confidence || 0) * 100)}%</div>
        <textarea id="line-${line.id}" onclick="event.stopPropagation()">${escapeHtml(line.verified_text || line.ocr_text)}</textarea>
        <div class="tanglish-label">Tanglish: <span id="tanglish-${line.id}">${escapeHtml(line.tanglish || "")}</span></div>
        <button class="nav-btn" onclick="event.stopPropagation(); verifyLine(${line.id})"><i class="fa-solid fa-check"></i> Save verification</button>
    </article>`;
}

function selectOcrLine(lineId) {
    document.querySelectorAll(".ocr-box.is-selected").forEach(box => box.classList.remove("is-selected"));
    document.querySelectorAll(".ocr-line-card.is-selected").forEach(card => card.classList.remove("is-selected"));
    document.getElementById(`box-${lineId}`)?.classList.add("is-selected");
    const card = document.getElementById(`line-card-${lineId}`);
    card?.classList.add("is-selected");
    card?.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

async function verifyLine(lineId) {
    const text = document.getElementById(`line-${lineId}`).value;
    const reviewer = localStorage.getItem("olaiReviewer") || prompt("Reviewer name");
    if (!reviewer?.trim()) return;
    localStorage.setItem("olaiReviewer", reviewer.trim());
    const response = await fetch(`${API}/lines/${lineId}/verify`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ reviewer, text, action: "edit" }),
    });
    if (!response.ok) throw new Error("Could not save verification");
    document.getElementById(`line-${lineId}`).style.borderColor = "#10b981";
    const refreshed = await response.json();
    const tanglish = document.getElementById(`tanglish-${lineId}`);
    if (tanglish) tanglish.textContent = refreshed.tanglish || "Saved; reload review to refresh transliteration.";
    loadStats();
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
    document.getElementById("camera-launch")?.addEventListener("click", openCamera);
    document.getElementById("camera-close")?.addEventListener("click", closeCamera);
    document.getElementById("camera-snap")?.addEventListener("click", snapCamera);
    document.getElementById("camera-retake")?.addEventListener("click", () => {
        capturedPhoto = null;
        document.getElementById("camera-preview").hidden = false;
        document.getElementById("camera-snap").hidden = false;
        document.getElementById("camera-retake").hidden = true;
        document.getElementById("camera-use").hidden = true;
    });
    document.getElementById("camera-use")?.addEventListener("click", () => {
        if (capturedPhoto) uploadPhoto(capturedPhoto);
        closeCamera();
    });
    ["document-upload", "camera-upload"].forEach(id => {
        document.getElementById(id)?.addEventListener("change", event => {
            const [file] = event.target.files;
            if (file) uploadPhoto(file);
            event.target.value = "";
        });
    });
    loadHealth();
    loadStats();
    loadDocuments("recent-documents", 5);
});
