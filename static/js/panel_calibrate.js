// --- helpers ---
const $ = (s) => document.querySelector(s);
async function apiGet(url){ const r = await fetch(url); if(!r.ok) throw new Error(await r.text()); return r.json(); }
async function apiPost(url, body){ const r = await fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body||{})}); if(!r.ok) throw new Error(await r.text()); return r.json(); }
function toast(msg){
  const div = document.createElement('div'); div.className='position-fixed top-0 end-0 p-3'; div.style.zIndex=1080;
  div.innerHTML = `<div class="toast align-items-center text-bg-dark border-0 show"><div class="d-flex">
    <div class="toast-body">${msg}</div><button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast"></button></div></div>`;
  document.body.appendChild(div); setTimeout(()=>div.remove(),2500);
}

// --- state ---
let rois = null; // {lcd_rois:{lcd1:{x1,y1,x2,y2},...}, led_rois:{...}, seg_threshold,lcd_inverted, led_red_thresh:{sat,val}, digit_count_per_lcd}
let drawing = false, startPt = null, curPt = null;
let imgW=0, imgH=0;

// --- elements ---
const img = $('#snapImg');
const canvas = $('#calibCanvas');
const ctx = canvas.getContext('2d');

// --- sizing ---
function fitCanvas(){
  canvas.width = img.clientWidth;
  canvas.height = img.clientHeight;
  drawOverlay();
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

// --- overlay drawing ---
function drawRectView(x1,y1,x2,y2,color='#22c55e'){
  ctx.strokeStyle = color; ctx.lineWidth = 2; ctx.setLineDash([6,4]);
  ctx.strokeRect(Math.min(x1,x2), Math.min(y1,y2), Math.abs(x2-x1), Math.abs(y2-y1));
  ctx.setLineDash([]);
}
function drawOverlay(){
  if (!img.complete || !rois) { ctx.clearRect(0,0,canvas.width,canvas.height); return; }
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
  const rect = { x1: Math.min(nx1,nx2), y1: Math.min(ny1,ny2), x2: Math.max(nx1,nx2), y2: Math.max(ny2,ny1) };

  if (grp === 'lcd') rois.lcd_rois[key] = rect; else rois.led_rois[key] = rect;
  $('#curRect').textContent = `${rect.x1},${rect.y1} → ${rect.x2},${rect.y2}`;
  drawOverlay();
});

// --- UI bindings ---
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

// --- snapshot handling ---
async function loadSnapshot(){
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
    $('#calibError').classList.remove('d-none');
  }
}

// --- load/save ROIs ---
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
    led_red_thresh: { sat: parseInt(rois.led_red_thresh.sat||110,10), val: parseInt(rois.led_red_thresh.val||120,10) }
  };
  await apiPost('/api/panel/rois', body);
  toast('ROIs saved');
}

// --- buttons ---
document.addEventListener('DOMContentLoaded', async () => {
  $('#btnSnapshot').addEventListener('click', loadSnapshot);
  $('#btnSaveRois').addEventListener('click', async ()=>{
    const btn = $('#btnSaveRois'); const html = btn.innerHTML;
    btn.disabled = true; btn.innerHTML = `<span class="spinner-border spinner-border-sm me-1"></span>Saving…`;
    try { await saveRois(); } catch(e){ toast('Save failed: '+(e.message||e)); }
    finally { btn.disabled = false; btn.innerHTML = html; }
  });
  $('#btnReloadWorker').addEventListener('click', async ()=>{
    const btn = $('#btnReloadWorker'); const html = btn.innerHTML;
    btn.disabled = true; btn.innerHTML = `<span class="spinner-border spinner-border-sm me-1"></span>Reloading…`;
    try { await apiPost('/api/panel/reload', {}); toast('Worker reloaded'); } catch(e){ toast('Reload failed'); }
    finally { btn.disabled=false; btn.innerHTML = html; }
  });

  try {
    await loadRois();
    await loadSnapshot();
  } catch (e){
    $('#calibError').classList.remove('d-none');
  }
});

// --- reuse a tiny seven-seg renderer (same mapping as panel.js) ---
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

// --- dry-run: upload photo -> decode on server ---
document.addEventListener('DOMContentLoaded', () => {
  const fileInput = document.getElementById('testFile');
  if (!fileInput) return;

  fileInput.addEventListener('change', async (e) => {
    const f = e.target.files && e.target.files[0];
    if (!f) return;

    // Show the uploaded image in the snapshot area so you can draw ROIs on it
    const localUrl = URL.createObjectURL(f);
    img.onload = () => {
      URL.revokeObjectURL(localUrl);
      $('#snapMeta').textContent = `${img.naturalWidth}×${img.naturalHeight}`;
      fitCanvas();
      drawOverlay();
    };
    img.src = localUrl;

    const fd = new FormData();
    fd.append('image', f);

    try {
      const r = await fetch('/api/panel/debug/decode', { method: 'POST', body: fd });
      if (!r.ok) throw new Error(await r.text());
      const j = await r.json();

      // Seven-seg results
      renderSevenSeg(document.getElementById('testLcd1'), (j.lcds && j.lcds[0]) || '????', 4);
      renderSevenSeg(document.getElementById('testLcd2'), (j.lcds && j.lcds[1]) || '????', 4);
      renderSevenSeg(document.getElementById('testLcd3'), (j.lcds && j.lcds[2]) || '????', 4);
      renderSevenSeg(document.getElementById('testLcd4'), (j.lcds && j.lcds[3]) || '????', 4);

      // LEDs
      const leds = j.leds || {};
      setLed('test-led-opr_ctrl', !!leds['opr_ctrl']);
      setLed('test-led-interlck', !!leds['interlck']);
      setLed('test-led-ptfi', !!leds['ptfi']);
      setLed('test-led-flame', !!leds['flame']);
      setLed('test-led-alarm', !!leds['alarm']);

      // Annotated preview
      if (j.preview_jpeg_b64) {
        document.getElementById('testPreview').src = 'data:image/jpeg;base64,' + j.preview_jpeg_b64;
      }
      toast('Dry-run complete');
    } catch (e2) {
      toast('Dry-run failed: ' + (e2.message || e2));
    } finally {
      // allow picking the same file again
      e.target.value = '';
    }
  });
});
