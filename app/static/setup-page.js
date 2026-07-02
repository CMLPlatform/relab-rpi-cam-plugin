function fallbackCopyText(text) {
  const textarea = document.createElement("textarea");
  textarea.value = text;
  textarea.setAttribute("readonly", "");
  textarea.style.position = "fixed";
  textarea.style.opacity = "0";
  textarea.style.pointerEvents = "none";
  document.body.appendChild(textarea);
  textarea.focus();
  textarea.select();
  textarea.setSelectionRange(0, textarea.value.length);
  const ok = document.execCommand("copy");
  document.body.removeChild(textarea);
  return ok;
}

function pairingButtonFeedbackSvg(state) {
  if (state === "copied" || state === "success") {
    return `
        <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
          <path d="M20.25 6.75 9.75 17.25 3.75 11.25" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"></path>
        </svg>
        <span class="sr-only">${state === "success" ? "Refreshed" : "Copied"}</span>
      `;
  }
  return `
      <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
        <path d="M15.75 8.25 8.25 15.75M8.25 8.25l7.5 7.5" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"></path>
      </svg>
      <span class="sr-only">Copy failed</span>
    `;
}

function setPairingButtonFeedback(button, state, label, title) {
  if (!button) {
    return;
  }
  if (!button.dataset.defaultHtml) {
    button.dataset.defaultHtml = button.innerHTML;
  }
  button.dataset.feedbackState = state;
  button.innerHTML = pairingButtonFeedbackSvg(state);
  button.setAttribute("aria-label", label);
  button.setAttribute("title", title);
}

function restorePairingButton(button, label, title, delay = 1200) {
  window.setTimeout(() => {
    if (button.dataset.defaultHtml) {
      button.innerHTML = button.dataset.defaultHtml;
    }
    button.dataset.feedbackState = "";
    button.setAttribute("aria-label", label);
    button.setAttribute("title", title);
  }, delay);
}

function setPairingActionStatus(text, state) {
  const el = document.getElementById("pairing-action-status");
  if (!el) {
    return;
  }
  el.dataset.state = state || "";
  el.textContent = text || "";
  if (el._pairingActionStatusTimer) {
    window.clearTimeout(el._pairingActionStatusTimer);
    el._pairingActionStatusTimer = null;
  }
  if (text) {
    el._pairingActionStatusTimer = window.setTimeout(() => {
      el.textContent = "";
      el.dataset.state = "";
    }, 1200);
  }
}

async function copyText(text, button) {
  const value = String(text || "").trim();
  let copied = false;
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(value);
      copied = true;
    }
  } catch {
    copied = false;
  }

  if (!copied) {
    copied = fallbackCopyText(value);
  }

  if (button) {
    const previousLabel = button.getAttribute("aria-label") || "Copy code";
    const previousTitle = button.getAttribute("title") || previousLabel;
    const nextLabel = copied ? "Copied pairing code" : "Copy pairing code failed";
    setPairingButtonFeedback(button, copied ? "copied" : "failed", nextLabel, nextLabel);
    setPairingActionStatus(copied ? "Copied" : "Copy failed", copied ? "success" : "failed");
    restorePairingButton(button, previousLabel, previousTitle);
  }
}

async function refreshPairingCode(button) {
  const previousLabel = button ? button.getAttribute("aria-label") || "Generate a new pairing code" : "";
  const previousTitle = button ? button.getAttribute("title") || previousLabel : "";
  if (button) {
    button.disabled = true;
    button.setAttribute("aria-label", "Refreshing pairing code...");
    button.setAttribute("title", "Refreshing pairing code...");
  }
  setPairingActionStatus("Generating new code...", "pending");

  try {
    const resp = await fetch("/pairing/code", { method: "POST", credentials: "same-origin" });
    if (!resp.ok) {
      if (button) {
        setPairingButtonFeedback(button, "failed", "Could not refresh pairing code", "Could not refresh pairing code");
        setPairingActionStatus("Refresh failed", "failed");
        restorePairingButton(button, previousLabel, previousTitle);
      }
      alert(`Could not refresh the pairing code (${resp.status}).`);
      return;
    }
    if (button) {
      setPairingButtonFeedback(button, "success", "Pairing code refreshed", "Pairing code refreshed");
      setPairingActionStatus("Refreshed. Reloading page...", "success");
    }
    window.setTimeout(() => window.location.reload(), 700);
  } catch {
    if (button) {
      setPairingButtonFeedback(button, "failed", "Could not refresh pairing code", "Could not refresh pairing code");
      setPairingActionStatus("Refresh failed", "failed");
      restorePairingButton(button, previousLabel, previousTitle);
    }
    alert("Could not refresh the pairing code. Check your connection.");
  } finally {
    if (button) {
      button.disabled = false;
    }
  }
}

function openUnpairDialog() {
  document.getElementById("unpair-dialog").showModal();
}

async function confirmUnpair() {
  const dialog = document.getElementById("unpair-dialog");
  const btn = document.getElementById("unpair-confirm-btn");
  btn.disabled = true;
  btn.textContent = "Unpairing...";
  try {
    const resp = await fetch("/pairing", { method: "DELETE", credentials: "same-origin" });
    dialog.close();
    if (resp.ok) {
      setPairingActionStatus("Unpaired. Reloading page...", "success");
      window.setTimeout(() => window.location.reload(), 700);
    } else {
      alert(`Unpair failed (${resp.status}). Try again.`);
    }
  } catch {
    dialog.close();
    alert("Unpair request failed. Check your connection.");
  } finally {
    btn.disabled = false;
    btn.textContent = "Unpair";
  }
}

function setLocalKeyStatus(text, state) {
  const status = document.getElementById("local-key-status");
  if (!status) {
    return;
  }
  status.dataset.state = state || "";
  status.textContent = text || "";
}

async function revealLocalKey(button) {
  const target = document.getElementById("local-api-key");
  const copyButton = document.querySelector("[data-local-key-copy]");
  if (!target) {
    return;
  }

  button.disabled = true;
  button.textContent = "Revealing...";
  setLocalKeyStatus("Fetching local key...", "pending");

  try {
    const resp = await fetch("/local-key", {
      method: "GET",
      credentials: "same-origin",
      cache: "no-store",
    });
    if (!resp.ok) {
      throw new Error(`HTTP ${resp.status}`);
    }
    const key = (await resp.text()).trim();
    target.textContent = key || "Local key is empty.";
    if (copyButton) {
      copyButton.disabled = !key;
    }
    button.textContent = "Local API key revealed";
    setLocalKeyStatus("Key revealed for this page only.", "success");
  } catch (_err) {
    button.disabled = false;
    button.textContent = "Reveal local API key";
    setLocalKeyStatus("Could not reveal the local key from this client.", "failed");
  }
}

function setupCountdown() {
  const countdown = document.querySelector("[data-pairing-countdown]");
  const expiry = document.querySelector("[data-pairing-expiry]");
  const progress = document.querySelector("[data-pairing-progress]");

  if (!countdown || !expiry || !progress) {
    return;
  }

  const expiresAt = new Date(expiry.dataset.expiresAt || "");
  const ttlMs = Number(expiry.dataset.ttlMs || "0");

  if (Number.isNaN(expiresAt.getTime())) {
    return;
  }

  const formatRemaining = (ms) => {
    if (ms <= 0) {
      return "expired";
    }
    const totalSeconds = Math.ceil(ms / 1000);
    const minutes = Math.floor(totalSeconds / 60);
    const seconds = totalSeconds % 60;
    if (minutes > 0) {
      return `${minutes}m ${String(seconds).padStart(2, "0")}s`;
    }
    return `${seconds}s`;
  };

  const tick = () => {
    const remainingMs = expiresAt.getTime() - Date.now();
    countdown.textContent = formatRemaining(remainingMs);

    const ratio = ttlMs > 0 ? Math.max(0, Math.min(1, remainingMs / ttlMs)) : 0;
    progress.style.width = `${ratio * 100}%`;
    progress.classList.toggle("is-expired", remainingMs <= 0);
  };

  tick();
  const intervalId = window.setInterval(() => {
    tick();
    if (expiresAt.getTime() - Date.now() <= 0) {
      window.clearInterval(intervalId);
    }
  }, 1000);
}

function setupPairingStatePolling() {
  const stateEl = document.querySelector("[data-pairing-state]");
  if (!stateEl) {
    return;
  }
  const initialMode = stateEl.dataset.initialMode || "";
  const initialStatus = stateEl.dataset.initialPairingStatus || "";

  let reloading = false;
  const reload = () => {
    if (reloading) {
      return;
    }
    reloading = true;
    window.location.reload();
  };

  const poll = async () => {
    if (reloading) {
      return;
    }
    try {
      const resp = await fetch("/pairing/state", { cache: "no-store" });
      if (!resp.ok) {
        return;
      }
      const data = await resp.json();
      if (data.mode !== initialMode || data.status !== initialStatus) {
        reload();
      }
    } catch {
      // Network blip: keep polling.
    }
  };

  const intervalId = window.setInterval(poll, 2000);
  window.addEventListener("beforeunload", () => window.clearInterval(intervalId));
}

function setupActions() {
  for (const button of document.querySelectorAll("[data-copy-source]")) {
    button.addEventListener("click", () => {
      const source = document.getElementById(button.dataset.copySource);
      copyText(source ? source.textContent : "", button);
    });
  }

  const refreshButton = document.querySelector("[data-refresh-pairing]");
  if (refreshButton) {
    refreshButton.addEventListener("click", () => refreshPairingCode(refreshButton));
  }

  const revealLocalKeyButton = document.querySelector("[data-local-key-reveal]");
  if (revealLocalKeyButton) {
    revealLocalKeyButton.addEventListener("click", () => revealLocalKey(revealLocalKeyButton));
  }

  const openUnpairButton = document.querySelector("[data-unpair-open]");
  if (openUnpairButton) {
    openUnpairButton.addEventListener("click", openUnpairDialog);
  }

  const closeDialogButton = document.querySelector("[data-dialog-close]");
  if (closeDialogButton) {
    closeDialogButton.addEventListener("click", () => document.getElementById("unpair-dialog").close());
  }

  const confirmButton = document.querySelector("[data-unpair-confirm]");
  if (confirmButton) {
    confirmButton.addEventListener("click", confirmUnpair);
  }
}

document.addEventListener("DOMContentLoaded", () => {
  setupActions();
  setupCountdown();
  setupPairingStatePolling();
});
