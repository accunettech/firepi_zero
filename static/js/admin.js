// tiny helpers
const $ = (sel) => document.querySelector(sel);
async function apiGet(url) {
  const r = await fetch(url, { cache: "no-store" });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}
async function apiPost(url, body) {
  const r = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}
function setText(id, txt) { const el = $(id); if (el) el.textContent = txt; }

async function refreshLogTail() {
  try {
    const j = await apiGet("/api/admin/log/tail?lines=50");
    $("#logTail").textContent = j.text || "—";
  } catch (e) {
    $("#logTail").textContent = "Failed to load log: " + (e.message || e);
  }
}

async function refreshVersion() {
  try {
    const j = await apiGet("/api/admin/version");
    setText("#verInstalled", j.current || "—");
    setText("#verLatest", j.latest || "—");
    setText("#verRepo", j.repo || "accunettech/firepi_zero");
  } catch (e) {
    setText("#verInstalled", "—");
    setText("#verLatest", "—");
  }
}

function appendUpdateOutput(line) {
  const pre = $("#updateOutput");
  pre.textContent += (pre.textContent ? "\n" : "") + line;
  pre.scrollTop = pre.scrollHeight;
}

async function doUpdate() {
  if (!confirm("Backup current code and update to origin/main?\nThis will overwrite local changes.")) return;
  $("#updateOutput").textContent = "Starting update…";
  try {
    const j = await apiPost("/api/admin/update");
    (j.log || []).forEach(ln => appendUpdateOutput(ln));
    appendUpdateOutput("— Update finished —");
    if (j.next_step === "reboot") {
      const yn = confirm("Update completed. Reboot now?");
      if (yn) await apiPost("/api/admin/reboot");
    }
  } catch (e) {
    appendUpdateOutput("Update failed: " + (e.message || e));
  }
  await refreshVersion();
}

async function doRollback() {
  if (!confirm("Restore previous backup (if present)?\nThis will overwrite current files.")) return;
  $("#updateOutput").textContent = "Starting rollback…";
  try {
    const j = await apiPost("/api/admin/rollback");
    (j.log || []).forEach(ln => appendUpdateOutput(ln));
    appendUpdateOutput("— Rollback finished —");
    if (j.next_step === "reboot") {
      const yn = confirm("Rollback completed. Reboot now?");
      if (yn) await apiPost("/api/admin/reboot");
    }
  } catch (e) {
    appendUpdateOutput("Rollback failed: " + (e.message || e));
  }
  await refreshVersion();
}

document.addEventListener("DOMContentLoaded", () => {
  $("#btnRefreshLog")?.addEventListener("click", refreshLogTail);
  $("#btnDownloadLog")?.addEventListener("click", () => { /* normal link */ });
  $("#btnCheckVersion")?.addEventListener("click", refreshVersion);
  $("#btnUpdate")?.addEventListener("click", doUpdate);
  $("#btnRollback")?.addEventListener("click", doRollback);
  $("#btnReboot")?.addEventListener("click", async () => {
    if (confirm("Reboot the device now?")) {
      try { await apiPost("/api/admin/reboot"); } catch {}
    }
  });

  refreshLogTail();
  refreshVersion();
});
