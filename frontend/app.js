// Global State
let currentInspection = null;
let catalogItems = [];
let allBoxes = [];
let activeBoxFilter = 'all';
let canvasImage = null;
let cameraStream = null;
let dualFrontFile = null;
let dualBackFile = null;
let activeVisualPanel = 'front';

// ================= Officer Inspection-Session Workflow =================
// Additive on top of the existing ad-hoc single-scan flow above -- see
// scanSampleForProduct()/executeScan()/renderSessionWorkspace() below.
let currentSession = null;             // full session-summary object (from GET /api/sessions/{id}/summary)
let activeScanContext = null;          // {session_id, product_id, product_name} while "inside" a product's scan flow
let currentDetailReturnContext = null; // snapshot of activeScanContext at the moment a scan landed on view-detail

// ================= Auth / Session (two-portal RBAC) =================
// The frontend gating below (hiding buttons/nav per role) is UX polish only.
// The real security boundary is enforced server-side on every endpoint --
// see backend/auth.py. Never assume hiding an element here is protection.
const AUTH_STORAGE_KEY = "traceSession";
let currentUser = null; // { access_token, user_id, name, role }

function loadSession() {
    try {
        const raw = localStorage.getItem(AUTH_STORAGE_KEY);
        return raw ? JSON.parse(raw) : null;
    } catch (e) { return null; }
}
function saveSession(session) {
    try { localStorage.setItem(AUTH_STORAGE_KEY, JSON.stringify(session)); } catch (e) { /* ignore */ }
}
function clearSession() {
    try { localStorage.removeItem(AUTH_STORAGE_KEY); } catch (e) { /* ignore */ }
}

// Wraps fetch() to attach the bearer token and handle an expired/invalid session.
// Every API call in this file should go through this (not raw fetch), except the
// login call itself (which has no token yet) and report links (see reportUrlFor).
async function apiFetch(url, opts = {}) {
    const headers = Object.assign({}, opts.headers || {});
    if (currentUser && currentUser.access_token) {
        headers["Authorization"] = `Bearer ${currentUser.access_token}`;
    }
    const resp = await fetch(url, Object.assign({}, opts, { headers }));
    if (resp.status === 401) {
        clearSession();
        currentUser = null;
        showAuthGate("Your session expired. Please sign in again.");
        throw new Error("Session expired.");
    }
    return resp;
}

function showAuthGate(message) {
    const gate = document.getElementById("auth-gate");
    if (gate) gate.style.display = "flex";
    const errEl = document.getElementById("auth-error");
    if (errEl) {
        if (message) { errEl.textContent = message; errEl.style.display = "block"; }
        else { errEl.style.display = "none"; }
    }
}
function hideAuthGate() {
    const gate = document.getElementById("auth-gate");
    if (gate) gate.style.display = "none";
}

// Display titles for the two access levels. The role identifiers themselves stay
// 'inspector' / 'manager' -- every permission check on the server keys off those.
const ROLE_LABELS = {
    inspector: "Legal Metrology Officer",
    manager: "Controller of Legal Metrology",
};
function roleLabel(role) {
    return (currentUser && currentUser.role_label) || ROLE_LABELS[role] || role || "";
}

// Registration comes before login: a fresh browser lands on the Register tab, and
// once an officer has registered or signed in here we remember Sign In for next time.
const AUTH_TAB_KEY = "traceAuthTab";
let activeAuthTab = "register";

function switchAuthTab(tab) {
    activeAuthTab = tab;
    document.querySelectorAll(".auth-panel").forEach(p => p.classList.remove("active"));
    document.querySelectorAll(".auth-tab").forEach(t => t.classList.remove("active"));
    const panel = document.getElementById(`auth-panel-${tab}`);
    const tabBtn = document.getElementById(`auth-tab-${tab}`);
    if (panel) panel.classList.add("active");
    if (tabBtn) tabBtn.classList.add("active");

    const btn = document.getElementById("auth-submit-btn");
    if (btn) {
        btn.innerHTML = tab === "register"
            ? `<i class="fa-solid fa-user-plus"></i> Create Officer Account`
            : `<i class="fa-solid fa-right-to-bracket"></i> Sign In`;
    }
    document.querySelectorAll(".auth-error").forEach(e => { e.style.display = "none"; });
}

function submitAuth() {
    return activeAuthTab === "register" ? submitRegister() : submitLogin();
}

// Shared post-credential handling for both register and login responses.
function completeAuth(body) {
    currentUser = body; // { access_token, user_id, name, role, role_label, officer_id, jurisdiction }
    saveSession(currentUser);
    try { localStorage.setItem(AUTH_TAB_KEY, "login"); } catch (e) { /* ignore */ }
    hideAuthGate();
    startApp();
}

async function submitRegister() {
    const errEl = document.getElementById("reg-error");
    const officer_name = (document.getElementById("reg-name").value || "").trim();
    const officer_id = (document.getElementById("reg-officer-id").value || "").trim();
    const password = document.getElementById("reg-password").value || "";
    const jurisdiction = (document.getElementById("reg-jurisdiction").value || "").trim();

    const missing = !officer_name || !officer_id || !password || !jurisdiction;
    if (missing) {
        errEl.textContent = "All four fields are required to register.";
        errEl.style.display = "block";
        return;
    }
    try {
        const resp = await fetch("/api/auth/register", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ officer_name, officer_id, password, jurisdiction })
        });
        const body = await resp.json().catch(() => ({}));
        if (!resp.ok) throw new Error(body.detail || "Registration failed.");
        completeAuth(body);
    } catch (e) {
        errEl.textContent = e.message;
        errEl.style.display = "block";
    }
}

async function submitLogin() {
    const officer_name = (document.getElementById("auth-name").value || "").trim();
    const identifier = (document.getElementById("auth-email").value || "").trim();
    const password = document.getElementById("auth-password").value || "";
    const errEl = document.getElementById("auth-error");
    if (!officer_name || !identifier || !password) {
        errEl.textContent = "Enter your Officer Name, Officer ID and password.";
        errEl.style.display = "block";
        return;
    }
    try {
        const resp = await fetch("/api/auth/login", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ officer_name, identifier, password })
        });
        const body = await resp.json().catch(() => ({}));
        if (!resp.ok) throw new Error(body.detail || "Invalid Officer ID or password.");
        completeAuth(body);
    } catch (e) {
        errEl.textContent = e.message;
        errEl.style.display = "block";
    }
}

function logout() {
    clearSession();
    currentUser = null;
    location.hash = "";
    location.reload();
}

// Shows/hides nav items and actions per role. UX convenience only -- see note above.
function applyRoleUI(role) {
    const isInspector = role === "inspector";
    const isManager = role === "manager";

    const set = (id, visible, display = "flex") => {
        const el = document.getElementById(id);
        if (el) el.style.display = visible ? display : "none";
    };

    set("nav-scanner-btn", isInspector);           // scanning is inspector-only
    set("nav-myscans-btn", isInspector);            // inspector's own-scan history
    set("nav-dashboard-btn", isManager);            // manager oversight dashboard
    set("nav-gallery-btn", isInspector);            // gallery triggers scans -> inspector-only
    set("nav-officerdash-btn", isInspector);        // officer dashboard (distinct from manager's)
    set("nav-newinspection-btn", isInspector);      // start a new inspection session
    set("nav-activeinspections-btn", isInspector);  // this officer's own open sessions
    set("nav-reports-btn", isInspector);            // this officer's own sessions, for report access
    set("history-inspector-filter", isManager, "inline-block");
    set("logout-btn", true, "inline-flex");

    const badge = document.getElementById("user-badge-text");
    if (badge && currentUser) {
        const idPart = currentUser.officer_id ? ` • ${currentUser.officer_id}` : "";
        badge.textContent = `${currentUser.name}${idPart}`;
        badge.title = `${currentUser.name} — ${roleLabel(currentUser.role)}`;
    }
}

function startApp() {
    applyRoleUI(currentUser.role);
    loadCatalog();

    if (currentUser.role === "manager") {
        loadDashboard();
    } else {
        loadMyScans();
    }

    // Deep-link support: opens that view on load if the hash names one
    const deepLink = (location.hash || "").replace("#", "");
    const deepLinkable = [
        "dashboard", "gallery", "scanner", "myscans",
        "officer-dashboard", "new-inspection", "active-inspections", "reports",
    ];
    if (deepLinkable.includes(deepLink)) {
        switchView(deepLink);
    } else {
        switchView(currentUser.role === "manager" ? "dashboard" : "officer-dashboard");
    }
}

// ---------- Mobile navigation menu ----------
function toggleMobileNav() {
    const hdr = document.querySelector(".app-header");
    if (hdr) hdr.classList.toggle("nav-open");
}
function closeMobileNav() {
    const hdr = document.querySelector(".app-header");
    if (hdr) hdr.classList.remove("nav-open");
}

// Initialize on DOM Ready
document.addEventListener("DOMContentLoaded", () => {
    initCanvasEvents();

    // Collapse the mobile menu when tapping anywhere outside it
    document.addEventListener("click", (e) => {
        const hdr = document.querySelector(".app-header");
        if (hdr && hdr.classList.contains("nav-open") &&
            !e.target.closest(".header-nav") && !e.target.closest(".nav-toggle")) {
            hdr.classList.remove("nav-open");
        }
    });

    // Submit whichever auth panel is open on Enter
    document.querySelectorAll("#auth-gate input").forEach(input => {
        input.addEventListener("keydown", (e) => { if (e.key === "Enter") submitAuth(); });
    });

    // Registration precedes login, so a browser that has never authenticated here
    // opens on the Register tab; afterwards it opens on Sign In.
    let startTab = "register";
    try { if (localStorage.getItem(AUTH_TAB_KEY) === "login") startTab = "login"; } catch (e) { /* ignore */ }
    switchAuthTab(startTab);

    const session = loadSession();
    if (session && session.access_token) {
        currentUser = session;
        hideAuthGate();
        startApp();
    } else {
        showAuthGate();
    }
});

// View Switching
const OFFICER_SESSION_VIEWS = ['officer-dashboard', 'new-inspection', 'active-inspections', 'reports', 'session-workspace'];

function switchView(viewName) {
    closeMobileNav();

    // Role guardrails: managers have no scanner/gallery/officer views, inspectors have no
    // manager dashboard. (The server enforces this regardless; this just avoids landing
    // on a dead view.)
    if (currentUser) {
        if ((viewName === 'scanner' || viewName === 'gallery' || OFFICER_SESSION_VIEWS.includes(viewName)) && currentUser.role !== 'inspector') viewName = 'dashboard';
        if (viewName === 'dashboard' && currentUser.role !== 'manager') viewName = 'myscans';
        if (viewName === 'myscans' && currentUser.role !== 'inspector') viewName = 'dashboard';
    }

    // A sample scan taken "inside" a product only stays linked to that product across
    // the scanner -> detail hop. Navigating anywhere else (including back into the
    // session workspace itself) always requires an explicit "Scan Sample" click on a
    // product card before the next scan links anywhere -- this avoids ambiguous/stale
    // linkage when a session has multiple products.
    if (viewName !== 'scanner' && viewName !== 'detail') {
        activeScanContext = null;
    }

    document.querySelectorAll(".view-section").forEach(sec => sec.classList.remove("active"));
    document.querySelectorAll(".nav-btn").forEach(btn => btn.classList.remove("active"));

    const targetSec = document.getElementById(`view-${viewName}`);
    const targetBtn = document.getElementById(`nav-${viewName}-btn`);

    if (targetSec) targetSec.classList.add("active");
    if (targetBtn) targetBtn.classList.add("active");

    if (viewName === 'dashboard') {
        loadDashboard();
    } else if (viewName === 'myscans') {
        loadHistory2();
    } else if (viewName === 'gallery') {
        renderGallery(catalogItems);
    } else if (viewName === 'officer-dashboard') {
        loadOfficerDashboard();
    } else if (viewName === 'new-inspection') {
        prepareNewInspectionForm();
    } else if (viewName === 'active-inspections') {
        loadActiveInspections();
    } else if (viewName === 'reports') {
        loadReportsSessions();
    } else if (viewName === 'scanner') {
        updateScanContextBanner();
    }
}

// Keeps the scanner's "Scanning sample for Product X" banner in sync with
// activeScanContext -- shown when scanning inside a product's flow, hidden for an
// ordinary ad-hoc scan (including after activeScanContext was cleared by navigation).
function updateScanContextBanner() {
    const banner = document.getElementById("scan-context-banner");
    if (!banner) return;
    if (activeScanContext) {
        banner.style.display = "block";
        banner.innerHTML = `<i class="fa-solid fa-box"></i> Scanning sample for <strong>${activeScanContext.product_name}</strong> &mdash; Inspection <strong>${activeScanContext.session_id}</strong>`;
    } else {
        banner.style.display = "none";
    }
}

// Input Mode Switching
function switchInputMode(modeName) {
    document.querySelectorAll(".input-mode-content").forEach(el => el.classList.remove("active"));
    document.querySelectorAll(".tab-btn").forEach(btn => btn.classList.remove("active"));

    const targetMode = document.getElementById(`mode-${modeName}`);
    if (targetMode) targetMode.classList.add("active");

    event.currentTarget.classList.add("active");

    if (modeName !== 'camera' && cameraStream) {
        stopCamera();
    }
}

// Load Catalog Products for Carousel & Gallery
async function loadCatalog() {
    try {
        const resp = await apiFetch("/api/catalog");
        catalogItems = await resp.json();
        
        document.getElementById("gallery-count").textContent = catalogItems.length;
        renderCarouselChips(catalogItems);
    } catch (err) {
        console.error("Error loading catalog:", err);
    }
}

// Render Carousel Chips in Scanner
function renderCarouselChips(items) {
    const container = document.getElementById("dataset-chips-container");
    if (!container) return;

    if (!items || items.length === 0) {
        container.innerHTML = `<div style="color: #94a3b8; font-size: 13px;">No indexed images found.</div>`;
        return;
    }

    let html = "";
    items.forEach(item => {
        html += `
            <div class="dataset-chip" onclick="inspectDatasetProduct('${item.filename}', this)">
                <img src="${item.thumbnail_url}" class="chip-img" alt="${item.name}">
                <div class="chip-name" title="${item.name}">${item.name}</div>
                <div class="chip-cat" title="${item.category}">${item.category}</div>
            </div>
        `;
    });
    container.innerHTML = html;
}

// 1-Click Inspection from Dataset
async function inspectDatasetProduct(filename, chipElement) {
    document.querySelectorAll(".dataset-chip").forEach(c => c.classList.remove("selected"));
    if (chipElement) chipElement.classList.add("selected");

    const formData = new FormData();
    formData.append("dataset_filename", filename);

    await executeScan(formData);
}

// File Upload Handler
async function handleFileUpload(event) {
    const file = event.target.files[0];
    if (!file) return;

    const formData = new FormData();
    formData.append("file", file);

    await executeScan(formData);
}

// Dual-Panel Multi-Angle File Upload Handlers
function handleDualFileSelect(panel, event) {
    const file = event.target.files[0];
    if (!file) return;

    if (panel === 'front') {
        dualFrontFile = file;
        const lbl = document.getElementById("front-file-label");
        const sub = document.getElementById("front-file-sub");
        const box = document.getElementById("dual-drop-front");
        if (lbl) lbl.textContent = file.name;
        if (sub) sub.textContent = `${(file.size / 1024).toFixed(1)} KB (Front Ready)`;
        if (box) box.classList.add("has-file");
    } else {
        dualBackFile = file;
        const lbl = document.getElementById("back-file-label");
        const sub = document.getElementById("back-file-sub");
        const box = document.getElementById("dual-drop-back");
        if (lbl) lbl.textContent = file.name;
        if (sub) sub.textContent = `${(file.size / 1024).toFixed(1)} KB (Back Ready)`;
        if (box) box.classList.add("has-file");
    }
}

async function executeDualPanelScan() {
    if (!dualFrontFile && !dualBackFile) {
        alert("Please select at least a Front Panel image before scanning.");
        return;
    }
    const formData = new FormData();
    if (dualFrontFile) formData.append("file", dualFrontFile);
    if (dualBackFile) formData.append("back_file", dualBackFile);
    await executeScan(formData);
}

// Drag & Drop Setup
const dropZone = document.getElementById("drop-zone");
if (dropZone) {
    ['dragenter', 'dragover', 'dragleave', 'drop'].forEach(eventName => {
        dropZone.addEventListener(eventName, preventDefaults, false);
    });
    function preventDefaults(e) { e.preventDefault(); e.stopPropagation(); }

    ['dragenter', 'dragover'].forEach(eventName => {
        dropZone.addEventListener(eventName, () => dropZone.style.borderColor = "var(--accent-blue)", false);
    });
    ['dragleave', 'drop'].forEach(eventName => {
        dropZone.addEventListener(eventName, () => dropZone.style.borderColor = "rgba(56, 189, 248, 0.3)", false);
    });

    dropZone.addEventListener('drop', (e) => {
        const dt = e.dataTransfer;
        const file = dt.files[0];
        if (file) {
            const formData = new FormData();
            formData.append("file", file);
            executeScan(formData);
        }
    });
}

// Camera Streaming
async function startCamera() {
    try {
        const stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "environment" } });
        cameraStream = stream;
        const video = document.getElementById("camera-feed");
        video.srcObject = stream;
    } catch (err) {
        alert("Camera access failed or denied: " + err.message);
    }
}

function stopCamera() {
    if (cameraStream) {
        cameraStream.getTracks().forEach(track => track.stop());
        cameraStream = null;
        const video = document.getElementById("camera-feed");
        if (video) video.srcObject = null;
    }
}

function captureCamera() {
    const video = document.getElementById("camera-feed");
    const canvas = document.getElementById("camera-canvas");
    if (!video || !cameraStream) {
        alert("Please start the camera first.");
        return;
    }

    canvas.width = video.videoWidth || 640;
    canvas.height = video.videoHeight || 480;
    const ctx = canvas.getContext("2d");
    ctx.drawImage(video, 0, 0, canvas.width, canvas.height);

    canvas.toBlob((blob) => {
        const formData = new FormData();
        formData.append("file", blob, "camera_capture.jpg");
        stopCamera();
        executeScan(formData);
    }, "image/jpeg", 0.9);
}

// Execute AI Scan
async function executeScan(formData) {
    if (!currentUser || currentUser.role !== 'inspector') {
        alert("Only Legal Metrology Officers can scan packages. Controllers have oversight access instead.");
        return;
    }
    const loader = document.getElementById("processing-loader");
    const resultsContainer = document.getElementById("inspection-results");

    loader.style.display = "block";
    resultsContainer.style.display = "none";
    loader.scrollIntoView({ behavior: "smooth" });

    // Step animation simulation
    animateSteps();

    // If this scan is being taken for a product inside an active inspection session,
    // tag it so the backend links the resulting sample to that product.
    if (activeScanContext) {
        formData.append("session_id", activeScanContext.session_id);
        formData.append("product_id", activeScanContext.product_id);
    }

    try {
        const resp = await apiFetch("/api/scan", {
            method: "POST",
            body: formData
        });

        if (!resp.ok) {
            const bodyText = await resp.text().catch(() => "");
            let detail = `Scanning failed (HTTP ${resp.status}).`;
            try {
                const errData = JSON.parse(bodyText);
                if (errData && errData.detail) detail = errData.detail;
            } catch (_) {
                if (bodyText) detail = `${detail} ${bodyText}`.trim();
            }
            throw new Error(detail);
        }

        const data = await resp.json();
        currentInspection = data;
        currentDetailReturnContext = activeScanContext;
        switchView('detail');
        renderInspectionResults(data);
    } catch (err) {
        alert("Compliance Scan Error: " + err.message);
        console.error(err);
    } finally {
        loader.style.display = "none";
    }
}

function animateSteps() {
    const steps = [
        { id: "pstep-1", title: "Step 1: Image Preprocessing", desc: "Correcting EXIF orientation, enhancing contrast & checking resolution..." },
        { id: "pstep-2", title: "Step 2: CLIP Visual AI", desc: "Evaluating zero-shot category classification and generating 512-dim embedding..." },
        { id: "pstep-3", title: "Step 3: Vector Similarity Search", desc: "Matching packaging against catalog database..." },
        { id: "pstep-4", title: "Step 4: EasyOCR Text Extraction", desc: "Detecting bounding boxes and transcribing mandatory declarations..." },
        { id: "pstep-5", title: "Step 5: Legal Metrology Rule Audit", desc: "Validating Rule 6(1)(a)-(f) and Rule 7 readability..." }
    ];

    let currentStep = 0;
    const interval = setInterval(() => {
        if (currentStep >= steps.length) {
            clearInterval(interval);
            return;
        }
        document.querySelectorAll(".step-node").forEach(n => n.classList.remove("active"));
        const step = steps[currentStep];
        const el = document.getElementById(step.id);
        if (el) el.classList.add("active");
        
        document.getElementById("processing-step-title").textContent = step.title;
        document.getElementById("processing-step-desc").textContent = step.desc;

        currentStep++;
    }, 900);
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

// Render Results on UI
function renderInspectionResults(data) {
    const resultsContainer = document.getElementById("inspection-results");
    resultsContainer.style.display = "grid";
    resultsContainer.scrollIntoView({ behavior: "smooth" });

    // Sample-context banner: only shown when this scan was just taken inside a
    // product's scan flow (see executeScan()); hidden for an ad-hoc scan or a
    // historical record opened from any list.
    const sampleBanner = document.getElementById("sample-context-banner");
    if (sampleBanner) {
        if (currentDetailReturnContext) {
            sampleBanner.style.display = "block";
            sampleBanner.innerHTML = `<i class="fa-solid fa-box"></i> Sample for <strong>${currentDetailReturnContext.product_name}</strong> &mdash; Inspection <strong>${currentDetailReturnContext.session_id}</strong>`;
        } else {
            sampleBanner.style.display = "none";
        }
    }

    // 1. Banner
    const banner = document.getElementById("compliance-banner");
    const statusTag = document.getElementById("banner-status-tag");
    const confVal = document.getElementById("banner-conf-val");
    const summaryText = document.getElementById("banner-summary-text");

    banner.className = "compliance-banner glass-card";
    if (data.overall_status === "COMPLIANT") {
        banner.classList.add("status-compliant");
        statusTag.textContent = "COMPLIANT";
    } else if (data.overall_status === "NON_COMPLIANT") {
        banner.classList.add("status-non-compliant");
        statusTag.textContent = "NON-COMPLIANT";
    } else {
        banner.classList.add("status-review");
        statusTag.textContent = "REVIEW REQUIRED";
    }

    confVal.textContent = `${Math.round(data.overall_confidence * 100)}%`;
    summaryText.textContent = data.summary;

    // 1b. Decision vs Override -- two distinct actions, two distinct buttons.
    // Inspector: their own routine post-scan call (only ever reachable on their own
    // scans, since the server 403s a GET on anyone else's). Manager: overriding an
    // already-recorded verdict, which requires a written reason and never touches
    // the inspector's own decision record.
    const decisionBtn = document.getElementById("btn-record-decision");
    const overrideBtn = document.getElementById("btn-override-verdict");
    if (currentUser && decisionBtn && overrideBtn) {
        decisionBtn.style.display = currentUser.role === "inspector" ? "inline-flex" : "none";
        overrideBtn.style.display = currentUser.role === "manager" ? "inline-flex" : "none";
    }
    if (data.override_history && data.override_history.length) {
        const last = data.override_history[data.override_history.length - 1];
        summaryText.textContent += ` [Overridden by ${last.manager_name || last.manager_id}: "${last.reason}"]`;
    }

    // 2. Violations Box
    const violBox = document.getElementById("violations-container");
    const violList = document.getElementById("violations-list");
    if (data.violations && data.violations.length > 0) {
        violBox.style.display = "block";
        violList.innerHTML = data.violations.map(v => `<li>${v}</li>`).join("");
    } else {
        violBox.style.display = "none";
    }

    // 3. Declarations
    const ext = data.extracted_data;
    renderDeclRow("product-name", ext.product_name);
    renderDeclRow("brand", ext.brand);
    renderDeclRow("net-quantity", ext.net_quantity);
    renderDeclRow("mrp", ext.mrp);
    renderDeclRow("usp", ext.unit_sale_price);
    renderDeclRow("mfg-date", ext.mfg_date);
    renderDeclRow("best-before", ext.best_before);
    renderDeclRow("manufacturer", ext.manufacturer);
    renderDeclRow("consumer-care", ext.consumer_care);
    renderDeclRow("fssai", ext.fssai_license);
    renderDeclRow("origin", ext.country_of_origin);

    // CLIP category badge
    if (data.clip_categories && data.clip_categories.length > 0) {
        const topCat = data.clip_categories[0];
        document.getElementById("clip-category-badge").textContent = `Visual Category: ${topCat.label} (${topCat.percentage}%)`;
    }

    // 4. Readability & Rule 7
    const read = data.readability;
    document.getElementById("blur-score").textContent = read.blur_score;
    document.getElementById("contrast-score").textContent = read.contrast_score;
    document.getElementById("font-height").textContent = read.avg_font_height_px ? `${read.avg_font_height_px}px` : "--";
    document.getElementById("text-coverage").textContent = `${(read.estimated_text_coverage * 100).toFixed(1)}%`;

    const readBadge = document.getElementById("readability-badge");
    if (read.is_legible) {
        readBadge.className = "badge badge-pass";
        readBadge.textContent = "Rule 7 Legible";
    } else {
        readBadge.className = "badge badge-review";
        readBadge.textContent = "Rule 7 Review";
    }

    const warnBox = document.getElementById("readability-warnings");
    if (read.warnings && read.warnings.length > 0) {
        warnBox.style.display = "block";
        warnBox.textContent = read.warnings.join(" | ");
    } else {
        warnBox.style.display = "none";
    }

    // 5. Rules Table
    const rulesBody = document.getElementById("rules-table-body");
    rulesBody.innerHTML = data.rule_results.map(r => {
        const badgeClass = r.status === "PASS" ? "badge-pass" : (r.status === "FAIL" ? "badge-fail" : "badge-review");
        const diagnosisHtml = (r.root_cause && r.rectification) ? `
            <div style="margin-top: 6px; padding: 6px 8px; background: rgba(59, 130, 246, 0.1); border-left: 3px solid #3b82f6; border-radius: 4px;">
                <div style="font-size: 9px; font-weight: 700; text-transform: uppercase; color: #60a5fa; letter-spacing: 0.5px;">Root Cause: ${rootCauseLabel(r.root_cause)}</div>
                <div style="font-size: 11px; color: var(--text-secondary); margin-top: 2px;"><strong>Fix:</strong> ${r.rectification}</div>
            </div>` : "";
        return `
            <tr>
                <td style="font-family: var(--font-mono); font-weight: 600;">${r.rule_id}</td>
                <td>
                    <div style="font-weight: 600;">${r.title}</div>
                    <div style="font-size: 10px; color: var(--text-muted);">${r.legal_reference}</div>
                </td>
                <td><span class="badge ${badgeClass}">${r.status}</span></td>
                <td style="color: var(--text-secondary);">${r.message}${diagnosisHtml}</td>
            </tr>
        `;
    }).join("");

    // 6. Similar Products
    const simContainer = document.getElementById("similar-products-container");
    if (data.similar_products && data.similar_products.length > 0) {
        simContainer.innerHTML = data.similar_products.map(p => `
            <div class="similar-item">
                <img src="${p.thumbnail_url}" class="similar-thumb" alt="${p.name}">
                <div class="similar-name" title="${p.name}">${p.name}</div>
                <div class="similar-badge">${p.similarity_percentage}% Match</div>
            </div>
        `).join("");
    } else {
        simContainer.innerHTML = `<div style="grid-column: 1/-1; color: #64748b; font-size: 12px; text-align: center;">No similar packaging records.</div>`;
    }

    // 7. Render Canvas & Bounding Boxes
    allBoxes = data.ocr_boxes || [];
    activeBoxFilter = 'all';

    const panelToolbar = document.getElementById("panel-toggle-toolbar");
    if (data.is_dual_panel && data.back_image_url) {
        if (panelToolbar) panelToolbar.style.display = "flex";
        const frontCount = allBoxes.filter(b => (b.panel || 'front') !== 'back').length;
        const backCount = allBoxes.filter(b => b.panel === 'back').length;
        const fc = document.getElementById("front-box-count");
        const bc = document.getElementById("back-box-count");
        if (fc) fc.textContent = frontCount;
        if (bc) bc.textContent = backCount;
        activeVisualPanel = 'front';
        const pf = document.getElementById("pill-panel-front");
        const pb = document.getElementById("pill-panel-back");
        if (pf) pf.classList.add("active");
        if (pb) pb.classList.remove("active");
    } else {
        if (panelToolbar) panelToolbar.style.display = "none";
        activeVisualPanel = 'front';
    }

    initCanvasImage(data.image_url);
}

function switchVisualPanel(panel) {
    if (!currentInspection) return;
    activeVisualPanel = panel;
    document.querySelectorAll(".panel-pill").forEach(p => p.classList.remove("active"));
    if (panel === 'front') {
        const pf = document.getElementById("pill-panel-front");
        if (pf) pf.classList.add("active");
        initCanvasImage(currentInspection.image_url);
    } else {
        const pb = document.getElementById("pill-panel-back");
        if (pb) pb.classList.add("active");
        if (currentInspection.back_image_url) {
            initCanvasImage(currentInspection.back_image_url);
        }
    }
}

// Opens a past inspection (from My Scans or the manager Dashboard history table)
// into the full interactive results view. Server-side BOLA check applies: an
// inspector gets a 403 here for anything that isn't their own scan.
async function viewInspectionDetail(inspectionId) {
    // Opening any historical record from a plain list (My Scans, manager History, a
    // product's sample table) is never "I just scanned this inside a product" -- only
    // executeScan() sets a return context, so always clear it here.
    currentDetailReturnContext = null;
    try {
        const resp = await apiFetch(`/api/inspections/${inspectionId}`);
        if (!resp.ok) {
            const body = await resp.json().catch(() => ({}));
            throw new Error(body.detail || `Could not load ${inspectionId}.`);
        }
        const data = await resp.json();
        currentInspection = data;
        switchView('detail');
        renderInspectionResults(data);
    } catch (e) {
        alert("Unable to open inspection: " + e.message);
    }
}

// AI extraction-confidence threshold for the officer-facing "Review / Rescan" label
// below -- a UI messaging cue only. It is NOT a legal compliance threshold and never
// feeds into overall_status/rule_results, which come solely from the untouched rules engine.
const LOW_CONFIDENCE_THRESHOLD = 0.85;

function renderDeclRow(idPrefix, fieldObj) {
    const valEl = document.getElementById(`field-${idPrefix}`);
    const statusEl = document.getElementById(`status-${idPrefix}`);
    if (!valEl || !statusEl) return;

    const lowConfidence = fieldObj && fieldObj.value && typeof fieldObj.confidence === "number" && fieldObj.confidence < LOW_CONFIDENCE_THRESHOLD;
    const lowConfBadge = lowConfidence
        ? ` <span class="badge badge-review" title="Extraction confidence ${(fieldObj.confidence * 100).toFixed(0)}%">Low confidence &mdash; Review / Rescan</span>`
        : "";

    if (fieldObj && fieldObj.value) {
        valEl.textContent = fieldObj.value;
        valEl.title = fieldObj.value;
        // Badge first, icon last: the icon then stays flush right in every row, so
        // the status column reads as one straight column down the card.
        if (fieldObj.is_valid) {
            statusEl.innerHTML = `${lowConfBadge}<i class="fa-solid fa-circle-check" title="Declared & Valid"></i>`;
        } else {
            statusEl.innerHTML = `${lowConfBadge}<i class="fa-solid fa-circle-exclamation" title="Requires Review or Non-Standard"></i>`;
        }
    } else {
        valEl.textContent = "Missing / Not Detected";
        valEl.title = "";
        statusEl.innerHTML = `<i class="fa-solid fa-circle-xmark" title="Missing Mandatory Field"></i>`;
    }
}

// Canvas Bounding Box Visualizer
function initCanvasImage(imageUrl) {
    const canvas = document.getElementById("evidence-canvas");
    const ctx = canvas.getContext("2d");
    
    canvasImage = new Image();
    canvasImage.onload = () => {
        // Set canvas resolution to image natural dimensions
        canvas.width = canvasImage.naturalWidth;
        canvas.height = canvasImage.naturalHeight;
        redrawCanvas();
    };
    canvasImage.src = imageUrl;
}

function redrawCanvas(highlightBox = null) {
    const canvas = document.getElementById("evidence-canvas");
    if (!canvas || !canvasImage) return;
    const ctx = canvas.getContext("2d");

    ctx.clearRect(0, 0, canvas.width, canvas.height);
    ctx.drawImage(canvasImage, 0, 0, canvas.width, canvas.height);

    const filtered = allBoxes.filter(b => {
        if (currentInspection && currentInspection.is_dual_panel) {
            const boxPanel = b.panel || 'front';
            if (boxPanel !== activeVisualPanel) return false;
        }
        if (activeBoxFilter === 'all') return true;
        return b.field_tag === activeBoxFilter;
    });

    filtered.forEach(box => {
        const isHighlight = (box === highlightBox);
        drawBoundingBox(ctx, box, isHighlight);
    });
}

function drawBoundingBox(ctx, box, isHighlight) {
    const tag = box.field_tag || 'general';
    let color = "#64748b"; // default slate
    if (tag === 'mrp') color = "#38bdf8"; // sky blue
    else if (tag === 'net_quantity') color = "#10b981"; // emerald
    else if (tag === 'manufacturer') color = "#ec4899"; // pink
    else if (tag === 'date') color = "#f59e0b"; // amber
    else if (tag === 'consumer_care') color = "#8b5cf6"; // purple

    const pts = box.bbox;
    if (!pts || pts.length < 4) return;

    ctx.beginPath();
    ctx.moveTo(pts[0][0], pts[0][1]);
    for (let i = 1; i < pts.length; i++) {
        ctx.lineTo(pts[i][0], pts[i][1]);
    }
    ctx.closePath();

    ctx.lineWidth = isHighlight ? 4 : 2;
    ctx.strokeStyle = color;
    ctx.stroke();

    ctx.fillStyle = isHighlight ? `${color}44` : `${color}18`;
    ctx.fill();

    // Small tag label on top
    if (isHighlight || tag !== 'general') {
        const topX = pts[0][0];
        const topY = Math.max(14, pts[0][1] - 4);
        ctx.fillStyle = color;
        ctx.font = "bold 12px 'JetBrains Mono', monospace";
        ctx.fillText(tag.toUpperCase(), topX, topY);
    }
}

// BBox Filter Buttons
function filterBBoxes(filterTag) {
    activeBoxFilter = filterTag;
    document.querySelectorAll(".filter-pill").forEach(btn => btn.classList.remove("active"));
    event.currentTarget.classList.add("active");
    redrawCanvas();
}

// Canvas Tooltip on Hover
function initCanvasEvents() {
    const canvas = document.getElementById("evidence-canvas");
    const tooltip = document.getElementById("canvas-tooltip");
    if (!canvas || !tooltip) return;

    canvas.addEventListener("mousemove", (e) => {
        if (!canvasImage) return;

        const rect = canvas.getBoundingClientRect();
        const scaleX = canvas.width / rect.width;
        const scaleY = canvas.height / rect.height;

        const mouseX = (e.clientX - rect.left) * scaleX;
        const mouseY = (e.clientY - rect.top) * scaleY;

        let matched = null;
        for (let i = allBoxes.length - 1; i >= 0; i--) {
            const b = allBoxes[i];
            if (currentInspection && currentInspection.is_dual_panel) {
                const boxPanel = b.panel || 'front';
                if (boxPanel !== activeVisualPanel) continue;
            }
            const pts = b.bbox;
            if (pts && pts.length >= 4) {
                const xs = pts.map(p => p[0]);
                const ys = pts.map(p => p[1]);
                const minX = Math.min(...xs);
                const maxX = Math.max(...xs);
                const minY = Math.min(...ys);
                const maxY = Math.max(...ys);

                if (mouseX >= minX && mouseX <= maxX && mouseY >= minY && mouseY <= maxY) {
                    matched = b;
                    break;
                }
            }
        }

        if (matched) {
            tooltip.style.display = "block";
            tooltip.style.left = `${e.clientX - rect.left + 15}px`;
            tooltip.style.top = `${e.clientY - rect.top + 15}px`;
            tooltip.innerHTML = `
                <div><strong>[${(matched.field_tag || 'general').toUpperCase()}]</strong> ${(matched.confidence * 100).toFixed(0)}%</div>
                <div style="color: #cbd5e1; margin-top: 2px;">${matched.text}</div>
            `;
            redrawCanvas(matched);
        } else {
            tooltip.style.display = "none";
            redrawCanvas();
        }
    });

    canvas.addEventListener("mouseleave", () => {
        tooltip.style.display = "none";
        redrawCanvas();
    });
}

// Build the correct report URL for a given inspection + format ('html' | 'pdf' | 'json' | 'csv').
// These open via plain <a href> / window.open() navigation, which cannot attach a
// custom Authorization header -- so the token rides along as a query param instead
// (the backend's get_current_user_flexible accepts either). See backend/auth.py.
function reportUrlFor(inspectionId, format) {
    const tokenQS = currentUser && currentUser.access_token ? `token=${encodeURIComponent(currentUser.access_token)}` : "";
    if (format === "html") return `/api/reports/${inspectionId}/html?${tokenQS}`;
    if (format === "pdf") return `/api/reports/${inspectionId}/pdf?${tokenQS}`;
    if (format === "docx") return `/api/reports/${inspectionId}/docx?${tokenQS}`;
    return `/api/reports/${inspectionId}/export?format=${format}&${tokenQS}`;
}

// Report links carry the JWT in the URL itself (see reportUrlFor) since a plain
// <a>/window.open() navigation can't attach an Authorization header. If the
// session is gone, opening that link anyway just lands the user on a raw
// {"detail":"Missing bearer token..."} JSON page with no obvious way back --
// so catch it here and re-show the login gate instead.
function requireSessionForReport() {
    if (currentUser && currentUser.access_token) return true;
    clearSession();
    currentUser = null;
    showAuthGate("Your session expired. Please sign in again.");
    return false;
}

// Download / Print Compliance Certificate (HTML)
function downloadReport() {
    if (!currentInspection || !requireSessionForReport()) return;
    window.open(reportUrlFor(currentInspection.inspection_id, "html"), "_blank");
}

// Export current inspection in an alternative format: 'pdf' | 'json' | 'csv'
function exportReport(format) {
    if (!currentInspection || !requireSessionForReport()) return;
    window.open(reportUrlFor(currentInspection.inspection_id, format), "_blank");
}

// Officer Review Modal (inspector's own post-scan decision)
function openReviewModal() {
    if (!currentInspection) return;
    if (!currentUser || currentUser.role !== "inspector") {
        alert("Only the officer who scanned this package can record a decision. Controllers use Override Verdict instead.");
        return;
    }
    // Prefill with the signed-in officer's own service number rather than a placeholder.
    const officerIdField = document.getElementById("review-officer-id");
    if (officerIdField) officerIdField.value = currentUser.officer_id || currentUser.user_id || "";
    document.getElementById("review-modal").style.display = "flex";
}
function closeReviewModal() {
    document.getElementById("review-modal").style.display = "none";
}

async function submitReview() {
    if (!currentInspection) return;
    const officerId = document.getElementById("review-officer-id").value;
    const decision = document.getElementById("review-decision").value;
    const notes = document.getElementById("review-notes").value;

    try {
        const resp = await apiFetch(`/api/inspections/${currentInspection.inspection_id}/review`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                officer_id: officerId,
                decision: decision,
                officer_notes: notes
            })
        });
        const res = await resp.json();
        if (!resp.ok) throw new Error(res.detail || "Decision could not be recorded.");
        currentInspection = res.inspection;
        renderInspectionResults(currentInspection);
        closeReviewModal();
        alert("Decision recorded successfully!");
    } catch (e) {
        alert("Decision failed: " + e.message);
    }
}

// ---------- Manager Verdict Override (distinct from the inspector decision above) ----------
function openOverrideModal() {
    if (!currentInspection) return;
    document.getElementById("override-status").value = currentInspection.overall_status;
    document.getElementById("override-reason").value = "";
    document.getElementById("override-modal").style.display = "flex";
}
function closeOverrideModal() {
    document.getElementById("override-modal").style.display = "none";
}

async function submitOverride() {
    if (!currentInspection) return;
    const newStatus = document.getElementById("override-status").value;
    const reason = document.getElementById("override-reason").value.trim();
    if (!reason) {
        alert("A written reason is required to override a verdict.");
        return;
    }
    try {
        const resp = await apiFetch(`/api/inspections/${currentInspection.inspection_id}/override`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ new_status: newStatus, reason: reason })
        });
        const res = await resp.json();
        if (!resp.ok) throw new Error(res.detail || "Override could not be applied.");
        currentInspection = res.inspection;
        renderInspectionResults(currentInspection);
        closeOverrideModal();
        alert("Verdict overridden and logged.");
        if (currentUser && currentUser.role === "manager") loadDashboard();
    } catch (e) {
        alert("Override failed: " + e.message);
    }
}

// Dashboard Data (manager-only -- GET /api/dashboard/metrics 403s for inspectors)
let historyFilterTimer = null;
let myScansFilterTimer = null;

// Shared row markup for both the manager History table and the inspector My Scans
// table. Clicking the id opens the full interactive detail view (server-side BOLA
// check applies for inspectors).
function inspectionRowHTML(h) {
    const badgeClass = h.overall_status === "COMPLIANT" ? "badge-pass" : (h.overall_status === "NON_COMPLIANT" ? "badge-fail" : "badge-review");
    const category = (h.extracted_data && h.extracted_data.category && h.extracted_data.category.value) || "General";
    return `
        <tr>
            <td style="font-family: var(--font-mono); font-weight: 600; color: var(--accent-blue); cursor: pointer;"
                onclick="viewInspectionDetail('${h.inspection_id}')" title="Open full inspection detail">${h.inspection_id}</td>
            <td>${h.timestamp}</td>
            <td style="display: flex; align-items: center; gap: 8px;">
                <img src="${h.thumbnail_url}" style="width: 28px; height: 28px; border-radius: 4px; object-fit: cover;">
                <span>${h.image_filename}</span>
            </td>
            <td>${category}</td>
            <td><span class="badge ${badgeClass}">${h.overall_status}</span></td>
            <td style="font-family: var(--font-mono);">${(h.overall_confidence * 100).toFixed(0)}%</td>
            <td>${h.violations.length > 0 ? `<span style="color: #f87171; font-weight: 600;">${h.violations.length} Found</span>` : `<span style="color: #10b981;">None</span>`}</td>
            <td>
                <div class="report-links">
                    <a href="${reportUrlFor(h.inspection_id, 'html')}" target="_blank" title="Printable certificate (HTML)"><i class="fa-solid fa-file-lines"></i></a>
                    <a href="${reportUrlFor(h.inspection_id, 'pdf')}" target="_blank" title="Download PDF"><i class="fa-solid fa-file-pdf"></i></a>
                    <a href="${reportUrlFor(h.inspection_id, 'docx')}" target="_blank" title="Download Word (Editable)"><i class="fa-solid fa-file-word"></i></a>
                    <a href="${reportUrlFor(h.inspection_id, 'json')}" target="_blank" title="Export JSON"><i class="fa-solid fa-file-code"></i></a>
                    <a href="${reportUrlFor(h.inspection_id, 'csv')}" target="_blank" title="Export CSV"><i class="fa-solid fa-file-csv"></i></a>
                </div>
            </td>
        </tr>
    `;
}

async function loadDashboard() {
    if (!currentUser || currentUser.role !== "manager") return; // oversight dashboard is manager-only
    try {
        const mResp = await apiFetch("/api/dashboard/metrics");
        const metrics = await mResp.json();
        renderBento(metrics);
        await loadInspectorFilterOptions();
        await loadHistory();
    } catch (err) {
        console.error("Dashboard error:", err);
    }
}

// Populates the manager-only "All inspectors" dropdown from the full (unfiltered) list.
async function loadInspectorFilterOptions() {
    const sel = document.getElementById("history-inspector-filter");
    if (!sel || !currentUser || currentUser.role !== "manager") return;
    try {
        const rows = await (await apiFetch("/api/inspections?limit=1000")).json();
        const ids = [...new Set(rows.map(r => r.inspector_id).filter(Boolean))].sort();
        const current = sel.value;
        sel.innerHTML = `<option value="">All officers</option>` +
            ids.map(id => `<option value="${bentoEsc(id)}">${bentoEsc(id)}</option>`).join("");
        if (ids.includes(current)) sel.value = current;
    } catch (e) { /* leave the dropdown as-is */ }
}

// Debounced re-fetch of the history table when search / status / inspector filters change
function applyHistoryFilter() {
    if (historyFilterTimer) clearTimeout(historyFilterTimer);
    historyFilterTimer = setTimeout(loadHistory, 250);
}

async function loadHistory() {
    const historyBody = document.getElementById("history-table-body");
    if (!historyBody || !currentUser || currentUser.role !== "manager") return;

    const searchEl = document.getElementById("history-search-input");
    const statusEl = document.getElementById("history-status-filter");
    const inspectorEl = document.getElementById("history-inspector-filter");
    const search = searchEl ? searchEl.value.trim() : "";
    const status = statusEl ? statusEl.value : "";
    const inspectorId = inspectorEl ? inspectorEl.value : "";

    const params = new URLSearchParams({ limit: "50" });
    if (search) params.set("search", search);
    if (status) params.set("status", status);
    if (inspectorId) params.set("inspector_id", inspectorId);

    try {
        const hResp = await apiFetch(`/api/inspections?${params.toString()}`);
        const history = await hResp.json();

        if (!history.length) {
            const msg = (search || status || inspectorId)
                ? "No inspections match the current filters."
                : "No inspections recorded yet.";
            historyBody.innerHTML = `<tr><td colspan="8" style="text-align: center; color: #6b7280; padding: 24px;">${msg}</td></tr>`;
            return;
        }

        historyBody.innerHTML = history.map(inspectionRowHTML).join("");
    } catch (err) {
        console.error("History load error:", err);
        historyBody.innerHTML = `<tr><td colspan="8" style="text-align: center; color: #f87171; padding: 24px;">Failed to load inspection history.</td></tr>`;
    }
}

// ---------- My Scans (inspector-only own-scan history) ----------
function applyMyScansFilter() {
    if (myScansFilterTimer) clearTimeout(myScansFilterTimer);
    myScansFilterTimer = setTimeout(loadMyScans, 250);
}

async function loadMyScans() {
    const body = document.getElementById("myscans-table-body");
    if (!body || !currentUser || currentUser.role !== "inspector") return;

    const searchEl = document.getElementById("myscans-search-input");
    const statusEl = document.getElementById("myscans-status-filter");
    const search = searchEl ? searchEl.value.trim() : "";
    const status = statusEl ? statusEl.value : "";

    // No inspector_id param needed -- the server forces it to the caller's own id.
    const params = new URLSearchParams({ limit: "50" });
    if (search) params.set("search", search);
    if (status) params.set("status", status);

    try {
        const resp = await apiFetch(`/api/inspections?${params.toString()}`);
        const rows = await resp.json();
        if (!rows.length) {
            const msg = (search || status) ? "No scans match the current filters." : "No scans yet — use the AI Scanner tab.";
            body.innerHTML = `<tr><td colspan="8" style="text-align: center; color: #6b7280; padding: 24px;">${msg}</td></tr>`;
            return;
        }
        body.innerHTML = rows.map(inspectionRowHTML).join("");
    } catch (e) {
        console.error("My Scans load error:", e);
        body.innerHTML = `<tr><td colspan="8" style="text-align: center; color: #f87171; padding: 24px;">Failed to load your scans.</td></tr>`;
    }
}

// ---------- Bento dashboard rendering ----------

function bentoSet(id, value) { const e = document.getElementById(id); if (e) e.textContent = value; }
function bentoHTML(id, value) { const e = document.getElementById(id); if (e) e.innerHTML = value; }
function bentoEsc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, c =>
        ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function bentoPct(v) { return (v == null || isNaN(v)) ? "--%" : `${Math.round(v)}%`; }
function bentoClamp(v) { v = Number(v) || 0; return Math.max(0, Math.min(100, v)); }

function bentoRelTime(ts) {
    if (!ts) return "";
    const d = new Date(String(ts).replace(" ", "T"));
    if (isNaN(d.getTime())) return String(ts);
    let s = Math.floor((Date.now() - d.getTime()) / 1000);
    if (s < 45) return "just now";
    const m = Math.floor(s / 60);
    if (m < 60) return `${m} min ago`;
    const h = Math.floor(m / 60);
    if (h < 24) return `${h} hr ago`;
    const days = Math.floor(h / 24);
    return `${days} day${days > 1 ? "s" : ""} ago`;
}

function bentoSparkline(values) {
    const w = 160, h = 44, pad = 5;
    const vals = (values && values.length) ? values : [0, 0];
    const max = Math.max.apply(null, vals);
    const min = Math.min.apply(null, vals);
    const span = (max - min) || 1;
    const step = w / (vals.length - 1 || 1);
    const pts = vals.map((v, i) => {
        const x = i * step;
        const y = h - pad - ((v - min) / span) * (h - pad * 2);
        return `${x.toFixed(1)},${y.toFixed(1)}`;
    }).join(" ");
    return `<svg width="100%" height="${h}" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none">
        <polyline class="spark-line" points="${pts}"/></svg>`;
}

function bentoMeter(pct) {
    return `<div class="mini-meter"><span style="width:${bentoClamp(pct)}%"></span></div>`;
}

function bentoGauge(pct, caption) {
    const p = bentoClamp(pct);
    const arcLen = Math.PI * 90;
    const off = arcLen * (1 - p / 100);
    return `<svg width="252" height="150" viewBox="0 0 220 126" preserveAspectRatio="xMidYMid meet">
        <path d="M 20 110 A 90 90 0 0 1 200 110" fill="none" stroke="#33334E" stroke-width="15" stroke-linecap="round"/>
        <path d="M 20 110 A 90 90 0 0 1 200 110" fill="none" stroke="#FFD700" stroke-width="15" stroke-linecap="round"
              stroke-dasharray="${arcLen.toFixed(1)}" stroke-dashoffset="${off.toFixed(1)}"/>
        <text x="110" y="100" text-anchor="middle" fill="#8A8AA3" font-size="11" font-family="Outfit, sans-serif">${bentoEsc(caption || "")}</text>
    </svg>`;
}

function bentoDonut(pct) {
    const p = bentoClamp(pct);
    const r = 26;
    const circ = 2 * Math.PI * r;
    const off = circ * (1 - p / 100);
    return `<svg width="74" height="74" viewBox="0 0 64 64">
        <circle cx="32" cy="32" r="${r}" fill="none" stroke="#2A2A42" stroke-width="7"/>
        <circle cx="32" cy="32" r="${r}" fill="none" stroke="#FFD700" stroke-width="7" stroke-linecap="round"
                stroke-dasharray="${circ.toFixed(1)}" stroke-dashoffset="${off.toFixed(1)}" transform="rotate(-90 32 32)"/>
        <text x="32" y="36" text-anchor="middle" fill="#FFFFFF" font-size="13" font-family="Outfit, sans-serif">${Math.round(p)}%</text>
    </svg>`;
}

function bentoTrendPill(id, delta, caption) {
    const e = document.getElementById(id);
    if (!e) return;
    if (!delta) {
        e.className = "bento-trend muted-trend";
        e.textContent = caption || "";
        return;
    }
    const up = delta > 0;
    e.className = "bento-trend " + (up ? "up" : "down");
    e.innerHTML = `${up ? "▲" : "▼"} ${Math.abs(delta)}${caption ? " " + caption : ""}`;
}

function renderBento(m) {
    const trend = m.trend_14d || [];
    const totals = trend.map(d => d.total || 0);
    const reviews = trend.map(d => d.review_required || 0);

    // Hero — compliance rate + gauge + verdict legend + volume strip
    bentoSet("bento-compliance-rate", bentoPct(m.compliance_rate));
    bentoHTML("bento-compliance-gauge",
        bentoGauge(m.compliance_rate, `${m.total_inspections || 0} inspections logged`) +
        `<div class="hero-legend">
            <div><span class="hl-dot pass"></span>${m.compliant_count || 0} compliant</div>
            <div><span class="hl-dot review"></span>${m.review_required_count || 0} in review</div>
            <div><span class="hl-dot fail"></span>${m.non_compliant_count || 0} non-compliant</div>
        </div>
        <div class="hero-spark">
            <div class="hero-spark-label">Inspection volume &middot; last 14 days</div>
            ${bentoSparkline(totals)}
        </div>`);
    const wow = m.wow_compliance_delta;
    const wEl = document.getElementById("bento-compliance-trend");
    if (wEl) {
        if (wow === null || wow === undefined) {
            wEl.className = "bento-trend muted-trend";
            wEl.textContent = "No week-over-week comparison yet";
        } else {
            wEl.className = "bento-trend " + (wow >= 0 ? "up" : "down");
            wEl.innerHTML = `${wow >= 0 ? "▲" : "▼"} ${Math.abs(wow)}% vs previous week`;
        }
    }

    // Total inspections + volume sparkline
    bentoSet("bento-total", m.total_inspections != null ? m.total_inspections : "--");
    bentoHTML("bento-total-spark", bentoSparkline(totals));
    const last = totals[totals.length - 1] || 0;
    const wk = totals.slice(7).reduce((a, b) => a + b, 0);
    const teEl = document.getElementById("bento-total-trend");
    if (teEl) {
        teEl.className = "bento-trend muted-trend";
        teEl.textContent = `${last} today · ${wk} in the last 7 days`;
    }

    // Pending review donut
    bentoSet("bento-review", m.review_required_count != null ? m.review_required_count : "--");
    const rPct = m.total_inspections ? (m.review_required_count / m.total_inspections * 100) : 0;
    bentoHTML("bento-review-donut", bentoDonut(rPct));

    // Avg AI confidence meter
    bentoSet("bento-confidence", bentoPct(m.avg_confidence));
    bentoHTML("bento-confidence-spark", bentoMeter(m.avg_confidence));

    // Category distribution
    const cats = Object.keys(m.category_distribution || {})
        .map(k => [k, m.category_distribution[k]])
        .sort((a, b) => b[1] - a[1]).slice(0, 4);
    const catMax = Math.max.apply(null, cats.map(c => c[1]).concat([1]));
    bentoHTML("bento-categories", cats.length ? cats.map(c => `
        <div class="seg-row">
            <div class="seg-label"><span class="seg-name">${bentoEsc(c[0])}</span><span>${c[1]}</span></div>
            <div class="seg-track"><div class="seg-fill" style="width:${Math.round(c[1] / catMax * 100)}%"></div></div>
        </div>`).join("") : `<div class="bento-empty">No category data yet.</div>`);

    // Hold rate meter
    bentoSet("bento-hold", bentoPct(m.hold_rate));
    bentoHTML("bento-hold-spark", reviews.some(v => v > 0)
        ? bentoSparkline(reviews)
        : `<div style="width:100%">
             <div class="bento-empty" style="padding:0 0 8px">Nothing currently on hold</div>
             ${bentoMeter(m.hold_rate)}
           </div>`);

    // Field detection rates
    bentoHTML("bento-fields", (m.field_detection || []).map(f => `
        <div class="fr">
            <span class="fr-name">${bentoEsc(f.label)}</span>
            <span class="fr-val">${f.pct}% <span class="${f.pct >= 70 ? "fr-up" : "fr-down"}">${f.pct >= 70 ? "▲" : "▼"}</span></span>
        </div>`).join("") || `<div class="bento-empty">No field data yet.</div>`);

    // Violations logged + verdict split
    bentoSet("bento-violation-total", m.total_violations_found != null ? m.total_violations_found : "--");
    const c = m.compliant_count || 0, rv = m.review_required_count || 0, nc = m.non_compliant_count || 0;
    const vtot = Math.max(1, c + rv + nc);
    bentoHTML("bento-verdict-split", `<div style="width:100%">
        <div class="verdict-split">
            <span class="v-pass" style="width:${c / vtot * 100}%"></span>
            <span class="v-review" style="width:${rv / vtot * 100}%"></span>
            <span class="v-fail" style="width:${nc / vtot * 100}%"></span>
        </div>
        <div class="verdict-legend">
            <span>${c} compliant</span><span>${rv} review</span><span>${nc} non-compliant</span>
        </div>
    </div>`);

    // Flagged violations list + bars
    const vt = m.violation_types || [];
    bentoHTML("bento-violations", vt.length ? vt.slice(0, 4).map(v => `
        <div class="lr">
            <div class="lr-left"><div class="lr-ico">⚠</div><span class="lr-name">${bentoEsc(v.label)}</span></div>
            <span class="count-pill">${v.count}</span>
        </div>`).join("") : `<div class="bento-empty">No violations flagged.</div>`);
    const vMax = Math.max.apply(null, vt.map(v => v.count).concat([1]));
    bentoHTML("bento-violation-bars", vt.length ? vt.slice(0, 6).map(v => `
        <div class="br">
            <span class="br-label">${bentoEsc(v.label)}</span>
            <div class="br-track"><div class="br-fill" style="width:${Math.round(v.count / vMax * 100)}%"></div></div>
            <span class="br-val">${v.count}</span>
        </div>`).join("") : `<div class="bento-empty">No violations recorded.</div>`);

    // This week (last 7 trend days)
    const wkScans = totals.slice(7).reduce((a, b) => a + b, 0);
    const wkComp = trend.slice(7).reduce((a, d) => a + (d.compliant || 0), 0);
    bentoSet("bento-week-scans", wkScans);
    bentoHTML("bento-week-meter", `<div style="width:100%">
        <div class="verdict-legend" style="margin:0 0 6px"><span>${wkComp} compliant</span><span>${Math.max(0, wkScans - wkComp)} other</span></div>
        ${bentoMeter(wkScans ? wkComp / wkScans * 100 : 0)}
    </div>`);

    // Most common violation
    const topViol = (m.violation_types || [])[0];
    bentoHTML("bento-topviol", topViol
        ? `<div class="tv-count">${topViol.count}</div><div class="tv-label">${bentoEsc(topViol.label)}</div>`
        : `<div class="bento-empty">No violations recorded.</div>`);

    // Activity calendar
    bentoHTML("bento-calendar", trend.length ? trend.map((d, i) => {
        const cls = (i === trend.length - 1) ? "cal-cell today" : (d.total > 0 ? "cal-cell has-scans" : "cal-cell");
        return `<div class="${cls}" title="${d.date} — ${d.total} scan(s)">${parseInt(d.date.slice(8), 10)}</div>`;
    }).join("") : `<div class="bento-empty">No activity.</div>`);

    // Recent inspections timeline
    loadRecentTimeline();
}

async function loadRecentTimeline() {
    const el = document.getElementById("bento-timeline");
    if (!el) return;
    try {
        const rows = await (await apiFetch("/api/inspections?limit=9")).json();
        if (!rows.length) {
            el.innerHTML = `<div class="bento-empty">No inspections yet.</div>`;
            return;
        }
        el.innerHTML = rows.map(h => {
            const st = h.overall_status;
            const dotCls = st === "COMPLIANT" ? "pass" : (st === "NON_COMPLIANT" ? "fail" : "review");
            const verdict = st === "COMPLIANT" ? "PASS" : (st === "NON_COMPLIANT" ? "FAIL" : "REVIEW");
            const nm = (h.extracted_data && h.extracted_data.product_name && h.extracted_data.product_name.value)
                || h.image_filename || h.inspection_id;
            return `<div class="ti">
                <div class="ti-dot ${dotCls}"></div>
                <div>
                    <div class="ti-name">${bentoEsc(String(nm).slice(0, 40))} &mdash; ${verdict}</div>
                    <div class="ti-meta">${bentoRelTime(h.timestamp)}</div>
                </div>
            </div>`;
        }).join("");
    } catch (e) {
        el.innerHTML = `<div class="bento-empty">Failed to load recent inspections.</div>`;
    }
}

// Gallery Grid View
function renderGallery(items) {
    const grid = document.getElementById("gallery-grid-container");
    if (!grid) return;

    grid.innerHTML = items.map(it => `
        <div class="gallery-card" onclick="inspectFromGallery('${it.filename}')">
            <img src="${it.thumbnail_url}" class="gallery-thumb" alt="${it.name}">
            <div class="gallery-title">${it.name}</div>
            <div class="gallery-meta">${it.category}</div>
            <button class="btn btn-primary btn-sm" style="width: 100%; justify-content: center;">
                <i class="fa-solid fa-magnifying-glass"></i> Inspect Now
            </button>
        </div>
    `).join("");
}

function filterGallery() {
    const q = document.getElementById("gallery-search-input").value.toLowerCase();
    const filtered = catalogItems.filter(it => 
        it.name.toLowerCase().includes(q) || it.category.toLowerCase().includes(q) || it.filename.toLowerCase().includes(q)
    );
    renderGallery(filtered);
}

function inspectFromGallery(filename) {
    switchView('scanner');
    const chip = Array.from(document.querySelectorAll(".dataset-chip")).find(c => c.textContent.includes(filename.replace(/\..+$/, '')));
    inspectDatasetProduct(filename, chip);
}

// Rules Configuration Modal
async function openRulesModal() {
    closeMobileNav();
    const canEdit = !!(currentUser && currentUser.role === "manager"); // PUT /api/rules/{id} is manager-only
    try {
        const resp = await apiFetch("/api/rules");
        const rules = await resp.json();
        const container = document.getElementById("rules-config-list");
        container.innerHTML = rules.map(r => `
            <div class="rule-config-item">
                <div>
                    <div class="rule-info-title">${r.title} (${r.rule_id})</div>
                    <div class="rule-info-sub">${r.legal_reference} &bull; Severity: <strong>${r.severity}</strong></div>
                    <div style="font-size: 11px; color: #94a3b8; margin-top: 4px;">${r.description || ""}</div>
                </div>
                <div style="display: flex; align-items: center; gap: 10px;">
                    <label style="font-size: 12px; color: #cbd5e1;">Enabled</label>
                    <input type="checkbox" ${r.enabled ? 'checked' : ''} ${canEdit ? '' : 'disabled title="View-only: only managers can change statutory rules"'}
                        onchange="${canEdit ? `toggleRule('${r.rule_id}', this.checked, '${r.severity}', ${r.required})` : ''}">
                </div>
            </div>
        `).join("");
        document.getElementById("rules-modal").style.display = "flex";
    } catch (err) {
        alert("Error loading rules: " + err.message);
    }
}

function closeRulesModal() {
    document.getElementById("rules-modal").style.display = "none";
}

async function toggleRule(ruleId, enabled, severity, required) {
    try {
        await apiFetch(`/api/rules/${ruleId}`, {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                rule_id: ruleId,
                enabled: enabled,
                severity: severity,
                required: Boolean(required)
            })
        });
    } catch (e) {
        console.error("Error updating rule:", e);
    }
}

// ================= Officer Inspection-Session Workflow =================
// Everything below is additive on top of the existing ad-hoc scan flow above.
// Sample scans still go through the exact same executeScan()/renderInspectionResults()
// path; this section only adds the session/product layer around it.

// Back button on view-detail: returns to the product's workspace if this sample was
// just scanned inside one, else falls back to today's plain behavior.
function goBackFromDetail() {
    if (currentDetailReturnContext) {
        openSessionWorkspace(currentDetailReturnContext.session_id);
    } else {
        switchView(currentUser && currentUser.role === 'manager' ? 'dashboard' : 'myscans');
    }
}

// Back button on view-session-workspace: active sessions came from Active Inspections,
// closed ones from Reports.
function goBackFromWorkspace() {
    if (currentSession && currentSession.status === 'CLOSED') {
        switchView('reports');
    } else {
        switchView('active-inspections');
    }
}

async function loadOfficerDashboard() {
    if (!currentUser || currentUser.role !== 'inspector') return;

    document.getElementById("officer-name-display").textContent = currentUser.name || "--";
    document.getElementById("officer-id-display").textContent = currentUser.officer_id || currentUser.user_id || "--";
    const roleEl = document.getElementById("officer-role-display");
    if (roleEl) roleEl.textContent = roleLabel(currentUser.role);
    const jurisEl = document.getElementById("officer-jurisdiction-display");
    const setupRow = document.getElementById("jurisdiction-setup-row");
    if (currentUser.jurisdiction) {
        jurisEl.textContent = currentUser.jurisdiction;
        if (setupRow) setupRow.style.display = "none";
    } else {
        jurisEl.textContent = "Not set";
        if (setupRow) setupRow.style.display = "block";
    }

    try {
        const resp = await apiFetch("/api/officer/metrics");
        const m = await resp.json();
        document.getElementById("officer-metric-active").textContent = m.active_sessions;
        document.getElementById("officer-metric-completed").textContent = m.completed_inspections;
        document.getElementById("officer-metric-pending").textContent = m.pending_reviews;
        document.getElementById("officer-metric-noncompliant").textContent = m.non_compliance_findings;
    } catch (e) {
        console.error("Officer dashboard error:", e);
    }

    // Quick-resume list of this officer's still-open inspections.
    const openBox = document.getElementById("officer-open-sessions");
    if (!openBox) return;
    try {
        const resp = await apiFetch("/api/sessions?status=ACTIVE&limit=5");
        const sessions = await resp.json();
        if (!sessions.length) {
            openBox.innerHTML = `
                <div class="empty-note">
                    <i class="fa-solid fa-clipboard-check"></i>
                    No inspections in progress. Start one to begin recording products and samples.
                </div>`;
            return;
        }
        openBox.innerHTML = sessions.map(s => `
            <div class="open-session-row">
                <div class="open-session-main">
                    <div class="open-session-id">${s.id}</div>
                    <div class="open-session-meta">
                        <span><i class="fa-solid fa-shop"></i> ${s.location}</span>
                        <span><i class="fa-solid fa-tag"></i> ${s.inspection_type}</span>
                        <span><i class="fa-solid fa-box"></i> ${s.product_count} product${s.product_count === 1 ? '' : 's'}</span>
                        <span><i class="fa-solid fa-clock"></i> ${s.created_at}</span>
                    </div>
                </div>
                <button class="btn btn-primary btn-sm" onclick="openSessionWorkspace('${s.id}')">
                    <i class="fa-solid fa-arrow-right-to-bracket"></i> Resume
                </button>
            </div>
        `).join("");
    } catch (e) {
        console.error("Open sessions load error:", e);
        openBox.innerHTML = `<div class="empty-note">Could not load your open inspections.</div>`;
    }
}

async function submitJurisdiction() {
    const input = document.getElementById("jurisdiction-input");
    const jurisdiction = (input.value || "").trim();
    if (!jurisdiction) {
        alert("Enter a jurisdiction.");
        return;
    }
    try {
        const resp = await apiFetch("/api/profile/jurisdiction", {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ jurisdiction })
        });
        const body = await resp.json().catch(() => ({}));
        if (!resp.ok) throw new Error(body.detail || "Could not save jurisdiction.");
        currentUser.jurisdiction = jurisdiction;
        saveSession(currentUser);
        loadOfficerDashboard();
    } catch (e) {
        alert("Failed to save jurisdiction: " + e.message);
    }
}

function prepareNewInspectionForm() {
    const now = new Date();
    document.getElementById("ni-datetime").textContent = now.toLocaleString(undefined, {
        day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit",
    });
    document.getElementById("ni-officer").textContent = (currentUser && currentUser.name) || "--";
    document.getElementById("ni-officer-id").textContent =
        (currentUser && (currentUser.officer_id || currentUser.user_id)) || "--";
    document.getElementById("ni-jurisdiction").textContent = (currentUser && currentUser.jurisdiction) || "Not set";
    document.getElementById("ni-location").value = "";
    document.getElementById("ni-inspection-type").value = "Routine";
    document.getElementById("ni-notes").value = "";
    const errEl = document.getElementById("ni-error");
    if (errEl) errEl.style.display = "none";
}

async function startNewInspection() {
    const errEl = document.getElementById("ni-error");
    const location_ = document.getElementById("ni-location").value.trim();
    const inspectionType = document.getElementById("ni-inspection-type").value;
    const notes = document.getElementById("ni-notes").value.trim();

    if (!location_) {
        errEl.textContent = "Inspection location is required.";
        errEl.style.display = "block";
        return;
    }
    if (!currentUser.jurisdiction) {
        errEl.textContent = "Set your jurisdiction on the Dashboard before starting an inspection.";
        errEl.style.display = "block";
        return;
    }

    try {
        const resp = await apiFetch("/api/sessions", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ location: location_, inspection_type: inspectionType, notes })
        });
        const data = await resp.json();
        if (!resp.ok) throw new Error(data.detail || "Could not start inspection.");
        currentSession = data;
        switchView('session-workspace');
        renderSessionWorkspace(data);
    } catch (e) {
        errEl.textContent = e.message;
        errEl.style.display = "block";
    }
}

async function loadActiveInspections() {
    const body = document.getElementById("active-inspections-table-body");
    if (!body) return;
    try {
        const resp = await apiFetch("/api/sessions?status=ACTIVE");
        const rows = await resp.json();
        if (!rows.length) {
            body.innerHTML = `<tr><td colspan="6" style="text-align: center; color: #6b7280; padding: 24px;">No active inspections &mdash; start one from the Dashboard.</td></tr>`;
            return;
        }
        body.innerHTML = rows.map(sessionRowHTML).join("");
    } catch (e) {
        console.error("Active inspections load error:", e);
        body.innerHTML = `<tr><td colspan="6" style="text-align: center; color: #f87171; padding: 24px;">Failed to load active inspections.</td></tr>`;
    }
}

async function loadReportsSessions() {
    const body = document.getElementById("reports-table-body");
    if (!body) return;
    try {
        const resp = await apiFetch("/api/sessions");
        const rows = await resp.json();
        if (!rows.length) {
            body.innerHTML = `<tr><td colspan="6" style="text-align: center; color: #6b7280; padding: 24px;">No inspections yet.</td></tr>`;
            return;
        }
        body.innerHTML = rows.map(sessionRowHTML).join("");
    } catch (e) {
        console.error("Reports load error:", e);
        body.innerHTML = `<tr><td colspan="6" style="text-align: center; color: #f87171; padding: 24px;">Failed to load inspections.</td></tr>`;
    }
}

// ---------- History: level 1 (session-wise) / level 2 (product-wise) ----------
let historyMode = "sessions";

function switchHistoryMode(mode) {
    historyMode = mode;
    document.getElementById("histmode-sessions").classList.toggle("active", mode === "sessions");
    document.getElementById("histmode-scans").classList.toggle("active", mode === "scans");
    document.getElementById("history-sessions-pane").style.display = mode === "sessions" ? "block" : "none";
    document.getElementById("history-scans-pane").style.display = mode === "scans" ? "block" : "none";
    loadHistory2();
}

// Entry point for the History tab -- loads whichever level is showing.
function loadHistory2() {
    if (historyMode === "sessions") loadSessionHistory();
    else loadMyScans();
}

async function loadSessionHistory() {
    const body = document.getElementById("history-sessions-body");
    if (!body) return;
    const statusEl = document.getElementById("history-session-status");
    const status = statusEl ? statusEl.value : "";
    const qs = status ? `?status=${encodeURIComponent(status)}` : "";
    try {
        const resp = await apiFetch(`/api/sessions${qs}`);
        const rows = await resp.json();
        if (!rows.length) {
            body.innerHTML = `<tr><td colspan="8" style="text-align:center;color:#6b7280;padding:24px;">No inspection sessions yet.</td></tr>`;
            return;
        }
        body.innerHTML = rows.map(s => {
            const badgeClass = s.status === 'CLOSED' ? 'badge-pass' : 'badge-review';
            return `
                <tr>
                    <td style="font-family: var(--font-mono); font-weight: 600; color: var(--accent-blue); cursor: pointer;"
                        onclick="openSessionWorkspace('${s.id}')" title="Open product-wise history for this inspection">${s.id}</td>
                    <td>${s.created_at}</td>
                    <td>${s.location}</td>
                    <td>${s.inspection_type}</td>
                    <td>${s.product_count}</td>
                    <td><span class="badge badge-pass ${s.compliant_products ? '' : 'is-dim'}">${s.compliant_products}</span></td>
                    <td><span class="badge badge-fail ${s.non_compliant_products ? '' : 'is-dim'}">${s.non_compliant_products}</span></td>
                    <td><span class="badge ${badgeClass}">${s.status}</span></td>
                </tr>`;
        }).join("");
    } catch (e) {
        console.error("Session history error:", e);
        body.innerHTML = `<tr><td colspan="8" style="text-align:center;color:#f87171;padding:24px;">Failed to load inspection sessions.</td></tr>`;
    }
}

// Product-wise filter inside a session (level 2).
function applyProductFilter() {
    if (currentSession) renderSessionWorkspace(currentSession);
}

const PRODUCT_STATUS_META = {
    COMPLIANT:      { label: "Compliant",      cls: "badge-pass" },
    NON_COMPLIANT:  { label: "Non-Compliant",  cls: "badge-fail" },
    REVIEW_REQUIRED:{ label: "Review Required", cls: "badge-review" },
    NOT_INSPECTED:  { label: "Not Inspected",  cls: "badge-review" },
};

// Shared row renderer for the Active Inspections / Reports tables -- mirrors
// inspectionRowHTML()'s pattern above, one row per inspection SESSION (not sample).
function sessionRowHTML(s) {
    const badgeClass = s.status === 'CLOSED' ? 'badge-pass' : 'badge-review';
    return `
        <tr>
            <td style="font-family: var(--font-mono); font-weight: 600; color: var(--accent-blue); cursor: pointer;"
                onclick="openSessionWorkspace('${s.id}')" title="Open this inspection">${s.id}</td>
            <td>${s.created_at}</td>
            <td>${s.location}</td>
            <td>${s.inspection_type}</td>
            <td>${s.product_count}</td>
            <td><span class="badge ${badgeClass}">${s.status}</span></td>
        </tr>
    `;
}

async function openSessionWorkspace(sessionId) {
    try {
        const resp = await apiFetch(`/api/sessions/${sessionId}/summary`);
        if (!resp.ok) {
            const body = await resp.json().catch(() => ({}));
            throw new Error(body.detail || `Could not load ${sessionId}.`);
        }
        const data = await resp.json();
        currentSession = data;
        switchView('session-workspace');
        renderSessionWorkspace(data);
    } catch (e) {
        alert("Unable to open inspection: " + e.message);
    }
}

function renderSessionWorkspace(summary) {
    document.getElementById("workspace-session-id").textContent = summary.id;
    document.getElementById("workspace-officer").textContent = `${summary.officer_name} (${summary.officer_id})`;
    document.getElementById("workspace-jurisdiction").textContent = summary.jurisdiction;
    document.getElementById("workspace-location").textContent = summary.location;
    document.getElementById("workspace-type").textContent = summary.inspection_type;
    document.getElementById("workspace-date").textContent = summary.created_at;

    const notesEl = document.getElementById("workspace-notes");
    if (summary.notes) {
        notesEl.style.display = "block";
        notesEl.textContent = summary.notes;
    } else {
        notesEl.style.display = "none";
    }

    const statusBadge = document.getElementById("workspace-status-badge");
    statusBadge.textContent = summary.status;
    statusBadge.className = `badge ${summary.status === 'CLOSED' ? 'badge-pass' : 'badge-review'}`;

    const isActive = summary.status === 'ACTIVE';
    const isSeized = !!summary.seized;
    const addBtn = document.getElementById("btn-add-product");
    const closeBtn = document.getElementById("btn-close-session");
    if (addBtn) addBtn.style.display = isActive ? "inline-flex" : "none";
    if (closeBtn) closeBtn.style.display = isActive ? "inline-flex" : "none";

    // Seizure is offered once there is something to seize: at least one product
    // with a confirmed non-compliant sample, and not already seized.
    const seizeBtn = document.getElementById("btn-seize-session");
    if (seizeBtn) {
        const seizable = !isSeized && (summary.non_compliant_products || 0) > 0;
        seizeBtn.style.display = seizable ? "inline-flex" : "none";
    }
    if (isSeized) {
        statusBadge.textContent = `SEIZED • ${summary.status}`;
        statusBadge.className = "badge badge-fail";
    }
    loadSeizureReports(summary.id);

    const allProducts = summary.products || [];
    const t = summary.totals || {};

    // Level-2 history filter: narrow the product list by compliance status.
    const filterEl = document.getElementById("product-status-filter");
    const statusFilter = filterEl ? filterEl.value : "";
    const products = statusFilter
        ? allProducts.filter(p => p.product_status === statusFilter)
        : allProducts;

    const countEl = document.getElementById("workspace-products-count");
    if (countEl) {
        if (!allProducts.length) {
            countEl.textContent = "";
        } else if (statusFilter) {
            countEl.textContent = `${products.length} of ${allProducts.length} products shown`;
        } else {
            countEl.textContent =
                `${allProducts.length} product${allProducts.length > 1 ? 's' : ''} • `
                + `${summary.compliant_products || 0} compliant, ${summary.non_compliant_products || 0} non-compliant • `
                + `${t.samples_inspected || 0} of ${t.sample_target || 0} samples inspected`;
        }
    }

    const container = document.getElementById("session-products-container");
    if (allProducts.length && !products.length) {
        const meta = PRODUCT_STATUS_META[statusFilter];
        container.innerHTML = `
            <div class="glass-card empty-state-card">
                <i class="fa-solid fa-filter-circle-xmark"></i>
                <div class="empty-title">No ${meta ? meta.label.toLowerCase() : ''} products</div>
                <div class="empty-sub">No product in this inspection currently has that status.</div>
            </div>`;
        return;
    }
    if (!products.length) {
        container.innerHTML = `
            <div class="glass-card empty-state-card">
                <i class="fa-solid fa-box-open"></i>
                <div class="empty-title">No products added yet</div>
                <div class="empty-sub">${isActive
                    ? 'Use &ldquo;Add Product&rdquo; above to record the first commodity for this inspection.'
                    : 'This inspection was closed without any products.'}</div>
            </div>`;
        return;
    }

    container.innerHTML = products.map(p => {
        const pct = p.sample_target > 0
            ? Math.min(100, Math.round((p.samples_inspected / p.sample_target) * 100))
            : 0;
        const meta = [
            p.manufacturer ? `<span><i class="fa-solid fa-industry"></i> ${p.manufacturer}</span>` : '',
            p.category ? `<span><i class="fa-solid fa-tag"></i> ${p.category}</span>` : '',
            p.location ? `<span><i class="fa-solid fa-location-dot"></i> ${p.location}</span>` : '',
        ].filter(Boolean).join('');

        const samplesBlock = (p.scans && p.scans.length) ? `
            <div class="samples-block">
                <div class="samples-block-title">Inspected Samples (${p.scans.length})</div>
                <div class="history-table-wrapper">
                    <table class="history-table">
                        <thead>
                            <tr>
                                <th>Inspection ID</th><th>Timestamp</th><th>Product / Image</th><th>Category</th>
                                <th>Compliance Status</th><th>Confidence</th><th>Violations</th><th>Reports</th>
                            </tr>
                        </thead>
                        <tbody>${p.scans.map(inspectionRowHTML).join("")}</tbody>
                    </table>
                </div>
            </div>` : `
            <div class="empty-note">
                <i class="fa-solid fa-camera"></i>
                No samples scanned yet for this product.${isActive ? ' Use &ldquo;Scan Sample&rdquo; to inspect the first package.' : ''}
            </div>`;

        return `
            <div class="glass-card product-card">
                <div class="product-card-header">
                    <div>
                        <div class="product-title">
                            ${p.product_name}
                            <span class="badge ${(PRODUCT_STATUS_META[p.product_status] || {}).cls || 'badge-review'}">${(PRODUCT_STATUS_META[p.product_status] || {}).label || p.product_status}</span>
                        </div>
                        <div class="product-meta">${meta}</div>
                    </div>
                    ${isActive ? `<button class="btn btn-accent btn-sm" onclick="scanSampleForProduct('${p.id}')"><i class="fa-solid fa-camera"></i> Scan Sample</button>` : ''}
                </div>

                <div class="sampling-block">
                    <div class="sampling-top">
                        <span class="sampling-title">Sampling Progress</span>
                        <span class="sampling-count">${p.samples_inspected} / ${p.sample_target} <em>(${pct}%)</em></span>
                    </div>
                    <div class="progress-track">
                        <div class="progress-fill ${pct >= 100 ? 'is-complete' : ''}" style="width: ${pct}%;"></div>
                    </div>
                    <div class="sampling-stats">
                        <div class="sampling-stat"><b>${p.total_quantity}</b> Total stock</div>
                        <div class="sampling-stat"><b>${p.sample_target}</b> Sample target</div>
                        <div class="sampling-stat"><b>${p.samples_inspected}</b> Inspected</div>
                        <div class="sampling-stat"><b>${p.remaining_samples}</b> Remaining</div>
                        <div class="sampling-stat"><b>${p.uninspected_stock}</b> Uninspected stock</div>
                    </div>
                    <div class="sampling-note">
                        <i class="fa-solid fa-circle-info"></i>
                        <span>Uninspected stock has not been sampled and is <strong>not</strong> certified compliant by this inspection.</span>
                    </div>
                </div>

                <div class="verdict-row">
                    <span class="verdict-chip verdict-pass ${p.pass_count ? '' : 'is-zero'}"><b>${p.pass_count}</b> PASS</span>
                    <span class="verdict-chip verdict-review ${p.review_count ? '' : 'is-zero'}"><b>${p.review_count}</b> REVIEW</span>
                    <span class="verdict-chip verdict-fail ${p.non_compliant_count ? '' : 'is-zero'}"><b>${p.non_compliant_count}</b> NON-COMPLIANT</span>
                </div>

                ${samplesBlock}
            </div>
        `;
    }).join("");
}

function openAddProductModal() {
    document.getElementById("ap-product-name").value = "";
    document.getElementById("ap-manufacturer").value = "";
    document.getElementById("ap-total-quantity").value = "";
    document.getElementById("ap-sample-target").value = "";
    document.getElementById("ap-category").value = "";
    document.getElementById("ap-location").value = "";
    document.getElementById("ap-notes").value = "";
    document.getElementById("ap-error").style.display = "none";
    document.getElementById("add-product-modal").style.display = "flex";
}

function closeAddProductModal() {
    document.getElementById("add-product-modal").style.display = "none";
}

async function submitAddProduct() {
    if (!currentSession) return;
    const errEl = document.getElementById("ap-error");
    const payload = {
        product_name: document.getElementById("ap-product-name").value.trim(),
        manufacturer: document.getElementById("ap-manufacturer").value.trim(),
        total_quantity: parseInt(document.getElementById("ap-total-quantity").value, 10) || 0,
        sample_target: parseInt(document.getElementById("ap-sample-target").value, 10) || 0,
        category: document.getElementById("ap-category").value.trim(),
        location: document.getElementById("ap-location").value.trim(),
        notes: document.getElementById("ap-notes").value.trim(),
    };
    if (!payload.product_name) {
        errEl.textContent = "Product name is required.";
        errEl.style.display = "block";
        return;
    }
    try {
        const resp = await apiFetch(`/api/sessions/${currentSession.id}/products`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload)
        });
        const data = await resp.json();
        if (!resp.ok) throw new Error(data.detail || "Could not add product.");
        currentSession = data;
        renderSessionWorkspace(data);
        closeAddProductModal();
    } catch (e) {
        errEl.textContent = e.message;
        errEl.style.display = "block";
    }
}

// ---------- Seizure -> automatic manufacturer violation reports ----------
const DISPATCH_META = {
    SENT:       { label: "Sent",            cls: "badge-pass" },
    SIMULATED:  { label: "Sent (simulated)", cls: "badge-pass" },
    NO_CONTACT: { label: "No contact on file", cls: "badge-review" },
    FAILED:     { label: "Delivery failed",  cls: "badge-fail" },
};

function violationReportUrl(reportId) {
    const token = currentUser && currentUser.access_token ? `?token=${encodeURIComponent(currentUser.access_token)}` : "";
    return `/api/violation-reports/${reportId}/html${token}`;
}

async function confirmSeizure() {
    if (!currentSession) return;
    const nc = currentSession.non_compliant_products || 0;
    const ok = confirm(
        `Confirm SEIZURE for ${currentSession.id}?\n\n`
        + `${nc} non-compliant product(s) will be reported. TRACE AI will generate a statutory `
        + `violation report for each manufacturer involved and dispatch it to their registered contact.\n\n`
        + `This cannot be undone.`
    );
    if (!ok) return;
    try {
        const resp = await apiFetch(`/api/sessions/${currentSession.id}/seize`, { method: "POST" });
        const data = await resp.json();
        if (!resp.ok) throw new Error(data.detail || "Could not record the seizure.");
        currentSession = data.session;
        renderSessionWorkspace(currentSession);
        await loadSeizureReports(currentSession.id);
        alert(data.message);
    } catch (e) {
        alert("Seizure failed: " + e.message);
    }
}

async function loadSeizureReports(sessionId) {
    const panel = document.getElementById("seizure-panel");
    const box = document.getElementById("seizure-reports");
    if (!panel || !box) return;
    try {
        const resp = await apiFetch(`/api/sessions/${sessionId}/violation-reports`);
        const reports = await resp.json();
        if (!reports.length) { panel.style.display = "none"; return; }
        panel.style.display = "block";
        box.innerHTML = reports.map(r => {
            const rep = r.report || {};
            const meta = DISPATCH_META[r.dispatch_status] || { label: r.dispatch_status, cls: "badge-review" };
            const violations = (rep.violations || []).length;
            return `
                <div class="report-row">
                    <div class="report-row-main">
                        <div class="report-row-title">
                            <i class="fa-solid fa-industry"></i> ${r.manufacturer}
                            <span class="badge ${meta.cls}">${meta.label}</span>
                        </div>
                        <div class="report-row-meta">
                            <span><i class="fa-solid fa-envelope"></i> ${r.recipient_email || 'No registered contact'}</span>
                            <span><i class="fa-solid fa-clock"></i> ${r.sent_at || 'not dispatched'}</span>
                            <span><i class="fa-solid fa-triangle-exclamation"></i> ${violations} violation${violations === 1 ? '' : 's'}</span>
                            <span><i class="fa-solid fa-box"></i> ${rep.products_inspected || 0} product${(rep.products_inspected || 0) === 1 ? '' : 's'}</span>
                        </div>
                        <div class="report-row-detail">${r.dispatch_detail || ''}</div>
                    </div>
                    <a class="btn btn-secondary btn-sm" href="${violationReportUrl(r.id)}" target="_blank">
                        <i class="fa-solid fa-file-lines"></i> View Report
                    </a>
                </div>`;
        }).join("");
    } catch (e) {
        console.error("Violation report load error:", e);
        panel.style.display = "none";
    }
}

async function closeCurrentSession() {
    if (!currentSession) return;
    if (!confirm("Close this inspection? No further products or samples can be added afterward.")) return;
    try {
        const resp = await apiFetch(`/api/sessions/${currentSession.id}/close`, { method: "POST" });
        const data = await resp.json();
        if (!resp.ok) throw new Error(data.detail || "Could not close inspection.");
        currentSession = data;
        renderSessionWorkspace(data);
    } catch (e) {
        alert("Failed to close inspection: " + e.message);
    }
}

// Sets the active scan context, shows it on the scanner banner, and lands the officer
// on the existing, unmodified AI Scanner UI -- all 4 existing input widgets (dataset
// chip / upload / dual-panel / camera) still funnel into the one executeScan().
function scanSampleForProduct(productId) {
    if (!currentSession) return;
    const product = (currentSession.products || []).find(p => p.id === productId);
    const productName = product ? product.product_name : productId;
    activeScanContext = { session_id: currentSession.id, product_id: productId, product_name: productName };
    switchView('scanner');
}
