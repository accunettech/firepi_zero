let rois = null; // {lcd_rois:{lcd1:{x1,y1,x2,y2},...}, led_rois:{...}, seg_threshold,lcd_inverted, led_red_thresh:{sat,val}, digit_count_per_lcd}
let drawing = false, startPt = null, curPt = null;

const img = $('#snapImg');
const canvas = $('#calibCanvas');
const ctx = canvas.getContext('2d');

function fitCanvas(){
  if (!img || !canvas || !ctx) return;

  // CSS size
  const cssW = img.clientWidth;
  const cssH = img.clientHeight;

  // Scale for device pixel ratio so lines are crisp
  const dpr = window.devicePixelRatio || 1;
  canvas.width  = Math.max(1, Math.floor(cssW * dpr));
  canvas.height = Math.max(1, Math.floor(cssH * dpr));
  canvas.style.width  = cssW + 'px';
  canvas.style.height = cssH + 'px';
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0); // scale drawing ops back to CSS pixels

  drawOverlay();
  try { hideProgress(); } catch(e){}
}
window.addEventListener('resize', fitCanvas);

function viewToNatural(x, y){
  const sx = img.naturalWidth / img.clientWidth;
  const sy = img.naturalHeight / img.clientHeight;
  return [Math.round(x * sx), Math.round(y * sy)];
}
function naturalToView(x, y){
  const sx = img.clientWidth / img.naturalWidth;
  const sy = img.clientHeight / img.naturalHeight;
  return [Math.round(x * sx), Math.round(y * sy)];
}

function drawRectView(x1,y1,x2,y2,color='#22c55e'){
  ctx.strokeStyle = color;
  ctx.lineWidth = 2;
  ctx.setLineDash([6,4]);
  ctx.strokeRect(Math.min(x1,x2), Math.min(y1,y2), Math.abs(x2-x1), Math.abs(y2-y1));
  ctx.setLineDash([]);
}
function drawOverlay(){
  if (!img || !img.complete || !rois || !ctx) {
    if (ctx) ctx.clearRect(0,0,canvas.width,canvas.height);
    return;
  }
  ctx.clearRect(0,0,canvas.width,canvas.height);

  // Draw existing rectangles
  const drawGroup = (group, color) => {
    for (const [k, r] of Object.entries(group||{})){
      if (!r) continue;
      const [x1,y1] = naturalToView(r.x1, r.y1);
      const [x2,y2] = naturalToView(r.x2, r.y2);
      drawRectView(x1,y1,x2,y2,color);
    }
  };
  drawGroup(rois.lcd_rois, '#60a5fa');
  drawGroup(rois.led_rois, '#f97316');

  // live drawing
  if (drawing && startPt && curPt) drawRectView(startPt.x,startPt.y,curPt.x,curPt.y,'#22c55e');
}

// --- mouse input ---
function getMousePos(evt){
  const rect = canvas.getBoundingClientRect();
  return { x: evt.clientX - rect.left, y: evt.clientY - rect.top };
}
canvas.addEventListener('mousedown', e => { drawing = true; startPt = getMousePos(e); curPt = {...startPt}; drawOverlay(); });
canvas.addEventListener('mousemove', e => { if(!drawing) return; curPt = getMousePos(e); drawOverlay(); });
canvas.addEventListener('mouseup', e => {
  if (!drawing) return;
  drawing = false;
  curPt = getMousePos(e);
  if (!rois) return;

  const sel = $('#roiTarget').value; // format "lcd:lcd1" or "led:opr_ctrl"
  const [grp, key] = sel.split(':');

  const [nx1, ny1] = viewToNatural(startPt.x, startPt.y);
  const [nx2, ny2] = viewToNatural(curPt.x, curPt.y);
  const rect = { x1: Math.min(nx1,nx2), y1: Math.min(ny1,ny2), x2: Math.max(nx1,nx2), y2: Math.max(ny1,ny2) };

  if (grp === 'lcd') rois.lcd_rois[key] = rect; else rois.led_rois[key] = rect;
  $('#curRect').textContent = `${rect.x1},${rect.y1} → ${rect.x2},${rect.y2}`;
  drawOverlay();
  try { hideProgress(); } catch(e){}
});

function bindControls(){
  $('#segThr').value = rois.seg_threshold ?? 0.55;
  $('#segThrVal').textContent = $('#segThr').value;
  $('#digitCount').value = rois.digit_count_per_lcd ?? 4;
  $('#lcdInverted').checked = !!rois.lcd_inverted;
  $('#ledSat').value = (rois.led_red_thresh && rois.led_red_thresh.sat) ?? 110;
  $('#ledVal').value = (rois.led_red_thresh && rois.led_red_thresh.val) ?? 120;

  $('#segThr').addEventListener('input', e => { rois.seg_threshold = parseFloat(e.target.value); $('#segThrVal').textContent = e.target.value; });
  $('#digitCount').addEventListener('input', e => { rois.digit_count_per_lcd = Math.max(1, Math.min(6, parseInt(e.target.value||'4',10))); });
  $('#lcdInverted').addEventListener('change', e => { rois.lcd_inverted = !!e.target.checked; });
  $('#ledSat').addEventListener('input', e => { rois.led_red_thresh = rois.led_red_thresh||{}; rois.led_red_thresh.sat = parseInt(e.target.value||'110',10); });
  $('#ledVal').addEventListener('input', e => { rois.led_red_thresh = rois.led_red_thresh||{}; rois.led_red_thresh.val = parseInt(e.target.value||'120',10); });
}

async function loadSnapshot(){
  try { showProgress('Fetching snapshot', 'Capturing current frame...'); } catch(e){}
  try{
    const r = await fetch('/api/panel/snapshot?cb=' + Date.now());
    if (!r.ok) throw new Error(await r.text());
    const blob = await r.blob();
    const url = URL.createObjectURL(blob);
    img.onload = () => {
      URL.revokeObjectURL(url);
      $('#snapMeta').textContent = `${img.naturalWidth}×${img.naturalHeight}`;
      fitCanvas();
      drawOverlay();
    };
    img.src = url;
  }catch(e){
    $('#calibError')?.classList.remove('d-none');
  } finally {
    try { hideProgress(); } catch(e){}
  }
}

async function loadRois(){
  const j = await apiGet('/api/panel/rois');
  rois = {
    lcd_rois: j.lcd_rois || {},
    led_rois: j.led_rois || {},
    seg_threshold: j.seg_threshold ?? 0.55,
    digit_count_per_lcd: j.digit_count_per_lcd ?? 4,
    lcd_inverted: !!j.lcd_inverted,
    led_red_thresh: j.led_red_thresh || {sat:110,val:120}
  };
  bindControls();
  drawOverlay();
  try { hideProgress(); } catch(e){}
}

async function saveRois(){
  // Basic validation
  const okRect = (r) => r && Number.isInteger(r.x1) && Number.isInteger(r.y1) && Number.isInteger(r.x2) && Number.isInteger(r.y2);
  for (const [k,v] of Object.entries(rois.lcd_rois)) if (!okRect(v)) throw new Error(`Bad rect for ${k}`);
  for (const [k,v] of Object.entries(rois.led_rois)) if (!okRect(v)) throw new Error(`Bad rect for ${k}`);

  const body = {
    lcd_rois: rois.lcd_rois,
    led_rois: rois.led_rois,
    seg_threshold: rois.seg_threshold,
    digit_count_per_lcd: rois.digit_count_per_lcd,
    lcd_inverted: !!rois.lcd_inverted,
    led_red_thresh: { sat: parseInt(rois.led_red_thresh.sat||110,10), val: parseInt(rois.led_red_thresh.val||120,10) },
    roi_ref_size: { w: img.naturalWidth, h: img.naturalHeight }
  };
  await apiPost('/api/panel/rois', body);
  toast('ROIs saved');
}

document.addEventListener('DOMContentLoaded', async () => {
  $('#btnSnapshot')?.addEventListener('click', loadSnapshot);

  const btnClear = $('#btnClearRois');
  btnClear?.addEventListener('click', () => {
    if (!rois) rois = { lcd_rois: {}, led_rois: {} };
    else { rois.lcd_rois = {}; rois.led_rois = {}; }
    const cr = $('#curRect'); if (cr) cr.textContent = '—';
    drawOverlay();
    if (typeof toast === 'function') toast('Cleared ROIs (not yet saved)');
  });

  const btnSave = $('#btnSaveRois');
  btnSave?.addEventListener('click', async ()=>{
    const html = btnSave.innerHTML;
    btnSave.disabled = true; btnSave.innerHTML = `<span class="spinner-border spinner-border-sm me-1"></span>Saving…`;
    try { await saveRois(); } catch(e){ toast('Save failed: '+(e.message||e)); }
    finally { btnSave.disabled = false; btnSave.innerHTML = html; }
  });

  const btnReload = $('#btnReloadWorker');
  btnReload?.addEventListener('click', async ()=>{
    const html = btnReload.innerHTML;
    btnReload.disabled = true; btnReload.innerHTML = `<span class="spinner-border spinner-border-sm me-1"></span>Reloading…`;
    try { 
      showProgress('Reloading worker','Applying ROI settings…'); 
      await apiPost('/api/panel/reload', {}); 
      toast('Worker reloaded'); 
    } catch(e) { 
      toast('Reload failed'); 
    } finally { 
      try{ hideProgress(); }catch(e){}
      btnReload.disabled=false; btnReload.innerHTML = html; 
    }
  });

  try {
    await loadRois();
    await loadSnapshot();
  } catch (e){
    $('#calibError')?.classList.remove('d-none');
  }

  // ---------- Dry-Run upload & decode (merged) ----------
  const dryFile = document.getElementById('dryRunFile');
  const btnUseUploaded = document.getElementById('btnUseUploaded');
  const btnDryRun = document.getElementById('btnDryRunDecode');
  const imgPreview = document.getElementById('dryRunPreview');

  let uploadedFileBlob = null;

  const tryShowProgress = (m1, m2) => { try { showProgress(m1, m2); } catch(_){} };
  const tryHideProgress  = () => { try { hideProgress(); } catch(_){} };
  const toastOk   = (m) => { try { toast(m); } catch(_) { console.log(m); } };
  const toastErr  = (m) => { try { errorToast(m); } catch(_) { console.error(m); } };

  if (dryFile) {
    dryFile.addEventListener('change', (e) => {
      uploadedFileBlob = e.target.files && e.target.files[0] ? e.target.files[0] : null;
      if (btnUseUploaded) btnUseUploaded.disabled = !uploadedFileBlob;
      if (btnDryRun) btnDryRun.disabled = !uploadedFileBlob;

      if (uploadedFileBlob && imgPreview) {
        const url = URL.createObjectURL(uploadedFileBlob);
        imgPreview.src = url;
        imgPreview.classList.remove('d-none');
      }
    });
  }

  async function blobToDataURL(blob) {
    return await new Promise((resolve, reject) => {
      const fr = new FileReader();
      fr.onerror = () => reject(new Error('read failed'));
      fr.onload = () => resolve(fr.result);
      fr.readAsDataURL(blob);
    });
  }

  // Loads uploaded image into the same snapshot <img> so you can draw ROIs and save them
  async function loadImageIntoCalibrationCanvas(dataUrl) {
    return await new Promise((resolve, reject) => {
      img.onload = () => {
        $('#snapMeta').textContent = `${img.naturalWidth}×${img.naturalHeight}`;
        fitCanvas();
        drawOverlay();
        // Let any ROI tooling know a new snapshot has loaded
        document.dispatchEvent(new CustomEvent('firepi:snapshotLoaded', {
          detail: { width: img.naturalWidth, height: img.naturalHeight, source: 'uploaded' }
        }));
        resolve();
      };
      img.onerror = () => reject(new Error('image load failed'));
      img.src = dataUrl;
    });
  }

  btnUseUploaded?.addEventListener('click', async () => {
    if (!uploadedFileBlob) return;
    tryShowProgress('Loading uploaded image…');
    try {
      const dataUrl = await blobToDataURL(uploadedFileBlob);
      await loadImageIntoCalibrationCanvas(dataUrl);
      toastOk('Loaded uploaded image into canvas.');
    } catch (e) {
      console.error(e);
      toastErr('Could not load image into canvas.');
    } finally {
      tryHideProgress();
    }
  });

  // Helpers to render results to either text or seven-seg mimic if present
  const setText = (sel, txt) => { const el = document.querySelector(sel); if (el) el.textContent = txt; };
  function renderSevenSegIfPresent(idx, val) {
    const el = document.getElementById(`testLcd${idx}`); // legacy seven-seg container
    if (el && typeof renderSevenSeg === 'function') {
      renderSevenSeg(el, val || '????', 4);
    }
  }
  function setLedDual(idDry, idLegacy, on) {
    const e1 = document.getElementById(idDry);
    if (e1) {
      e1.classList.toggle('bg-success', !!on);
      e1.classList.toggle('bg-secondary', !on);
    }
    const e2 = document.getElementById(idLegacy); // legacy
    if (e2) e2.classList.toggle('on', !!on);
  }

  btnDryRun?.addEventListener('click', async () => {
    if (!uploadedFileBlob) return;
    tryShowProgress('Decoding uploaded snapshot…');
    try {
      const fd = new FormData();
      fd.append('image', uploadedFileBlob, uploadedFileBlob.name || 'upload.jpg');
      const res = await fetch('/api/panel/dry_run', { method: 'POST', body: fd });
      const j = await res.json().catch(() => ({}));
      if (!res.ok || j.error) throw new Error(j.error || 'Dry-run failed');
      console.log(j);

      const lcds = j.lcds || ["","","",""];
      // Text outputs (new)
      setText('#dry-lcd1', lcds[0] || '----');
      setText('#dry-lcd2', lcds[1] || '----');
      setText('#dry-lcd3', lcds[2] || '----');
      setText('#dry-lcd4', lcds[3] || '----');
      // Legacy seven-seg containers (if present)
      renderSevenSegIfPresent(1, lcds[0]);
      renderSevenSegIfPresent(2, lcds[1]);
      renderSevenSegIfPresent(3, lcds[2]);
      renderSevenSegIfPresent(4, lcds[3]);

      const leds = j.leds || {};
      setLedDual('dry-led-opr',       'test-led-opr_ctrl', !!leds.opr_ctrl);
      setLedDual('dry-led-interlck',  'test-led-interlck', !!leds.interlck);
      setLedDual('dry-led-ptfi',      'test-led-ptfi',     !!leds.ptfi);
      setLedDual('dry-led-flame',     'test-led-flame',    !!leds.flame);
      setLedDual('dry-led-alarm',     'test-led-alarm',    !!leds.alarm);

      toastOk('Dry-run decode complete.');
    } catch (e) {
      console.error(e);
      toastErr(e.message || 'Dry-run failed');
    } finally {
      tryHideProgress();
    }
  });

}); // end DOMContentLoaded


// ------- Seven-seg helpers you already had (kept intact) -------
const DIGIT_SEGMENTS = {
  "0":[1,1,1,1,1,1,0], "1":[0,1,1,0,0,0,0], "2":[1,1,0,1,1,0,1], "3":[1,1,1,1,0,0,1],
  "4":[0,1,1,0,0,1,1], "5":[1,0,1,1,0,1,1], "6":[1,0,1,1,1,1,1], "7":[1,1,1,0,0,0,0],
  "8":[1,1,1,1,1,1,1], "9":[1,1,1,1,0,1,1], "-":[0,0,0,1,0,0,0], " ":[0,0,0,0,0,0,0], "?":[0,0,0,0,0,0,0]
};
function buildDigit(ch){
  const on = DIGIT_SEGMENTS[ch] || DIGIT_SEGMENTS["?"];
  const d = document.createElement('div'); d.className = 'sevenseg-digit';
  ['a','b','c','d','e','f','g'].forEach((seg,i)=>{
    const s = document.createElement('span'); s.className = `seg seg-${seg} ${on[i]?'on':''}`;
    d.appendChild(s);
  });
  return d;
}
function renderSevenSeg(container, str, fixedDigits=4){
  const s = (str||'').toString().padStart(fixedDigits,' ');
  if (container.dataset.last === s) return;
  container.dataset.last = s;
  container.innerHTML = '';
  for (let i=0;i<fixedDigits;i++) container.appendChild(buildDigit(s[i]));
}
function setLed(id, on) {
  const el = document.getElementById(id);
  if (el) el.classList.toggle('on', !!on);
}

// ------- REMOVED old #testFile upload handler (replaced by dry-run controls) -------