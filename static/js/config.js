let recipientModal;            // bootstrap.Modal instance
let editingRecipientId = null; // null => adding, number => editing

function showProviderCredentials(which) {
  const value = (which || "").toString().toLowerCase();
  const tw = $("#twilioCard");
  const cs = $("#clicksendCard");
  if (value === "clicksend") {
    tw && tw.classList.add("d-none");
    cs && cs.classList.remove("d-none");
  } else {
    cs && cs.classList.add("d-none");
    tw && tw.classList.remove("d-none");
  }
}

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
  $("#mqttHost").value      = s.mqtt_host || "";
  $("#mqttUser").value      = s.mqtt_user || "";
  $("#mqttPassword").value  = s.mqtt_password || "";
  $("#mqttTopicBase").value = s.mqtt_topic_base || "";
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
  showProviderCredentials(provider); // immediate
  await apiPut("/api/settings", { telephony_provider: provider });
  try { await loadSettingsIntoForm(); } catch {}
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
    tr.innerHTML = `
      <td>${r.name || ""}</td>
      <td>${r.phone || ""}</td>
      <td>${r.email || ""}</td>
      <td>${r.receive_sms ? "Yes" : "No"}</td>
      <td class="text-end">
        <button class="btn btn-outline-light btn-sm me-2" data-action="edit" data-id="${r.id}">
          <i class="bi bi-pencil"></i>
        </button>
        <button class="btn btn-outline-light btn-sm" data-action="delete" data-id="${r.id}">
          <i class="bi bi-trash"></i>
        </button>
      </td>`;
    tbody.appendChild(tr);
  }

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
  renderRecipientsTable(await fetchRecipients());
}
function openRecipientModal(mode = "add", rec = null) {
  editingRecipientId = mode === "edit" && rec ? rec.id : null;
  $("#recipientModalTitle").textContent = editingRecipientId ? "Edit Recipient" : "Add Recipient";
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
  if (!payload.name) return toast("Name is required");

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

async function loadAudioFiles()  { return apiGet("/api/audio/files"); }
async function loadAudioSettings(){ return apiGet("/api/audio/settings"); }
async function saveAudioSettings(body){ return apiPut("/api/audio/settings", body); }
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

  const map = Object.fromEntries(files.map(f => [f.filename, f.url]));
  $("#audioActivatedSel").dataset.urlMap = JSON.stringify(map);
  $("#audioDeactivatedSel").dataset.urlMap = JSON.stringify(map);

  if (typeof settings.volume === "number") {
    $("#audioVolume").value = settings.volume;
    $("#audioVolumeVal").textContent = `${settings.volume}%`;
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
  if (!inp.files || inp.files.length === 0) return toast("Choose a file to upload");
  try {
    await uploadAudioFile(inp.files[0]);
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
  if (!fn) return toast("No file selected");
  const map = JSON.parse(sel.dataset.urlMap || "{}");
  const url = map[fn];
  if (!url) return toast("File not found on server");
  const player = $("#audioPlayer");
  player.pause();
  player.src = url;
  player.currentTime = 0;
  player.play().catch(() => toast("Browser blocked autoplay—click the play button again"));
}

document.addEventListener("DOMContentLoaded", async () => {
  const modalEl = document.getElementById("recipientModal");
  if (modalEl) {
    recipientModal = bootstrap.Modal.getOrCreateInstance(modalEl, {
      backdrop: "static",
      keyboard: false,
    });
  }

  $("#btnSaveToggles")?.addEventListener("click", () => saveGlobalToggles().catch(e => toast(e.message || e)));
  $("#btnTestNotifications")?.addEventListener("click", testNotifications);
  $("#btnSaveSmtp")?.addEventListener("click", () => saveSmtp().catch(e => toast(e.message || e)));
  $("#btnSaveProvider")?.addEventListener("click", () => saveProvider().catch(e => toast(e.message || e)));
  $("#telephonyProvider")?.addEventListener("change", (e) => showProviderCredentials((e.target.value || "twilio")));
  $("#btnSaveTwilio")?.addEventListener("click", () => saveTwilio().catch(e => toast(e.message || e)));
  $("#btnSaveClickSend")?.addEventListener("click", () => saveClickSend().catch(e => toast(e.message || e)));
  $("#btnSaveMqtt")?.addEventListener("click", () => saveMqtt().catch(e => toast(e.message || e)));

  $("#btnAddRecipient")?.addEventListener("click", () => openRecipientModal("add"));
  $("#recipientSaveBtn")?.addEventListener("click", saveRecipient);

  $("#btnSaveAudio")?.addEventListener("click", () => onSaveAudioSelections().catch(e => toast(e.message || e)));
  $("#btnAudioUpload")?.addEventListener("click", () => onUploadAudio());
  $("#btnTestAudioActivated")?.addEventListener("click", () => previewSelected("#audioActivatedSel"));
  $("#btnTestAudioDeactivated")?.addEventListener("click", () => previewSelected("#audioDeactivatedSel"));

  const vol = $("#audioVolume");
  vol?.addEventListener("input", () => { $("#audioVolumeVal").textContent = `${vol.value}%`; });

  // Initial loads
  try { await loadSettingsIntoForm(); } catch (e) { toast("Failed to load settings: " + (e.message || e)); }
  try { await refreshRecipientsUI(); } catch (e) { toast("Failed to load recipients: " + (e.message || e)); }
  try { await refreshAudioUI(); } catch (e) { toast("Failed to load audio settings: " + (e.message || e)); }
});