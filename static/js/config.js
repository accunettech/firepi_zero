// ---------- tiny helpers ----------
const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => Array.from(document.querySelectorAll(sel));

async function apiGet(url) {
  const r = await fetch(url, { cache: "no-store" });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}
async function apiPut(url, body) {
  const r = await fetch(url, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body || {}),
  });
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

// ---------- global state ----------
let recipientModal;            // bootstrap.Modal instance
let editingRecipientId = null; // null => adding, number => editing

// ---------- health badge ----------
async function refreshHealth() {
  const badge = $("#healthBadge");
  if (!badge) return;

  try {
    const j = await apiGet("/api/health");
    const state = (j.state || "—").toString().toUpperCase();

    let timeStr = "";
    if (typeof j.last_change_ts === "number" && j.last_change_ts > 0) {
      const nowSec = Date.now() / 1000;
      const delta = Math.max(0, nowSec - j.last_change_ts);
      timeStr = delta < 60
        ? `${Math.floor(delta)}s`
        : `${Math.floor(delta / 60)}m`;
    }

    badge.textContent = timeStr ? `${state} • ${timeStr}` : state;

    // Color by state (OFF = alarm-ish => danger; ON = good => success; else neutral)
    let cls = "badge rounded-pill px-3 py-2 ";
    if (state === "ON") {
      cls += "bg-success-subtle text-success-emphasis";
    } else if (state === "OFF") {
      cls += "bg-danger-subtle text-danger-emphasis";
    } else {
      cls += "bg-secondary-subtle text-secondary-emphasis";
    }
    badge.className = cls;
  } catch {
    badge.textContent = "Unknown";
    badge.className = "badge rounded-pill px-3 py-2 bg-warning-subtle text-warning-emphasis";
  }
}

// ---------- provider UI toggle ----------
function showProviderCredentials(which) {
  const value = (which || "").toString().toLowerCase();
  const tw = $("#twilioCard");
  const cs = $("#clicksendCard");
  if (value === "clicksend") {
    tw && tw.classList.add("d-none");
    cs && cs.classList.remove("d-none");
  } else {
    // default to Twilio
    cs && cs.classList.add("d-none");
    tw && tw.classList.remove("d-none");
  }
}

// ---------- settings load/save ----------
async function loadSettingsIntoForm() {
  const s = await apiGet("/api/settings");

  // Toggles
  $("#enablePhoneAlert").checked   = !!s.enable_phone_alert;
  $("#enableEmailAlert").checked   = !!s.enable_email_alert;
  $("#enableSmsAlert").checked     = !!s.enable_sms_alert;
  $("#enableSpeakerAlert").checked = !!s.enable_speaker_alert;

  // Provider
  const provider = (s.telephony_provider || "twilio").toString().toLowerCase();
  const providerSel = $("#telephonyProvider");
  if (providerSel) providerSel.value = provider;
  showProviderCredentials(provider);

  // SMTP
  $("#smtpServer").value     = s.smtp_server || (s.smtp?.server ?? "");
  $("#smtpPort").value       = (s.smtp_port ?? s.smtp?.port) ?? "";
  $("#smtpUsername").value   = s.smtp_username || (s.smtp?.username ?? "");
  $("#smtpPassword").value   = s.smtp_password || (s.smtp?.password ?? "");
  $("#smtpNotifyText").value = s.smtp_notify_text || (s.smtp?.notify_text ?? "");

  // Twilio
  const tw = s.twilio || {};
  $("#twilioUsername").value     = s.twilio_username ?? tw.username ?? "";
  $("#twilioToken").value        = s.twilio_token ?? tw.token ?? "";
  $("#twilioApiSecret").value    = s.twilio_api_secret ?? tw.api_secret ?? "";
  $("#twilioSourceNumber").value = s.twilio_source_number ?? tw.source_number ?? "";
  $("#twilioNotifyText").value   = s.twilio_notify_text ?? tw.notify_text ?? "";

  // ClickSend
  const cs = s.clicksend || {};
  $("#clicksendUsername").value   = s.clicksend_username ?? cs.username ?? "";
  $("#clicksendApiKey").value     = s.clicksend_api_key ?? cs.api_key ?? "";
  $("#clicksendFrom").value       = s.clicksend_from ?? cs.from ?? "";
  $("#clicksendVoiceFrom").value  = s.clicksend_voice_from ?? cs.voice_from ?? "";
  $("#clicksendNotifyText").value = s.clicksend_notify_text ?? cs.notify_text ?? "";

  // MQTT
  $("#mqttHost").value        = s.mqtt_host || "";
  $("#mqttUser").value        = s.mqtt_user || "";
  $("#mqttPassword").value    = s.mqtt_password || "";
  $("#mqttTopicBase").value   = s.mqtt_topic_base || "";
}

async function saveGlobalToggles() {
  const body = {
    enable_phone_alert:   $("#enablePhoneAlert").checked,
    enable_email_alert:   $("#enableEmailAlert").checked,
    enable_sms_alert:     $("#enableSmsAlert").checked,
    enable_speaker_alert: $("#enableSpeakerAlert").checked,
  };
  await apiPut("/api/settings", body);
  toast("Global controls saved");
}

async function saveSmtp() {
  const body = {
    smtp_server: $("#smtpServer").value.trim(),
    smtp_port:   $("#smtpPort").value ? parseInt($("#smtpPort").value, 10) : null,
    smtp_username: $("#smtpUsername").value.trim(),
    smtp_password: $("#smtpPassword").value,
    smtp_notify_text: $("#smtpNotifyText").value.trim(),
  };
  await apiPut("/api/settings", body);
  toast("SMTP saved");
}

async function saveProvider() {
  const sel = $("#telephonyProvider");
  const provider = (sel?.value || "twilio").toString().toLowerCase();

  // Optimistically toggle immediately
  showProviderCredentials(provider);

  // Persist to backend and then reload settings to reflect server's persisted value
  await apiPut("/api/settings", { telephony_provider: provider });
  try {
    await loadSettingsIntoForm(); // re-sync UI after save
  } catch (_) {
    // If reload fails, at least we already toggled locally
  }
  toast("Provider saved");
}

async function saveTwilio() {
  const body = {
    twilio_username: $("#twilioUsername").value.trim(),
    twilio_token: $("#twilioToken").value.trim(),
    twilio_api_secret: $("#twilioApiSecret").value,
    twilio_source_number: $("#twilioSourceNumber").value.trim(),
    twilio_notify_text: $("#twilioNotifyText").value.trim(),
  };
  await apiPut("/api/settings", body);
  toast("Twilio saved");
}

async function saveClickSend() {
  const body = {
    clicksend_username: $("#clicksendUsername").value.trim(),
    clicksend_api_key: $("#clicksendApiKey").value.trim(),
    clicksend_from: $("#clicksendFrom").value.trim(),
    clicksend_voice_from: $("#clicksendVoiceFrom").value.trim(),
    clicksend_notify_text: $("#clicksendNotifyText").value.trim(),
  };
  await apiPut("/api/settings", body);
  toast("ClickSend saved");
}

async function saveMqtt() {
  const body = {
    mqtt_host: $("#mqttHost").value.trim(),
    mqtt_user: $("#mqttUser").value.trim(),
    mqtt_password: $("#mqttPassword").value,
    mqtt_topic_base: $("#mqttTopicBase").value.trim(),
  };
  await apiPut("/api/settings", body);
  toast("MQTT saved");
}

async function testNotifications() {
  try {
    await apiGet("/api/notifications/test");
    toast("Test triggered");
  } catch (e) {
    toast("Test failed: " + (e.message || e));
  }
}

// ---------- recipients ----------
async function fetchRecipients() {
  return apiGet("/api/recipients");
}

function renderRecipientsTable(rows) {
  const tbody = $("#recipientsTbody");
  const empty = $("#emptyState");
  if (!tbody) return;

  tbody.innerHTML = "";

  if (!rows || rows.length === 0) {
    if (empty) empty.classList.remove("d-none");
    return;
  }
  if (empty) empty.classList.add("d-none");

  for (const r of rows) {
    const tr = document.createElement("tr");

    const tdName = document.createElement("td");
    tdName.textContent = r.name || "";
    tr.appendChild(tdName);

    const tdPhone = document.createElement("td");
    tdPhone.textContent = r.phone || "";
    tr.appendChild(tdPhone);

    const tdEmail = document.createElement("td");
    tdEmail.textContent = r.email || "";
    tr.appendChild(tdEmail);

    const tdSms = document.createElement("td");
    tdSms.textContent = r.receive_sms ? "Yes" : "No";
    tr.appendChild(tdSms);

    const tdActions = document.createElement("td");
    tdActions.className = "text-end";
    tdActions.innerHTML = `
      <button class="btn btn-outline-light btn-sm me-2" data-action="edit" data-id="${r.id}">
        <i class="bi bi-pencil"></i>
      </button>
      <button class="btn btn-outline-light btn-sm" data-action="delete" data-id="${r.id}">
        <i class="bi bi-trash"></i>
      </button>
    `;
    tr.appendChild(tdActions);

    tbody.appendChild(tr);
  }

  // Row button handlers (delegate)
  tbody.querySelectorAll("button[data-action]").forEach((btn) => {
    if (btn.dataset.bound) return;
    btn.dataset.bound = "1";
    btn.addEventListener("click", async () => {
      const id = parseInt(btn.getAttribute("data-id"), 10);
      const action = btn.getAttribute("data-action");
      if (!id) return;

      if (action === "edit") {
        const rows = await fetchRecipients();
        const rec = rows.find((x) => x.id === id);
        openRecipientModal("edit", rec || null);
      } else if (action === "delete") {
        if (!confirm("Delete this recipient?")) return;
        await fetch(`/api/recipients/${id}`, { method: "DELETE" });
        toast("Recipient deleted");
        await refreshRecipientsUI();
      }
    });
  });
}

async function refreshRecipientsUI() {
  const rows = await fetchRecipients();
  renderRecipientsTable(rows);
}

function openRecipientModal(mode = "add", rec = null) {
  editingRecipientId = mode === "edit" && rec ? rec.id : null;

  // Title
  const titleEl = $("#recipientModalTitle");
  if (titleEl) titleEl.textContent = editingRecipientId ? "Edit Recipient" : "Add Recipient";

  // Fill fields
  $("#recName").value  = rec?.name  ?? "";
  $("#recPhone").value = rec?.phone ?? "";
  $("#recEmail").value = rec?.email ?? "";
  $("#recSms").checked = !!rec?.receive_sms;

  recipientModal.show();
}

async function saveRecipient() {
  const payload = {
    name: $("#recName").value.trim(),
    phone: $("#recPhone").value.trim(),
    email: $("#recEmail").value.trim(),
    receive_sms: $("#recSms").checked,
  };
  if (!payload.name) {
    toast("Name is required");
    return;
  }

  try {
    if (editingRecipientId) {
      await fetch(`/api/recipients/${editingRecipientId}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      toast("Recipient updated");
    } else {
      await apiPost("/api/recipients", payload);
      toast("Recipient added");
    }
    recipientModal.hide();
    await refreshRecipientsUI();
  } catch (e) {
    toast("Save failed: " + (e.message || e));
  }
}

// ---------- history drawer ----------
async function refreshHistory() {
  try {
    const rows = await apiGet("/api/history?limit=100");
    const list = $("#historyList");
    const count = $("#historyCount");
    if (count) count.textContent = `${rows.length} recent event(s)`;

    if (!list) return;
    list.innerHTML = "";

    for (const r of rows) {
      const ts = new Date(r.ts);
      const when = ts ? ts.toLocaleString() : "—";
      const statusBadge = r.status === "success"
        ? `<span class="badge bg-success-subtle text-success-emphasis">success</span>`
        : r.status === "error"
          ? `<span class="badge bg-danger-subtle text-danger-emphasis">error</span>`
          : `<span class="badge bg-secondary-subtle text-secondary-emphasis">${r.status || "—"}</span>`;

      const item = document.createElement("div");
      item.className = "list-group-item list-group-item-action bg-transparent";
      item.innerHTML = `
        <div class="d-flex w-100 justify-content-between">
          <h6 class="mb-1">${(r.alert_type || "event").toUpperCase()} • ${r.channel || "—"}</h6>
          <small class="history-meta">${when}</small>
        </div>
        <div class="mb-1">
          <div class="small text-muted">Sensor</div>
          <div>${r.sensor || "—"} <span class="ms-2 small text-muted">value:</span> <strong>${r.sensor_val || "—"}</strong></div>
        </div>
        <div class="d-flex align-items-center gap-2">
          ${statusBadge}
          ${r.error_text ? `<small class="text-danger-emphasis">${r.error_text}</small>` : ""}
        </div>
      `;
      list.appendChild(item);
    }
  } catch {
    const list = $("#historyList");
    if (list) {
      list.innerHTML = `<div class="text-muted">Unable to load history.</div>`;
    }
  }
}

function wireHistoryDrawer() {
  const drawer = document.getElementById("historyDrawer");
  if (!drawer || drawer.dataset.bound) return;
  drawer.dataset.bound = "1";

  drawer.addEventListener("shown.bs.offcanvas", () => {
    refreshHistory();
  });

  const btn = $("#btnRefreshHistory");
  if (btn && !btn.dataset.bound) {
    btn.dataset.bound = "1";
    btn.addEventListener("click", refreshHistory);
  }
}

// === AUDIO ===
async function loadAudioFiles() {
  return apiGet("/api/audio/files");
}
async function loadAudioSettings() {
  return apiGet("/api/audio/settings");
}
async function saveAudioSettings(body) {
  return apiPut("/api/audio/settings", body);
}
async function uploadAudioFile(file) {
  const fd = new FormData();
  fd.append("file", file);
  const r = await fetch("/api/audio/upload", { method: "POST", body: fd });
  if (!r.ok) throw new Error(await r.text());
  return r.json();
}

function populateAudioSelect(sel, files, current) {
  sel.innerHTML = "";
  const optNone = document.createElement("option");
  optNone.value = "";
  optNone.textContent = "— None —";
  sel.appendChild(optNone);

  for (const f of files) {
    const opt = document.createElement("option");
    opt.value = f.filename;
    opt.textContent = f.filename;
    if (current && f.filename === current) opt.selected = true;
    sel.appendChild(opt);
  }
}

async function refreshAudioUI() {
  const [files, settings] = await Promise.all([loadAudioFiles(), loadAudioSettings()]);
  populateAudioSelect($("#audioActivatedSel"), files, settings.solenoid_activated_audio || "");
  populateAudioSelect($("#audioDeactivatedSel"), files, settings.solenoid_deactivated_audio || "");

  console.log(settings)

  // stash urls by filename for fast lookup on preview
  const map = Object.fromEntries(files.map(f => [f.filename, f.url]));
  $("#audioActivatedSel").dataset.urlMap = JSON.stringify(map);
  $("#audioDeactivatedSel").dataset.urlMap = JSON.stringify(map);
  
  if (typeof settings.volume === "number") {
    const volEl = $("#audioVolume");
    const volLbl = $("#audioVolumeVal");
    if (volEl) volEl.value = settings.volume;
    if (volLbl) volLbl.textContent = `${settings.volume}%`;
  }
}

async function onSaveAudioSelections() {
  const body = {
    solenoid_activated_audio: $("#audioActivatedSel").value || null,
    solenoid_deactivated_audio: $("#audioDeactivatedSel").value || null,
    volume: $("#audioVolume") ? parseInt($("#audioVolume").value, 10) : null,
  };
  await saveAudioSettings(body);
  toast("Audio selections saved");
}

async function onUploadAudio() {
  const inp = $("#audioUpload");
  if (!inp.files || inp.files.length === 0) {
    toast("Choose a file to upload");
    return;
  }
  try {
    const file = inp.files[0];
    await uploadAudioFile(file);
    inp.value = "";
    await refreshAudioUI();
    toast("Audio uploaded");
  } catch (e) {
    toast("Upload failed: " + (e.message || e));
  }
}

function previewSelected(selId) {
  const sel = $(selId);
  const fn = sel.value;
  if (!fn) {
    toast("No file selected");
    return;
  }
  const map = JSON.parse(sel.dataset.urlMap || "{}");
  const url = map[fn];
  if (!url) {
    toast("File not found on server");
    return;
  }
  const player = $("#audioPlayer");
  player.pause();
  player.src = url;
  player.currentTime = 0;
  // Let the browser decide device/output—this plays locally for the user
  player.play().catch(() => toast("Browser blocked autoplay—click the play button again"));
}

// ---------- wire UI once DOM ready ----------
document.addEventListener("DOMContentLoaded", async () => {
  // Bootstrap Modal instance (must be created from element)
  const modalEl = document.getElementById("recipientModal");
  if (modalEl) {
    recipientModal = bootstrap.Modal.getOrCreateInstance(modalEl, {
      backdrop: "static",
      keyboard: false,
    });
  }

  const btnSaveToggles = $("#btnSaveToggles");
  if (btnSaveToggles && !btnSaveToggles.dataset.bound) {
    btnSaveToggles.dataset.bound = "1";
    btnSaveToggles.addEventListener("click", () => saveGlobalToggles().catch(e => toast(e.message || e)));
  }

  const btnTest = $("#btnTestNotifications");
  if (btnTest && !btnTest.dataset.bound) {
    btnTest.dataset.bound = "1";
    btnTest.addEventListener("click", testNotifications);
  }

  const btnSaveSmtp = $("#btnSaveSmtp");
  if (btnSaveSmtp && !btnSaveSmtp.dataset.bound) {
    btnSaveSmtp.dataset.bound = "1";
    btnSaveSmtp.addEventListener("click", () => saveSmtp().catch(e => toast(e.message || e)));
  }

  const btnSaveProvider = $("#btnSaveProvider");
  if (btnSaveProvider && !btnSaveProvider.dataset.bound) {
    btnSaveProvider.dataset.bound = "1";
    btnSaveProvider.addEventListener("click", () => saveProvider().catch(e => toast(e.message || e)));
  }

  const providerSelect = $("#telephonyProvider");
  if (providerSelect && !providerSelect.dataset.bound) {
    providerSelect.dataset.bound = "1";
    providerSelect.addEventListener("change", (e) => {
      const v = (e.target.value || "twilio").toString().toLowerCase();
      showProviderCredentials(v); // show immediately on change
    });
  }

  const btnSaveTwilio = $("#btnSaveTwilio");
  if (btnSaveTwilio && !btnSaveTwilio.dataset.bound) {
    btnSaveTwilio.dataset.bound = "1";
    btnSaveTwilio.addEventListener("click", () => saveTwilio().catch(e => toast(e.message || e)));
  }

  const btnSaveClickSend = $("#btnSaveClickSend");
  if (btnSaveClickSend && !btnSaveClickSend.dataset.bound) {
    btnSaveClickSend.dataset.bound = "1";
    btnSaveClickSend.addEventListener("click", () => saveClickSend().catch(e => toast(e.message || e)));
  }

  const btnSaveMqtt = $("#btnSaveMqtt");
  if (btnSaveMqtt && !btnSaveMqtt.dataset.bound) {
    btnSaveMqtt.dataset.bound = "1";
    btnSaveMqtt.addEventListener("click", () => saveMqtt().catch(e => toast(e.message || e)));
  }

  const btnAddRecipient = $("#btnAddRecipient");
  if (btnAddRecipient && !btnAddRecipient.dataset.bound) {
    btnAddRecipient.dataset.bound = "1";
    btnAddRecipient.addEventListener("click", () => openRecipientModal("add"));
  }

  const recipientSaveBtn = $("#recipientSaveBtn");
  if (recipientSaveBtn && !recipientSaveBtn.dataset.bound) {
    recipientSaveBtn.dataset.bound = "1";
    recipientSaveBtn.addEventListener("click", saveRecipient);
  }

  // AUDIO: buttons
  const btnSaveAudio = $("#btnSaveAudio");
  if (btnSaveAudio && !btnSaveAudio.dataset.bound) {
    btnSaveAudio.dataset.bound = "1";
    btnSaveAudio.addEventListener("click", () => onSaveAudioSelections().catch(e => toast(e.message || e)));
  }
  const btnUpload = $("#btnAudioUpload");
  if (btnUpload && !btnUpload.dataset.bound) {
    btnUpload.dataset.bound = "1";
    btnUpload.addEventListener("click", () => onUploadAudio());
  }
  const btnTestA = $("#btnTestAudioActivated");
  if (btnTestA && !btnTestA.dataset.bound) {
    btnTestA.dataset.bound = "1";
    btnTestA.addEventListener("click", () => previewSelected("#audioActivatedSel"));
  }
  const btnTestD = $("#btnTestAudioDeactivated");
  if (btnTestD && !btnTestD.dataset.bound) {
    btnTestD.dataset.bound = "1";
    btnTestD.addEventListener("click", () => previewSelected("#audioDeactivatedSel"));
  }

  const vol = $("#audioVolume");
  if (vol && !vol.dataset.bound) {
    vol.dataset.bound = "1";
    vol.addEventListener("input", () => {
      $("#audioVolumeVal").textContent = `${vol.value}%`;
    });
  }

  wireHistoryDrawer();

  // Initial loads
  try {
    await loadSettingsIntoForm();
  } catch (e) {
    toast("Failed to load settings: " + (e.message || e));
  }

  try {
    await refreshRecipientsUI();
  } catch (e) {
    toast("Failed to load recipients: " + (e.message || e));
  }

  try {
    await refreshAudioUI();
  } catch (e) {
    toast("Failed to load audio settings: " + (e.message || e));
  }

  refreshHealth();
  setInterval(refreshHealth, 5000);
});
