from __future__ import annotations
import os, time, threading
from pathlib import Path
from typing import Optional
from PIL import Image

try:
    from picamera2 import Picamera2
    _PICAM = True
except Exception:
    _PICAM = False

class PanelSnapshot:
    def __init__(
        self,
        app,
        *,
        interval: float = 5.0,
        width: int = 800,
        height: int = 450,
        jpeg_quality: int = 80,
        warmup_s: float = 0.2,
    ):
        self.app = app
        self.interval = float(os.environ.get("FIREPI_SNAPSHOT_SEC", interval))
        self.w, self.h = int(width), int(height)
        self.jpeg_quality = int(jpeg_quality)
        self.warmup_s = float(warmup_s)

        self.dst = Path(app.instance_path) / "snapshot.jpg"
        self.dst.parent.mkdir(parents=True, exist_ok=True)
        if not self.dst.exists():
            self._write_placeholder(self.dst)

        self._stop = threading.Event()
        self._t = threading.Thread(target=self._run, name="PanelSnapshot", daemon=True)
        self.started = False
        self._picam: Optional[Picamera2] = None
        self._still_cfg = None
        self._snapshot_version = 0
        app.extensions["snapshot_get_version"] = lambda: self._snapshot_version

        if _PICAM:
            try:
                self._picam = Picamera2()
                # IMPORTANT: Use RGB888 (not BGR) so colors are correct.
                self._still_cfg = self._picam.create_still_configuration(
                    main={"size": (self.w, self.h), "format": "RGB888"},
                    buffer_count=1,
                )
                app.logger.info("[PanelSnapshot] Picamera2 ready for %dx%d stills", self.w, self.h)
            except Exception as e:
                self._picam = None
                self._still_cfg = None
                app.logger.warning("[PanelSnapshot] Picamera2 init error: %s", e)
        else:
            app.logger.info("[PanelSnapshot] Picamera2 not available; using placeholder frames")

    def start(self):
        if self.started:
            return
        self._t.start()
        self.started = True

    def stop(self):
        self._stop.set()
        try:
            self._t.join(timeout=2.0)
        except Exception:
            pass

        try:
            if self._picam is not None:
                self._picam.close()
        except Exception:
            pass
        self.started = False

    def _run(self):
        log = self.app.logger
        last_good = 0.0

        while not self._stop.is_set():
            t0 = time.time()
            tmp = self.dst.with_suffix(".tmp.jpg")
            wrote = False

            if self._picam is not None and self._still_cfg is not None:
                try:
                    self._picam.configure(self._still_cfg)
                    self._picam.start()
                    time.sleep(self.warmup_s)
                    self._picam.capture_file(str(tmp), name="main")
                    self._picam.stop()

                    if tmp.exists() and tmp.stat().st_size > 0:
                        tmp.replace(self.dst)
                        self._snapshot_version += 1
                        self.app.sse_hub.publish("snapshot", {"version": self._snapshot_version, "ts": int(time.time())})
                        wrote = True
                        last_good = time.time()
                except Exception as e:
                    try:
                        self._picam.stop()
                    except Exception:
                        pass
                    log.warning("[PanelSnapshot] capture failed: %s", e)

            if not wrote and (time.time() - last_good) > 60:
                self._write_placeholder(self.dst)

            dt = time.time() - t0
            time.sleep(max(0.1, self.interval - dt))

    def _write_placeholder(self, path: Path):
        img = Image.new("RGB", (self.w, self.h), (0, 0, 0))
        try:
            from PIL import ImageDraw, ImageFont
            draw = ImageDraw.Draw(img)
            text = "NO SIGNAL"
            try:
                font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", size=int(self.h * 0.10))
            except Exception:
                font = ImageFont.load_default()
            tw, th = draw.textsize(text, font=font)
            draw.text(((self.w - tw)//2, (self.h - th)//2), text, fill=(255, 0, 0), font=font)
        except Exception:
            pass

        tmp = path.with_suffix(".tmp.jpg")
        img.save(tmp, format="JPEG", quality=85, optimize=True)
        tmp.replace(path)