let __allowProgressShow = false;
let __progressCount = 0;
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

  function formatAgeFromSeconds(deltaSec) {
    const halfStr = (x) => {
      const r = Math.round(x * 2) / 2; // nearest 0.5
      return Number.isInteger(r) ? String(r|0) : String(r);
    };
    const d = Math.max(0, Math.floor(deltaSec));
    if (d < 60) return `${d}s`;
    if (d < 3600) return `${Math.floor(d / 60)}m`;
    if (d < 86400) return `${halfStr(d / 3600)}h`;
    if (d <= 365 * 86400) return `${halfStr(d / 86400)}d`;
    return `${halfStr(d / (365 * 86400))}y`;
  }

  function connectHealthSSE() {
    const badge = $("#healthBadge");
    if (!badge) return;

    try {
      if (window.__healthES) {
        window.__healthES.close();
        window.__healthES = null;
      }

      const es = new EventSource("/events", { withCredentials: false });
      window.__healthES = es;

      es.addEventListener("open", () => {
      });

      es.addEventListener("error", () => {
        console.warn("[sse] error");
        try { es.close(); } catch {}
        window.__healthES = null;
        setTimeout(connectHealthSSE, 5000);
      });

      es.addEventListener("health", (ev) => {
        try {
          const j = JSON.parse(ev.data);
          renderHealthBadge(j);
        } catch {}
      });

      es.addEventListener("snapshot", (ev) => {
        try {
          const { version, ts } = JSON.parse(ev.data || '{}');
          const img = document.getElementById('panelSnapshotImg');
          if (!img || typeof version !== 'number') return;
          const u = new URL(img.src, location.href);
          u.searchParams.set('v', String(version));  // cache-bust
          img.src = u.toString();
          const meta = document.getElementById('snapMeta');
          if (meta) {
            meta.textContent = ts ? `updated - ${new Date(ts*1000).toLocaleTimeString()}` : ``;
          }
        } catch {}
      });
    } catch (e) {
      console.warn("SSE init failed:", e);
    }
  }

  function renderHealthBadge(j) {
    const badge = $("#healthBadge");
    if (!badge) return;

    const state = (j.state ?? j.solenoid_state ?? "").toString().toUpperCase();
    if (state === "ON" || state === "OFF") {
      const ts = Number(j.last_change_ts);
      let age = "—";
      const halfStr = (x) => {
        const r = Math.round(x * 2) / 2;
        return Number.isInteger(r) ? String(r|0) : String(r);
      };
      if (Number.isFinite(ts) && ts > 0) {
        const delta = Math.max(0, Math.floor(Date.now() / 1000 - ts));
        if (delta < 60)       age = `${delta}s`;
        else if (delta < 3600)age = `${Math.floor(delta / 60)}m`;
        else if (delta < 86400)age = `${halfStr(delta / 3600)}h`;
        else if (delta <= 365*86400) age = `${halfStr(delta / 86400)}d`;
        else                   age = `${halfStr(delta / (365*86400))}y`;
      }
      const label = age ? `${state} • ${age}` : state;
      if (state === "ON") setBadge(badge, "bg-success-subtle text-success-emphasis", label);
      else setBadge(badge, "bg-danger-subtle text-danger-emphasis", label);
      return;
    }

    const ok = j?.status === "ok" || j?.ok === true;
    if (ok) setBadge(badge, "bg-success-subtle text-success-emphasis", "OK");
    else setBadge(badge, "bg-danger-subtle text-danger-emphasis", "Error");
  }

  // --- history ---
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
    connectHealthSSE();
    //refreshHealthBadge();

    $("#btnRefreshHistory")?.addEventListener("click", () => refreshHistory(100));
    const drawer = document.getElementById("historyDrawer");
    drawer?.addEventListener("shown.bs.offcanvas", () => refreshHistory(100));
  });
})();


  // --- progress modal helpers ---
  let __progressModalInst = null;
  function __ensureProgressModal() {
    const el = document.getElementById('progressModal');
    if (!el) return null;
    if (!__progressModalInst) {
      __progressModalInst = 
// FirePi guard: only allow progressModal to show via showProgress()
(() => {
  if (!window.bootstrap || !bootstrap.Modal || bootstrap.Modal.__firepiGuard) return;
  const P = bootstrap.Modal.prototype;
  const _show = P.show;
  const _hide = P.hide;
  P.show = function(...args){
    const el = this._element;
    if (el && el.id === 'progressModal' && !window.__allowProgressShow) {
      console.warn('[progress] blocked stray show on progressModal');
      return;
    }
    return _show.apply(this, args);
  };
  P.hide = function(...args){
    const el = this._element;
    const ret = _hide.apply(this, args);
    if (el && el.id === 'progressModal') {
      // defensive cleanup
      el.classList.remove('show');
      el.style.display = 'none';
      document.querySelectorAll('.modal-backdrop').forEach(b => b.remove());
      document.body.classList.remove('modal-open');
      document.body.style.removeProperty('overflow');
      document.body.style.removeProperty('padding-right');
    }
    return ret;
  };
  bootstrap.Modal.__firepiGuard = true;
})();
bootstrap.Modal ? new bootstrap.Modal(el, { backdrop: 'static', keyboard: false }) : null;
    }
    return __progressModalInst;
  }
  window.showProgress = (title, subtext) => {
    const m = __ensureProgressModal(); if (!m) return;
    const t = document.getElementById('progressText'); if (t) t.textContent = title || 'Working…';
    const s = document.getElementById('progressSub');  if (s) s.textContent = subtext || 'Please wait…';
    m.show();
  };
  window.hideProgress = () => {
    if (__progressModalInst) __progressModalInst.hide();
  };


function showProgress(title='Working…', msg='') {
  const el = document.getElementById('progressModal');
  if (!el || !window.bootstrap || !bootstrap.Modal) return;
  el.querySelector('.modal-title')?.replaceChildren(document.createTextNode(title || ''));
  el.querySelector('.modal-body')?.replaceChildren(document.createTextNode(msg || ''));
  const m = bootstrap.Modal.getOrCreateInstance(el, { backdrop: 'static', keyboard: false, focus: false });
  if (el.classList.contains('show')) { __progressCount = Math.max(1, __progressCount); return; }
  __progressCount++;
  window.__allowProgressShow = true;
  try { m.show(); } finally { window.__allowProgressShow = false; }
}


function hideProgress() {
  const el = document.getElementById('progressModal');
  if (!el || !window.bootstrap || !bootstrap.Modal) return;
  __progressCount = Math.max(0, __progressCount - 1);
  if (__progressCount > 0) return;
  const m = bootstrap.Modal.getOrCreateInstance(el);
  try { m.hide(); } catch {}
  // Defensive cleanup
  el.classList.remove('show');
  el.style.display = 'none';
  el.setAttribute('aria-hidden', 'true');
  el.removeAttribute('aria-modal');
  document.querySelectorAll('.modal-backdrop').forEach(b => b.remove());
  document.body.classList.remove('modal-open');
  document.body.style.removeProperty('overflow');
  document.body.style.removeProperty('padding-right');
}
