(() => {
  window.$  = (sel) => document.querySelector(sel);
  window.$$ = (sel) => Array.from(document.querySelectorAll(sel));

  window.apiGet = async (url) => {
    const r = await fetch(url, { cache: "no-store" });
    if (!r.ok) throw new Error(await r.text());
    return r.json();
  };
  window.apiPost = async (url, body) => {
    const r = await fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: body ? JSON.stringify(body) : null,
    });
    if (!r.ok) throw new Error(await r.text());
    return r.json();
  };
  window.apiPut = async (url, body) => {
    const r = await fetch(url, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: body ? JSON.stringify(body) : "{}",
    });
    if (!r.ok) throw new Error(await r.text());
    return r.json();
  };

  window.toast = (message) => {
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
  };

  function setBadge(el, cls, text) {
    el.className = "badge rounded-pill px-3 py-2 " + cls;
    el.textContent = text;
  }

  async function refreshHealthBadge() {
    const badge = $("#healthBadge");
    if (!badge) return;
    try {
      const j = await apiGet("/api/health");

      // Prefer explicit state if provided
      const state = (j.state ?? j.solenoid_state ?? "").toString().toUpperCase();
      if (state === "ON" || state === "OFF") {
        let age = "";
        const ts = Number(j.last_change_ts);
        if (Number.isFinite(ts) && ts > 0) {
          const delta = Math.max(0, Math.floor(Date.now() / 1000 - ts));
          age = delta < 60 ? `${delta}s` : `${Math.floor(delta / 60)}m`;
        }
        const label = age ? `${state} • ${age}` : state;
        if (state === "ON") setBadge(badge, "bg-success-subtle text-success-emphasis", label);
        else setBadge(badge, "bg-danger-subtle text-danger-emphasis", label);
        return;
      }

      // Fallback to generic ok flag
      const ok = j?.status === "ok" || j?.ok === true;
      if (ok) setBadge(badge, "bg-success-subtle text-success-emphasis", "OK");
      else setBadge(badge, "bg-danger-subtle text-danger-emphasis", "Error");
    } catch {
      setBadge(badge, "bg-warning-subtle text-warning-emphasis", "Unavailable");
    }
  }

  function renderHistory(list, items) {
    list.innerHTML = "";
    if (!Array.isArray(items) || !items.length) {
      list.innerHTML = `<div class="text-muted small px-2">No history yet.</div>`;
      return;
    }
    for (const it of items) {
      const ts = it.ts || it.time || it.timestamp || "";
      const msg = it.message || it.text || it.event || `${(it.alert_type||"event").toUpperCase()} ${it.channel?("• "+it.channel):""}`;
      const row = document.createElement("div");
      row.className = "list-group-item bg-transparent text-light";
      row.innerHTML = `
        <div class="d-flex justify-content-between">
          <div class="me-3">
            <div class="fw-semibold">${msg}</div>
            ${it.status ? `<small class="text-muted">${it.status}</small>` : ""}
          </div>
          ${ts ? `<small class="text-muted text-nowrap ms-2">${ts}</small>` : ""}
        </div>`;
      list.appendChild(row);
    }
  }

  async function refreshHistory(limit = 100) {
    const count = $("#historyCount");
    const list = $("#historyList");
    if (!list) return;
    try {
      if (count) count.textContent = "Loading…";
      const data = await apiGet(`/api/history?limit=${limit}`);
      renderHistory(list, data);
      if (count) count.textContent = `${data?.length ?? 0} item(s)`;
    } catch {
      if (count) count.textContent = "Failed to load";
      list.innerHTML = `<div class="text-danger small px-2">Unable to load history.</div>`;
    }
  }

  document.addEventListener("DOMContentLoaded", () => {
    refreshHealthBadge();
    setInterval(refreshHealthBadge, 5000);

    $("#btnRefreshHistory")?.addEventListener("click", () => refreshHistory(100));
    const drawer = document.getElementById("historyDrawer");
    drawer?.addEventListener("shown.bs.offcanvas", () => refreshHistory(100));
  });
})();
