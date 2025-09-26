// --- seven-seg rendering ---
const DIGIT_SEGMENTS = {
  "0":[1,1,1,1,1,1,0], "1":[0,1,1,0,0,0,0], "2":[1,1,0,1,1,0,1], "3":[1,1,1,1,0,0,1],
  "4":[0,1,1,0,0,1,1], "5":[1,0,1,1,0,1,1], "6":[1,0,1,1,1,1,1], "7":[1,1,1,0,0,0,0],
  "8":[1,1,1,1,1,1,1], "9":[1,1,1,1,0,1,1], "-":[0,0,0,1,0,0,0], " ":[0,0,0,0,0,0,0], "?":[0,0,0,0,0,0,0]
};

function buildDigit(ch){
  const on = DIGIT_SEGMENTS[ch] || DIGIT_SEGMENTS["?"];
  const d = document.createElement('div');
  d.className = 'sevenseg-digit';
  ['a','b','c','d','e','f','g'].forEach((seg,i)=>{
    const s = document.createElement('span');
    s.className = `seg seg-${seg} ${on[i] ? 'on' : ''}`;
    d.appendChild(s);
  });
  return d;
}

function renderSevenSeg(container, str, fixedDigits=4){
  if (!container) return;
  const s = (str || '').toString().padStart(fixedDigits, ' ');
  if (container.dataset.last === s) return;
  container.dataset.last = s;
  container.innerHTML = '';
  for (let i = 0; i < fixedDigits; i++){
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

    const errEl = document.getElementById('panelError');
    if (errEl) errEl.classList.add('d-none');

    const ts = data.ts ? new Date(data.ts * 1000) : new Date();
    const updEl = document.getElementById('panelUpdated');
    if (updEl) updEl.textContent = ts.toLocaleTimeString();

    renderSevenSeg(document.getElementById('lcd1'), (data.lcds && data.lcds[0]) || '????', 4);
    renderSevenSeg(document.getElementById('lcd2'), (data.lcds && data.lcds[1]) || '????', 4);
    renderSevenSeg(document.getElementById('lcd3'), (data.lcds && data.lcds[2]) || '????', 4);
    renderSevenSeg(document.getElementById('lcd4'), (data.lcds && data.lcds[3]) || '????', 4);

    const leds = data.leds || {};
    setLed('led-opr_ctrl',  !!leds['opr_ctrl']);
    setLed('led-interlck',  !!leds['interlck']);
    setLed('led-ptfi',      !!leds['ptfi']);
    setLed('led-flame',     !!leds['flame']);
    setLed('led-alarm',     !!leds['alarm']);
  } catch(e){
    const errEl = document.getElementById('panelError');
    if (errEl) errEl.classList.remove('d-none');
  }
}

document.addEventListener('DOMContentLoaded', ()=>{
  updatePanel();
  setInterval(updatePanel, 1000);

  const btn = document.getElementById('btnReloadRois');
  if (btn){
    btn.addEventListener('click', async ()=>{
      const html = btn.innerHTML;
      btn.disabled = true;
      btn.innerHTML = `<span class="spinner-border spinner-border-sm me-1"></span>Reloading…`;
      try {
        await apiPost('/api/panel/reload', {});
        toast('ROIs reloaded');
      } catch {
        toast('Reload failed');
      } finally {
        btn.disabled = false;
        btn.innerHTML = html;
      }
    });
  }
});