// static/js/panel_test.js
(() => {
  const $ = (sel) => document.querySelector(sel);

  // Elements
  const fileInput   = $("#fileInput");
  const targetSel   = $("#targetSelect");
  const img         = $("#imgBase");
  const canvas      = $("#roiCanvas");
  const ctx         = canvas.getContext("2d");
  const roiText     = $("#roiText");
  const resultText  = $("#resultText");

  const optDigits   = $("#optDigits");
  const optSegThr   = $("#optSegThr");
  const optLedSat   = $("#optLedSat");
  const optLedVal   = $("#optLedVal");
  const optInverted = $("#optInverted");

  const btnClearOne = $("#btnClearOne");
  const btnClearAll = $("#btnClearAll");
  const btnParse    = $("#btnParse");

  // State
  let scaleX = 1, scaleY = 1;         // canvas->image scale
  let drawing = false;
  let startPt = null;
  const rois = {};                    // { key: {x1,y1,x2,y2} } in CANVAS coords

  // Prevent native drag/ghost image & text selection issues
  img.draggable = false;
  img.style.pointerEvents = "none";
  img.addEventListener("dragstart", (e) => e.preventDefault());
  img.addEventListener("mousedown", (e) => e.preventDefault());

  // --- Utilities ---
  const clamp = (v, min, max) => Math.max(min, Math.min(max, v));

  function canvasPoint(evt) {
    const r = canvas.getBoundingClientRect();
    return {
      x: Math.round(evt.clientX - r.left),
      y: Math.round(evt.clientY - r.top),
    };
  }

  function fitCanvasToImage() {
    if (!img.naturalWidth || !img.naturalHeight) return;

    // Size canvas to the displayed image (client size)
    const w = img.clientWidth;
    const h = img.clientHeight;

    canvas.width = w;
    canvas.height = h;
    canvas.style.width  = w + "px";
    canvas.style.height = h + "px";
    canvas.classList.remove("d-none");
    img.classList.remove("d-none");

    // canvas->image scale
    scaleX = img.naturalWidth  / w;
    scaleY = img.naturalHeight / h;

    redraw();
    updateRoiText();
  }

  function drawOneBox(key, r, highlight=false) {
    const x = Math.min(r.x1, r.x2);
    const y = Math.min(r.y1, r.y2);
    const w = Math.abs(r.x2 - r.x1);
    const h = Math.abs(r.y2 - r.y1);

    ctx.save();
    ctx.lineWidth = 2;
    ctx.strokeStyle = highlight ? "rgba(251,191,36,0.95)" : "rgba(14,165,233,0.9)";
    ctx.fillStyle   = highlight ? "rgba(251,191,36,0.16)" : "rgba(14,165,233,0.12)";
    ctx.fillRect(x, y, w, h);
    ctx.strokeRect(x, y, w, h);

    // Size label
    const label = `${key}  ${w}x${h}px`;
    ctx.font = "12px ui-monospace, SFMono-Regular, Menlo, Consolas, monospace";
    const tw = Math.ceil(ctx.measureText(label).width) + 10;
    ctx.fillStyle = "rgba(0,0,0,0.55)";
    ctx.fillRect(x + 4, y + 4, tw, 18);
    ctx.fillStyle = "#fff";
    ctx.fillText(label, x + 8, y + 18);
    ctx.restore();
  }

  function redraw() {
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    // draw all saved boxes
    for (const [k, r] of Object.entries(rois)) {
      if (!r) continue;
      const isCurrent = (k === targetSel.value);
      drawOneBox(k, r, isCurrent);
    }
  }

  function roisCanvasToImage(roisCanvas) {
    const out = {};
    const W = img.naturalWidth;
    const H = img.naturalHeight;

    for (const [k, r] of Object.entries(roisCanvas)) {
      if (!r) continue;

      const x1 = Math.round(Math.min(r.x1, r.x2) * scaleX);
      const y1 = Math.round(Math.min(r.y1, r.y2) * scaleY);
      const x2 = Math.round(Math.max(r.x1, r.x2) * scaleX);
      const y2 = Math.round(Math.max(r.y1, r.y2) * scaleY);

      const rx1 = clamp(x1, 0, W);
      const ry1 = clamp(y1, 0, H);
      const rx2 = clamp(x2, 0, W);
      const ry2 = clamp(y2, 0, H);

      // drop extremely small boxes that will not parse correctly
      if ((rx2 - rx1) >= 8 && (ry2 - ry1) >= 12) {
        out[k] = { x1: rx1, y1: ry1, x2: rx2, y2: ry2 };
      }
    }
    return out;
  }

  function updateRoiText() {
    try {
      const imgRois = roisCanvasToImage(rois);
      roiText.textContent = Object.keys(imgRois).length
        ? JSON.stringify(imgRois, null, 2)
        : "(none)";
    } catch (e) {
      roiText.textContent = "(error preparing ROIs)";
    }
  }

  // --- Pointer events on CANVAS ONLY ---
  canvas.style.pointerEvents = "auto";
  canvas.addEventListener("pointerdown", (e) => {
    e.preventDefault();
    if (!img.naturalWidth) return;
    drawing = true;
    canvas.setPointerCapture?.(e.pointerId);
    const p = canvasPoint(e);
    const key = targetSel.value;
    rois[key] = { x1: p.x, y1: p.y, x2: p.x, y2: p.y };
    redraw();
    updateRoiText();
  }, { passive: false });

  canvas.addEventListener("pointermove", (e) => {
    if (!drawing) return;
    e.preventDefault();
    const p = canvasPoint(e);
    const key = targetSel.value;
    const r = rois[key];
    if (!r) return;
    r.x2 = clamp(p.x, 0, canvas.width);
    r.y2 = clamp(p.y, 0, canvas.height);
    redraw();
    updateRoiText();
  }, { passive: false });

  function endPointer(e) {
    if (!drawing) return;
    e && e.preventDefault?.();
    drawing = false;
    try { canvas.releasePointerCapture?.(e.pointerId); } catch {}
    redraw();
    updateRoiText();
  }
  canvas.addEventListener("pointerup", endPointer, { passive: false });
  canvas.addEventListener("pointerleave", endPointer, { passive: false });
  canvas.addEventListener("pointercancel", endPointer, { passive: false });

  // --- Buttons ---
  btnClearOne?.addEventListener("click", () => {
    const key = targetSel.value;
    if (rois[key]) delete rois[key];
    redraw();
    updateRoiText();
  });

  btnClearAll?.addEventListener("click", () => {
    for (const k of Object.keys(rois)) delete rois[k];
    redraw();
    updateRoiText();
  });

  btnParse?.addEventListener("click", async () => {
    if (!img.naturalWidth) {
      resultText.textContent = "Load an image first.";
      return;
    }

    const payload = {
      rois: roisCanvasToImage(rois),
      options: {
        digits: parseInt(optDigits.value || "4", 10),
        seg_threshold: parseFloat(optSegThr.value || "0.55"),
        lcd_inverted: !!optInverted.checked,
        led_sat: parseInt(optLedSat.value || "110", 10),
        led_val: parseInt(optLedVal.value || "120", 10),
      },
      image_data_url: img.src.startsWith("data:") ? img.src : null,
    };

    try {
      resultText.textContent = "Parsing…";
      const r = await fetch("/dev/panel-test/parse", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      const j = await r.json();
      resultText.textContent = JSON.stringify(j, null, 2);
    } catch (err) {
      resultText.textContent = `Parse failed: ${err}`;
    }
  });

  // When switching target, just redraw to highlight current one
  targetSel?.addEventListener("change", redraw);

  // --- File input ---
  fileInput?.addEventListener("change", (e) => {
    const f = e.target.files?.[0];
    if (!f) return;

    const reader = new FileReader();
    reader.onload = () => {
      // New image; clear previous ROIs to prevent confusion with different sizes
      for (const k of Object.keys(rois)) delete rois[k];
      resultText.textContent = "(none)";
      roiText.textContent = "(none)";
      img.src = reader.result;  // triggers img.onload -> fitCanvasToImage
    };
    reader.readAsDataURL(f);
  });

  // Recompute canvas size if layout changes
  window.addEventListener("resize", () => {
    if (img.src) fitCanvasToImage();
  });

  // If the page is loaded with an already-set src, initialize
  if (img.complete && img.naturalWidth) {
    fitCanvasToImage();
  } else {
    img.addEventListener("load", fitCanvasToImage);
  }
})();