from __future__ import annotations
import logging, time
from typing import Optional
from gpiozero import Button
from db import log_alert_history, load_settings_dict
from .notification import (
    play_audio_pwm_async,
    send_email,
    provider_call_out,
    provider_send_sms,
)
from .mqtt_pub import publish_event

try:
    from flask import Flask  # for type hint only
except Exception:
    Flask = object  # type: ignore


class SolenoidMonitor:
    """
    RIB dry contact:
      GPIO25 <- Orange (NO)
      GND    <- Yellow (COM)

    - Uses internal pull-up on GPIO25 (HIGH=open=OFF, LOW=closed=ON)
    - No internal worker thread; gpiozero handles edges.
    """

    def __init__(
        self,
        app: Optional["Flask"] = None,
        pin: int = 25,
        bounce_time: float = 0.05,
        off_delay_s: float = 2.0,
        min_alert_interval_s: int = 10,
    ):
        self.app = app
        self.pin = pin
        self.bounce_time = bounce_time
        self.off_delay_s = off_delay_s
        self.min_alert_interval_s = min_alert_interval_s

        self._btn: Optional[Button] = None
        self.started = False

        self._boot_ts = 0.0
        self._last_change_ts = 0.0  # epoch seconds

        self._log = (app.logger if app and hasattr(app, "logger") else logging.getLogger(__name__))

        self._last_state: Optional[str] = None  # "ON"/"OFF"
        self._last_alert_ts: float = 0.0

    # ---------- public API ----------
    def external_alert(self, sensor: str, sensor_description: str, sensor_val: str, alert_text: str, force: bool = False):
        # Other modules can trigger the same alert path
        with self.app.app_context():
            cfg = load_settings_dict()
        self._send_alert_sequence(cfg, sensor, sensor_description, sensor_val, alert_text, force)

    def start(self):
        if self.started:
            return

        try:
            self._btn = Button(self.pin, pull_up=True, bounce_time=self.bounce_time)
        except Exception as e:
            self._log.exception("Failed to init GPIO Button on pin %s: %s", self.pin, e)
            raise

        # Baseline current state BEFORE wiring callbacks
        try:
            cur_pressed = bool(self._btn.is_pressed)
        except Exception:
            cur_pressed = False

        self._last_state = "ON" if cur_pressed else "OFF"
        self._last_change_ts = time.time()
        self._boot_ts = time.time()
        self._log.info("Fuel solenoid initial state: %s", self._last_state)

        # Publish initial state for UI/consumers
        try:
            cfg = self._load_cfg()
            publish_event(cfg.get("mqtt") or {}, sensor="fuel_solenoid", value="APP_STARTUP_STATE:" + self._last_state, logger=self._log)
        except Exception:
            self._log.exception("Failed to publish initial MQTT state")

        # Now wire callbacks
        self._btn.when_pressed  = self._on_change   # closed -> ON
        self._btn.when_released = self._on_change   # open   -> OFF
        self.started = True
        self._log.info("Solenoid Monitor started on GPIO %s", self.pin)
        if cur_pressed:
            play_audio_pwm_async("startup_solenoid_activated.mp3", is_stock_audio=True, logger=self._log)
        else:
            play_audio_pwm_async("startup_solenoid_deactivated.mp3", is_stock_audio=True, logger=self._log)

    def stop(self):
        play_audio_pwm_async("shutdown.mp3", is_stock_audio=True, logger=self._log)
        try:
            if self._btn:
                self._btn.close()
        except Exception:
            pass
        self.started = False
        self._log.info("Solenoid Monitor stopped.")

    def health(self) -> dict:
        now = time.time()
        return {
            "status": "ok",
            "uptime_s": int(now - (self._boot_ts or now)),
            "pin": self.pin,
            "state": self._last_state,
            "last_change_ts": self._last_change_ts or None,
            "last_alert_ts": self._last_alert_ts or None,
            "rate_limit_remaining_s": max(
                0, int(self.min_alert_interval_s - (now - self._last_alert_ts))
            ) if self._last_alert_ts else 0,
        }

    # ---------- internals ----------
    def _log_alert_history(
        self,
        alert_type: str,
        sensor: str,
        sensor_val: str,
        channel: str,
        status: str,
        error_text: str | None = None,
        payload: dict | None = None,
    ):
        try:
            if self.app is not None:
                with self.app.app_context():
                    log_alert_history(
                        alert_type=alert_type,
                        sensor=sensor,
                        sensor_val=sensor_val,
                        channel=channel,
                        status=status,
                        error_text=error_text,
                        payload=payload,
                    )
            else:
                log_alert_history(
                    alert_type=alert_type,
                    sensor=sensor,
                    sensor_val=sensor_val,
                    channel=channel,
                    status=status,
                    error_text=error_text,
                    payload=payload,
                )
        except Exception:
            self._log.warning("Alert history write failed.", exc_info=True)

    def _load_cfg(self) -> dict:
        try:
            if self.app is not None:
                with self.app.app_context():
                    return load_settings_dict()
            return load_settings_dict()
        except Exception as e:
            self._log.warning("Config load failed; using defaults. %s", e, exc_info=True)
            return {}

    def _on_change(self):
        sensor = "fuel_solenoid"
        sensor_description = "Fuel solenoid"
        try:
            state = "ON" if (self._btn and self._btn.is_pressed) else "OFF"
            if state == self._last_state:
                return

            prev = self._last_state
            self._last_state = state
            self._last_change_ts = time.time()
            self._log.info("%s state %s -> %s", sensor_description, prev, state)

            cfg = self._load_cfg()
            publish_event(cfg.get("mqtt") or {}, sensor=sensor, value=state, logger=self._log)
            self._handle_state_change(cfg, sensor=sensor, sensor_description=sensor_description, state=state)

        except Exception as e:
            self._log.exception("Error handling GPIO change: %s", e)

    def _handle_state_change(self, cfg: dict, sensor: str, sensor_description: str, state: str):
        self._log.info(f"{sensor} ({sensor_description}) is now {state}")

        if state == "ON":
            # Optional activation sound (non-blocking)
            if cfg.get("enable_speaker_alert"):
                try:
                    play_audio_pwm_async(cfg.get("solenoid_activated_audio"), logger=self._log)
                    self._log.info("[SPEAKER] activation sound queued")
                    self._log_alert_history("Notification", sensor, state, "speaker", "success")
                except Exception as e:
                    self._log_alert_history("Notification", sensor, state, "speaker", "error", str(e))
            return

        self._send_alert_sequence(
            cfg,
            sensor=sensor,
            sensor_description=sensor_description,
            sensor_val=state,
            alert_text="ALARM: Gas solenoid de-energized (burner down)",
            force=False,
        )

    def _send_alert_sequence(self, cfg: dict, sensor: str, sensor_description: str, sensor_val: str, alert_text: str, force: bool = False):
        now = time.time()
        if self._last_alert_ts and (now - self._last_alert_ts) < self.min_alert_interval_s:
            self._log.info(
                "Alert suppressed by rate-limit (%.0fs remaining)",
                self.min_alert_interval_s - (now - self._last_alert_ts),
            )
            return

        # OFF debounce (optional)
        if not force and (sensor_val == "OFF" and self.off_delay_s > 0):
            time.sleep(self.off_delay_s)
            try:
                curr = "ON" if (self._btn and self._btn.is_pressed) else "OFF"
                if curr != "OFF":
                    self._log.info("OFF debounce failed (now %s); aborting alert.", curr)
                    return
            except Exception:
                pass

        self._last_alert_ts = now

        # Speaker (non-blocking)
        if cfg.get("enable_speaker_alert"):
            try:
                play_audio_pwm_async(cfg.get("solenoid_deactivated_audio"), logger=self._log)
                self._log.info("[SPEAKER] queued")
                self._log_alert_history("Notification", sensor, sensor_val, "speaker", "success")
            except Exception as e:
                self._log.info(f"[SPEAKER] FAILED: {e}")
                self._log_alert_history("Notification", sensor, sensor_val, "speaker", "error", str(e))
        else:
            self._log.info("[SPEAKER] disabled. Skipping")
            self._log_alert_history("Notification", sensor, sensor_val, "speaker", "skipped")

        recipients = cfg.get("recipients") or []

        # PHONE
        if cfg.get("enable_phone_alert"):
            try:
                call_res = provider_call_out(cfg, message=alert_text, recipients=recipients)
                prov_log = (cfg.get("telephony_provider") or cfg.get("provider") or "twilio")
                self._log.info("[PHONE] processed with provider %s: %s", prov_log, call_res)
                self._log_alert_history("Notification", sensor, sensor_val, "phone", "success")
            except Exception as e:
                self._log.info(f"[PHONE] FAILED: {e}")
                self._log_alert_history("Notification", sensor, sensor_val, "phone", "error", str(e))
        else:
            self._log.info("[PHONE] disabled. Skipping")
            self._log_alert_history("Notification", sensor, sensor_val, "phone", "skipped")

        # EMAIL
        if cfg.get("enable_email_alert"):
            try:
                email_res = send_email(cfg.get("smtp") or {}, recipients)
                self._log.info("[EMAIL] processed: %s", email_res)
                self._log_alert_history("Notification", sensor, sensor_val, "email", "success")
            except Exception as e:
                self._log.info(f"[EMAIL] FAILED: {e}")
                self._log_alert_history("Notification", sensor, sensor_val, "email", "error", str(e))
        else:
            self._log.info("[EMAIL] disabled. Skipping")
            self._log_alert_history("Notification", sensor, sensor_val, "email", "skipped")

        # SMS
        if cfg.get("enable_sms_alert"):
            try:
                sms_res = provider_send_sms(cfg, body=alert_text, recipients=recipients)
                self._log.info("[SMS] processed: %s", sms_res)
                self._log_alert_history("Notification", sensor, sensor_val, "sms", "success")
            except Exception as e:
                self._log.info(f"[SMS] FAILED: {e}")
                self._log_alert_history("Notification", sensor, sensor_val, "sms", "error", str(e))
        else:
            self._log.info("[SMS] disabled. Skipping")
            self._log_alert_history("Notification", sensor, sensor_val, "sms", "skipped")

    # -------- Public test trigger --------
    def test_alerts(self, message: str | None = None) -> None:
        cfg = self._load_cfg()
        if message:
            try:
                if "smtp" in cfg and isinstance(cfg["smtp"], dict):
                    cfg["smtp"] = dict(cfg["smtp"], notify_text=message)
                if "twilio" in cfg and isinstance(cfg["twilio"], dict):
                    cfg["twilio"] = dict(cfg["twilio"], notify_text=message)
                if "clicksend" in cfg and isinstance(cfg["clicksend"], dict):
                    cfg["clicksend"] = dict(cfg["clicksend"], notify_text=message)
            except Exception:
                pass

        self._send_alert_sequence(
            cfg,
            sensor="TEST_SENSOR",
            sensor_description="Test Trigger",
            sensor_val="OFF",
            alert_text=message or "TEST: Furnace alert system check",
            force=True,
        )