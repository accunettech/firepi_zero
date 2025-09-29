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
    try {
      const r = await apiPost('/api/wifi/connect', { ssid, psk });
      if (r.status === 'ok') {
        toast('Connected. AP will close if active.');
      } else {
        toast(r.error || 'Connect failed');
      }
    } catch (e) {
      toast('Connect failed');
    } finally {
      try { hideProgress(); } catch(e){}
      btn.disabled = false; btn.innerHTML = html;
      setTimeout(refreshStatus, 1500);
    }
  }

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
