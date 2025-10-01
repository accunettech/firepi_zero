(() => {
  async function refreshStatus() {
    try {
      const j = await apiGet('/api/wifi/status');
      const pill = document.getElementById('wifiStatusPill');
      const ip   = document.getElementById('wifiStatusIp');
      if (pill) {
        let cls = 'bg-secondary-subtle text-secondary-emphasis';
        let txt = (j.state || 'unknown').toUpperCase();
        if (j.state === 'connected') cls = 'bg-success-subtle text-success-emphasis';
        else if (j.state?.includes('connecting')) cls = 'bg-warning-subtle text-warning-emphasis';
        pill.className = 'badge rounded-pill px-3 py-2 ' + cls;
        pill.textContent = txt;
      }
      if (ip) ip.textContent = j.ip || '—';
    } catch (_) {}
  }

  async function scan() {
    try {
      showProgress('Scanning Wi‑Fi', 'Looking for nearby networks…');
    } catch(e){}
    try {
      const j = await apiGet('/api/wifi/scan');
      const list = document.getElementById('wifiScanList');
      list.innerHTML = '';
      (j.networks||[]).forEach(n => {
        const li = document.createElement('button');
        li.className = 'list-group-item list-group-item-action d-flex justify-content-between align-items-center';
        li.type = 'button';
        li.innerHTML = `<span>${n.ssid || '(hidden)'} <small class="text-muted">${n.security}</small></span><span class="badge bg-dark-subtle">${n.signal}</span>`;
        li.addEventListener('click', () => {
          const ssid = document.getElementById('wifiSsid');
          if (ssid) { ssid.value = n.ssid; ssid.focus(); }
        });
        list.appendChild(li);
      });
      toast('Scan complete');
    } catch (e) {
      toast('Scan failed');
    } finally {
      try { hideProgress(); } catch(e){}
    }
  }

  async function connect() {
    const ssid = (document.getElementById('wifiSsid')?.value || '').trim();
    const psk  = (document.getElementById('wifiPsk')?.value || '').trim();
    if (!ssid) { toast('Please choose an SSID'); return; }
    const btn = document.getElementById('btnWifiConnect');
    const html = btn.innerHTML; btn.disabled = true;
    btn.innerHTML = `<span class="spinner-border spinner-border-sm me-1"></span>Connecting…`;
    try {
      showProgress('Connecting', 'Applying Wi‑Fi credentials…');
    } catch(e){}
    const controller = new AbortController();
    const handoffTimer = setTimeout(() => controller.abort(), 2500);
    let started = false;
    try {
      const res = await fetch('/api/wifi/connect', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ ssid, psk }),
        signal: controller.signal
      });
      started = res.ok; // but we don’t actually require a response
    } catch (e) {
      // Network error here is EXPECTED if AP drops mid-request.
      // Do NOT treat as failure.
      started = true
    } finally {
      clearTimeout(handoffTimer);
    }
    hideProgress();
    toast('Switching networks… If this page stops responding, reconnect to your usual Wi-Fi. I’ll try to find the device automatically.', 'info');

    if (started) {
      toast('Switching networks… if this page stops responding, reconnect to your Wi-Fi. I’ll try to find the device.', 'info');
      probeAndRedirect();
    } else {
      toast('Failed to start Wi-Fi switch. Check service logs.', 'danger');
    }
  }

  async function probeAndRedirect() {
  const sleep = (ms) => new Promise(r => setTimeout(r, ms));
  const targets = [window.location.origin, 'http://firepi.local'];
  const deadline = Date.now() + 60_000;

  await sleep(8000); // let it associate + DHCP

  while (Date.now() < deadline) {
    for (const base of targets) {
      try {
        const r = await fetch(`${base}/api/health`, { cache: 'no-store' });
        if (r.ok) { window.location.href = `${base}/admin`; return; }
      } catch (_) { /* keep trying */ }
    }
    await sleep(3000);
  }
  toast('If this page is unresponsive, connect to your Wi-Fi and open http://firepi.local/admin', 'warning');
}

  function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

  async function forget() {
    const ssid = (document.getElementById('wifiSsid')?.value || '').trim();
    if (!ssid) { toast('Enter SSID to forget'); return; }
    try { await apiPost('/api/wifi/forget', { ssid }); toast('Forgot network'); } catch(_) { toast('Forget failed'); }
  }

  document.addEventListener('DOMContentLoaded', () => {
    const scanBtn = document.getElementById('btnWifiScan');
    const connBtn = document.getElementById('btnWifiConnect');
    const forgetBtn = document.getElementById('btnWifiForget');
    scanBtn && scanBtn.addEventListener('click', scan);
    connBtn && connBtn.addEventListener('click', connect);
    forgetBtn && forgetBtn.addEventListener('click', forget);
    refreshStatus();
  });
})();
