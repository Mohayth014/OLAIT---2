// OLAI mobile client

const API = "/api";
let mobileCameraStream = null;
let mobileCapturedPhoto = null;
let mobileCameraMode = "browser";

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
    const status = document.getElementById("m-upload-status");
    status.hidden = false;
    status.textContent = `Uploading ${file.name}...`;
    const form = new FormData();
    form.append("file", file);
    try {
        const response = await fetch(`${API}/documents/upload`, { method: "POST", body: form });
        const body = await response.json();
        if (!response.ok) throw new Error(body.detail || "Upload failed");
        for (let attempt = 0; attempt < 120; attempt += 1) {
            const document = await getJson(`/documents/${encodeURIComponent(body.id)}`);
            if (document.status === "review") {
                status.textContent = "OCR complete. Page ready for human review.";
                await loadRecent();
                await loadStats();
                return;
            }
            if (document.status === "failed") throw new Error(document.error || "OCR failed");
            status.textContent = `Processing ${document.filename}...`;
            await new Promise(resolve => setTimeout(resolve, 1000));
        }
        throw new Error("Processing is taking longer than expected.");
    } catch (error) {
        status.textContent = `Could not process photo: ${error.message}`;
    }
}

async function openMobileCamera() {
    const modal = document.getElementById("m-camera-modal");
    const video = document.getElementById("m-camera-preview");
    const error = document.getElementById("m-camera-error");
    modal.hidden = false;
    error.hidden = true;
    mobileCameraMode = "browser";
    document.getElementById("m-camera-snap").disabled = true;
    try {
        if (!navigator.mediaDevices?.getUserMedia) throw new Error("Live camera is not supported.");
        const cameraRequest = navigator.mediaDevices.getUserMedia({ video: true, audio: false });
        mobileCameraStream = await Promise.race([
            cameraRequest,
            new Promise((_, reject) => setTimeout(() => reject(new Error("Camera permission timed out.")), 8000)),
        ]);
        video.srcObject = mobileCameraStream;
        await new Promise(resolve => {
            if (video.readyState >= HTMLMediaElement.HAVE_METADATA && video.videoWidth > 0) return resolve();
            video.addEventListener("loadedmetadata", resolve, { once: true });
        });
        await video.play();
        document.getElementById("m-camera-snap").disabled = false;
    } catch (cameraError) {
        mobileCameraStream?.getTracks().forEach(track => track.stop());
        mobileCameraStream = null;
        error.hidden = false;
        mobileCameraMode = "server";
        error.textContent = `${cameraError.message} Capture will use the laptop camera directly.`;
        document.getElementById("m-camera-snap").disabled = false;
    }
}

function closeMobileCamera() {
    mobileCameraStream?.getTracks().forEach(track => track.stop());
    mobileCameraStream = null;
    document.getElementById("m-camera-modal").hidden = true;
}

async function snapMobileCamera() {
    if (mobileCameraMode === "server") {
        const response = await fetch(`${API}/camera/snapshot`, { method: "POST" });
        if (!response.ok) throw new Error((await response.json()).detail || "Camera capture failed");
        mobileCapturedPhoto = new File([await response.blob()], `camera-${Date.now()}.jpg`, { type: "image/jpeg" });
        document.getElementById("m-camera-preview").hidden = true;
        document.getElementById("m-camera-snap").hidden = true;
        document.getElementById("m-camera-retake").hidden = false;
        document.getElementById("m-camera-use").hidden = false;
        return;
    }
    const video = document.getElementById("m-camera-preview");
    const canvas = document.getElementById("m-camera-canvas");
    if (!video.videoWidth || !video.videoHeight || video.readyState < HTMLMediaElement.HAVE_CURRENT_DATA) {
        const error = document.getElementById("m-camera-error");
        error.hidden = false;
        error.textContent = "Camera is still starting. Wait for the preview, then try Capture again.";
        return;
    }
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    canvas.getContext("2d").drawImage(video, 0, 0);
    canvas.toBlob(blob => {
        mobileCapturedPhoto = new File([blob], `camera-${Date.now()}.jpg`, { type: "image/jpeg" });
        video.hidden = true;
        document.getElementById("m-camera-snap").hidden = true;
        document.getElementById("m-camera-retake").hidden = false;
        document.getElementById("m-camera-use").hidden = false;
    }, "image/jpeg", 0.92);
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
    document.getElementById("m-camera-launch")?.addEventListener("click", openMobileCamera);
    document.getElementById("m-camera-close")?.addEventListener("click", closeMobileCamera);
    document.getElementById("m-camera-snap")?.addEventListener("click", snapMobileCamera);
    document.getElementById("m-camera-retake")?.addEventListener("click", () => {
        mobileCapturedPhoto = null;
        document.getElementById("m-camera-preview").hidden = false;
        document.getElementById("m-camera-snap").hidden = false;
        document.getElementById("m-camera-retake").hidden = true;
        document.getElementById("m-camera-use").hidden = true;
    });
    document.getElementById("m-camera-use")?.addEventListener("click", () => {
        if (mobileCapturedPhoto) uploadPhoto(mobileCapturedPhoto);
        closeMobileCamera();
    });
    ["m-camera-upload", "m-document-upload"].forEach(id => {
        document.getElementById(id)?.addEventListener("change", event => {
            const [file] = event.target.files;
            if (file) uploadPhoto(file);
            event.target.value = "";
        });
    });
    document.querySelectorAll(".mnav-btn").forEach(b => b.addEventListener("click", () => switchView(b.dataset.view)));
    loadHealth();
    loadRecent();
    loadStats();
});
