const HLS_SRC = "/preview/hls/cam-preview/index.m3u8";
const THUMBNAIL_SRC = "/preview-thumbnail.jpg";
const START_DELAY_MS = 2000;

let hls = null;
let state = "idle";

function setChip(kind, text) {
  const chip = document.getElementById("preview-chip");
  chip.className = `status-chip status-chip--${kind}`;
  chip.textContent = text;
}

function showThumbnail() {
  document.getElementById("preview-thumbnail").src = `${THUMBNAIL_SRC}?t=${Date.now()}`;
  document.getElementById("preview-thumbnail").hidden = false;
  document.getElementById("preview-video").hidden = true;
}

function showVideo() {
  document.getElementById("preview-thumbnail").hidden = true;
  document.getElementById("preview-video").hidden = false;
}

function attachPlayer() {
  const video = document.getElementById("preview-video");
  if (window.Hls && window.Hls.isSupported()) {
    hls = new window.Hls({
      liveSyncDurationCount: 2,
      liveMaxLatencyDurationCount: 2,
      backBufferLength: 4,
      maxLiveSyncPlaybackRate: 1.5,
    });
    hls.loadSource(HLS_SRC);
    hls.attachMedia(video);
    hls.on(window.Hls.Events.MANIFEST_PARSED, () => video.play());
  } else if (video.canPlayType("application/vnd.apple.mpegurl")) {
    video.src = HLS_SRC;
    video.play();
  } else {
    throw new Error("HLS playback not supported in this browser.");
  }
}

async function startPreview() {
  if (state !== "idle") {
    return;
  }
  state = "starting";
  const btn = document.getElementById("preview-btn");
  const msg = document.getElementById("preview-msg");
  btn.disabled = true;
  btn.textContent = "Starting...";
  msg.textContent = "Waking the encoder...";
  setChip("muted", "Starting");
  try {
    const resp = await fetch("/preview/start", { method: "POST" });
    if (!resp.ok) {
      throw new Error(`Server failed to start preview (HTTP ${resp.status})`);
    }
    await new Promise((resolve) => window.setTimeout(resolve, START_DELAY_MS));
    showVideo();
    attachPlayer();
    state = "live";
    btn.disabled = false;
    btn.textContent = "Stop Preview";
    msg.textContent = "";
    setChip("live", "Live");
  } catch (err) {
    state = "idle";
    btn.disabled = false;
    btn.textContent = "Load Preview";
    msg.textContent = `Preview unavailable: ${err.message}`;
    setChip("muted", "Preview");
  }
}

async function stopPreview() {
  if (state === "idle") {
    return;
  }
  state = "stopping";
  const btn = document.getElementById("preview-btn");
  const msg = document.getElementById("preview-msg");
  btn.disabled = true;
  btn.textContent = "Stopping...";
  if (hls) {
    hls.destroy();
    hls = null;
  }
  const video = document.getElementById("preview-video");
  video.removeAttribute("src");
  video.load();
  try {
    await fetch("/preview/stop", { method: "POST" });
  } catch (_err) {
    // Best-effort: the encoder hibernates on its own.
  }
  showThumbnail();
  state = "idle";
  btn.disabled = false;
  btn.textContent = "Load Preview";
  msg.textContent = "";
  setChip("muted", "Preview");
}

function togglePreview() {
  if (state === "idle") {
    startPreview();
  } else if (state === "live") {
    stopPreview();
  }
}

document.addEventListener("DOMContentLoaded", () => {
  const button = document.querySelector("[data-preview-toggle]");
  if (button) {
    button.addEventListener("click", togglePreview);
  }
});
