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
    body: body ? JSON.stringify(body) : null,
  });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}
function toast(message) {
  const wrap = document.createElement("div");
  wrap.className = "position-fixed top-0 end-0 p-3";
  wrap.style.zIndex = 1080;
  wrap.innerHTML = `
    <div class="toast align-items-center text-bg-dark border-0 show">
      <div class="d-flex">
        <div class="toast-body">${message}</div>
        <button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast"></button>
      </div>
    </div>`;
  document.body.appendChild(wrap);
  setTimeout(() => wrap.remove(), 2600);
}

function parseVer(v) {
  if (!v) return [0, 0, 0];
  return v.replace(/^v/i, "").split(".").map((n) => {
    const x = parseInt(n, 10);
    return Number.isFinite(x) ? x : 0;
  });
}
function isSemverLess(a, b) {
  const A = parseVer(a), B = parseVer(b);
  const len = Math.max(A.length, B.length);
  for (let i = 0; i < len; i++) {
    const x = A[i] ?? 0, y = B[i] ?? 0;
    if (x < y) return true;
    if (x > y) return false;
  }
  return false; // equal
}

const btnUpdate   = () => $("#btnDoUpdate");
const btnRollback = () => $("#btnRollback");
const btnReboot   = () => $("#btnReboot");

async function loadVersions() {
  if (btnUpdate())  btnUpdate().disabled = true;
  if (btnRollback()) btnRollback().disabled = true;

  try {
    const j = await apiGet("/api/admin/version");
    $("#verCurrent").textContent = j.current || "—";
    $("#verLatest").textContent  = j.latest  || "—";

    // Enable Update only if current < latest (both present)
    const canUpdate = j.current && j.latest && isSemverLess(j.current, j.latest);
    if (btnUpdate()) btnUpdate().disabled = !canUpdate;

    // Enable Rollback only if a backup exists
    const hasBackup = !!j.has_backup;
    if (btnRollback()) btnRollback().disabled = !hasBackup;
  } catch (e) {
    $("#verCurrent").textContent = "—";
    $("#verLatest").textContent  = "—";
    if (btnUpdate())  btnUpdate().disabled = true;
    if (btnRollback()) btnRollback().disabled = true;
    toast("Failed to load version info");
  }
}

async function refreshLogs() {
  try {
    const j = await apiGet("/api/admin/log/tail?lines=50");
    $("#logTail").value = j.tail || j.lines || "";
    const dl = $("#btnDownloadLog");
    if (dl) {
      if (j.download_url) {
        dl.href = j.download_url;
        dl.classList.remove("disabled");
        dl.setAttribute("aria-disabled", "false");
      } else {
        dl.removeAttribute("href");
        dl.classList.add("disabled");
        dl.setAttribute("aria-disabled", "true");
      }
    }
  } catch (e) {
    $("#logTail").value = "Unable to load logs.";
  }
}

async function checkLatest() {
  await loadVersions();
  toast("Checked latest version");
}

async function doUpdate() {
  if (!confirm("Update FirePi from GitHub (main)? A single backup will be overwritten. Continue?")) return;
  const u = btnUpdate(); const r = btnRollback();
  try {
    if (u) u.disabled = true;
    if (r) r.disabled = true;
    $("#updateStatus").textContent = "Updating…";
    const j = await apiPost("/api/admin/update");
    $("#updateStatus").textContent = j.status || "Update finished.";
    await loadVersions(); // refresh current/latest + has_backup
    toast(j.status === "ok" ? "Update complete; reboot recommended." : "Update finished with issues");
  } catch (e) {
    $("#updateStatus").textContent = "Update failed.";
    toast("Update failed");
  }
}

async function doRollback() {
  if (!confirm("Rollback to previous backup? This will overwrite current files.")) return;
  const u = btnUpdate(); const r = btnRollback();
  try {
    if (u) u.disabled = true;
    if (r) r.disabled = true;
    $("#updateStatus").textContent = "Rolling back…";
    const j = await apiPost("/api/admin/rollback");
    $("#updateStatus").textContent = j.status || "Rollback complete.";
    await loadVersions(); // backup still exists; version likely changed
    toast(j.status === "ok" ? "Rollback complete; reboot recommended." : "Rollback finished with issues");
  } catch (e) {
    $("#updateStatus").textContent = "Rollback failed.";
    toast("Rollback failed");
  }
}

async function doReboot() {
  if (!confirm("Reboot the device now?")) return;
  try {
    await apiPost("/api/admin/reboot");
    toast("Rebooting…");
  } catch (e) {
    toast("Reboot request failed");
  }
}

document.addEventListener("DOMContentLoaded", async () => {
  $("#btnRefreshLog")?.addEventListener("click", refreshLogs);
  $("#btnCheckLatest")?.addEventListener("click", checkLatest);
  $("#btnDoUpdate")?.addEventListener("click", doUpdate);
  $("#btnRollback")?.addEventListener("click", doRollback);
  $("#btnReboot")?.addEventListener("click", doReboot);

  if (btnUpdate())  btnUpdate().disabled = true;
  if (btnRollback()) btnRollback().disabled = true;

  await loadVersions();
  await refreshLogs();
});

async function loadAudioAdmin() {
  const list = document.getElementById("audioAdminList");
  const empty = document.getElementById("audioAdminEmpty");
  if (!list) return;

  list.innerHTML = "";
  try {
    const files = await apiGet("/api/audio/files");
    if (!files || files.length === 0) {
      if (empty) empty.classList.remove("d-none");
      return;
    }
    if (empty) empty.classList.add("d-none");

    for (const f of files) {
      const li = document.createElement("li");
      li.className = "list-group-item d-flex justify-content-between align-items-center bg-transparent";
      li.innerHTML = `
        <div class="d-flex flex-column">
          <span>${f.filename}</span>
          ${f.size ? `<small class="text-muted">${f.size} bytes</small>` : ""}
        </div>
        <div class="d-flex align-items-center gap-2">
          <a class="btn btn-outline-light btn-sm" href="${f.url}" target="_blank">
            <i class="bi bi-play-circle"></i> Preview
          </a>
          <button class="btn btn-outline-light btn-sm" data-del="${encodeURIComponent(f.filename)}">
            <i class="bi bi-trash"></i> Delete
          </button>
        </div>
      `;
      list.appendChild(li);
    }

    list.querySelectorAll("button[data-del]").forEach(btn => {
      btn.addEventListener("click", async () => {
        const name = decodeURIComponent(btn.dataset.del);
        if (!confirm(`Delete audio file "${name}"?`)) return;
        try {
          await fetch(`/api/admin/audio/${encodeURIComponent(name)}`, { method: "DELETE" });
          toast("Audio deleted");
          await loadAudioAdmin();
        } catch {
          toast("Delete failed");
        }
      });
    });
  } catch {
    if (empty) {
      empty.textContent = "Failed to load audio files.";
      empty.classList.remove("d-none");
    }
  }
}

async function createBundle() {
  const includeSnapshot = document.getElementById("includeSnapshot")?.checked ?? true;
  const statusEl = document.getElementById("supportStatus");
  try {
    statusEl && (statusEl.textContent = "Creating bundle…");
    const j = await apiPost("/api/admin/support/bundle", { include_snapshot: !!includeSnapshot });
    const a = document.getElementById("btnDownloadBundle");
    if (a && j.download_url) {
      a.href = j.download_url;
      a.classList.remove("disabled");
      a.removeAttribute("aria-disabled");
    }
    statusEl && (statusEl.textContent = j.message || "Bundle ready.");
  } catch (e) {
    statusEl && (statusEl.textContent = "Bundle creation failed.");
    toast("Bundle creation failed");
  }
}

async function uploadBundle() {
  const statusEl = document.getElementById("supportStatus");
  try {
    statusEl && (statusEl.textContent = "Uploading bundle…");
    const j = await apiPost("/api/admin/support/upload", { url: '' });
    statusEl && (statusEl.textContent = j.message || "Bundle uploaded.");
    toast("Bundle uploaded");
  } catch {
    statusEl && (statusEl.textContent = "Upload failed.");
    toast("Upload failed");
  }
}

async function uploadSnapshotOnly() {
  const statusEl = document.getElementById("supportStatus");
  try {
    statusEl && (statusEl.textContent = "Uploading snapshot…");
    const j = await apiPost("/api/admin/support/upload-snapshot", { url: '' });
    statusEl && (statusEl.textContent = j.message || "Snapshot uploaded.");
    toast("Snapshot uploaded");
  } catch {
    statusEl && (statusEl.textContent = "Snapshot upload failed.");
    toast("Snapshot upload failed");
  }
}

document.addEventListener("DOMContentLoaded", async () => {
  document.getElementById("btnRefreshAudioAdmin")?.addEventListener("click", loadAudioAdmin);
  document.getElementById("btnUploadBundle")?.addEventListener("click", async () => {
    const include = document.getElementById("includeSnapshot")?.checked ?? true;
    const r = await fetch("/api/admin/support/upload", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        type: "bundle",
        use_latest: true,            // <-- IMPORTANT: reuse the bundle you just created
        include_snapshot: include    // used only if a new bundle must be created
      })
    });
    const j = await r.json();
    if (!r.ok) { toast(j.error || "Upload failed"); return; }
    toast(j.message || "Uploaded");
  });
  document.getElementById("btnUploadSnapshot")?.addEventListener("click", async () => {
    const r = await fetch("/api/admin/support/upload", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ type: "snapshot" })
    });
    const j = await r.json();
    if (!r.ok) { toast(j.error || "Upload failed"); return; }
    toast(j.message || "Uploaded");
  });
  document.getElementById("btnCreateBundle")?.addEventListener("click", async () => {
    const include = document.getElementById("includeSnapshot")?.checked ?? true;
    const r = await fetch("/api/admin/support/bundle", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ include_snapshot: include })
    });
    const j = await r.json();
    if (!r.ok) { toast(j.error || "Bundle failed"); return; }
    // enable download button
    const a = document.getElementById("btnDownloadBundle");
    if (a) { a.classList.remove("disabled"); a.href = j.download_url; }
    document.getElementById("supportStatus").textContent = j.message || "Bundle ready";
  });

  await loadAudioAdmin().catch(()=>{});
});
