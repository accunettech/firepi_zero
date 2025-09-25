// --- tiny helpers ---
const $ = (s) => document.querySelector(s);
async function apiGet(url){ const r = await fetch(url); if(!r.ok) throw new Error(await r.text()); return r.json(); }
async function apiPost(url, body){
  const r = await fetch(url, { method: 'POST', headers:{'Content-Type':'application/json'}, body: body?JSON.stringify(body):null });
  if(!r.ok) throw new Error(await r.text()); return r.json();
}

// --- toast (local to this page) ---
function toast(message){
  const div = document.createElement('div'); div.className = 'position-fixed top-0 end-0 p-3'; div.style.zIndex = 1080;
  div.innerHTML = `<div class="toast align-items-center text-bg-dark border-0 show">
      <div class="d-flex"><div class="toast-body">${message}</div>
      <button type="button" class="btn-close btn-close-white me-2 m-auto" data-bs-dismiss="toast"></button></div></div>`;
  document.body.appendChild(div); setTimeout(()=>div.remove(),2500);
}

// --- seven-seg rendering ---
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
  // If content is identical, skip reflow
  if (container.dataset.last === s) return;
  container.dataset.last = s;

  container.innerHTML = '';
  for (let i=0;i<fixedDigits;i++){
    container.appendChild(buildDigit(s[i]));
  }
}

// --- LED helpers ---
function setLed(id, on) {
  const el = document.getElementById(id);
  if (!el) return;
  el.classList.toggle('on', !!on);
}

// --- status polling ---
async function updatePanel(){
  try{
    const data = await apiGet('/api/panel/status'); // {lcds:[...], leds:{}, ts: ...}
    $('#panelError')?.classList.add('d-none');

    const ts = data.ts ? new Date(data.ts*1000) : new Date();
    $('#panelUpdated').textContent = ts.toLocaleTimeString();

    renderSevenSeg($('#lcd1'), (data.lcds && data.lcds[0]) || '????', 4);
    renderSevenSeg($('#lcd2'), (data.lcds && data.lcds[1]) || '????', 4);
    renderSevenSeg($('#lcd3'), (data.lcds && data.lcds[2]) || '????', 4);
    renderSevenSeg($('#lcd4'), (data.lcds && data.lcds[3]) || '????', 4);

    const leds = data.leds || {};
    setLed('led-opr_ctrl', !!leds['opr_ctrl']);
    setLed('led-interlck', !!leds['interlck']);
    setLed('led-ptfi', !!leds['ptfi']);
    setLed('led-flame', !!leds['flame']);
    setLed('led-alarm', !!leds['alarm']);
  }catch(e){
    $('#panelError')?.classList.remove('d-none');
  }
}

document.addEventListener('DOMContentLoaded', ()=>{
  // Initial draw
  updatePanel();
  // Poll every second
  setInterval(updatePanel, 1000);

  // Reload ROIs
  $('#btnReloadRois').addEventListener('click', async ()=>{
    const btn = $('#btnReloadRois');
    const html = btn.innerHTML;
    btn.disabled = true;
    btn.innerHTML = `<span class="spinner-border spinner-border-sm me-1"></span>Reloading…`;
    try {
      await apiPost('/api/panel/reload', {});
      toast('ROIs reloaded');
    } catch(e){
      toast('Reload failed');
    } finally {
      btn.disabled = false;
      btn.innerHTML = html;
    }
  });
});
