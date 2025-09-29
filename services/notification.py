from __future__ import annotations
import logging
import threading
import shutil
import os
import subprocess as sp
from pathlib import Path
from datetime import datetime
from email.utils import parseaddr, formataddr, format_datetime
from email.mime.text import MIMEText
from html import escape
import smtplib

from services.audio import resolve_audio_path

# Single shared lock so overlapping playbacks don't fight
_SPEAKER_LOCK = threading.Lock()


# ---------- Helpers ----------
def _valid_email(addr: str) -> bool:
    name, email = parseaddr(addr or "")
    return bool(email and "@" in email and "." in email.split("@")[-1])


def _alsa_device_exists(name: str) -> bool:
    """
    Return True if an ALSA PCM with this name exists (per `aplay -L`).
    """
    try:
        aplay = shutil.which("aplay") or "/usr/bin/aplay"
        out = sp.check_output([aplay, "-L"], text=True, stderr=sp.DEVNULL)
        ids = [ln.split(":")[0].strip() for ln in out.splitlines() if ln and not ln.startswith(" ")]
        return name in ids
    except Exception:
        return False


def play_audio_pwm_async(audio_path: str, is_stock_audio: bool = False, logger=None, device_name: str | None = None) -> None:
    """
    Non-blocking playback using ALSA. Relies on your /etc/asound.conf 'default' routing.
    If `device_name` is provided and exists, it's used (e.g. 'default', 'softvol', 'plughw:0,0').
    """
    log = logger or logging.getLogger("firepi.audio")

    def _worker():
        # Don't trample another playback
        if not _SPEAKER_LOCK.acquire(blocking=False):
            log.info("Speaker busy; skipping playback")
            return
        try:
            p: Path | None = resolve_audio_path(audio_path, is_stock_audio=is_stock_audio)
            if not p:
                if is_stock_audio:
                    log.info("Stock audio file not found: %s", audio_path)
                else:
                    log.info("Audio file not found: %s", audio_path)
                return

            env = os.environ.copy()
            # keep it pure-ALSA
            env.pop("PULSE_SERVER", None)
            env.pop("XDG_RUNTIME_DIR", None)

            aplay  = shutil.which("aplay")  or "/usr/bin/aplay"
            mpg123 = shutil.which("mpg123") or "/usr/bin/mpg123"
            sox    = shutil.which("sox")    or "/usr/bin/sox"

            # Prefer sox pipeline (adds tiny fade, handles wav/mp3/ogg/…)
            if os.access(sox, os.X_OK) and os.access(aplay, os.X_OK):
                # sox -> wav to stdout with 20ms fade-in/out + headroom
                p1 = sp.Popen(
                    [sox, str(p), "-t", "wav", "-", "gain", "-h", "fade", "t", "0.02", "-0", "0.02"],
                    stdout=sp.PIPE, stderr=sp.DEVNULL, env=env
                )
                args = [aplay, "-q"]
                if device_name: args += ["-D", device_name]
                sp.Popen(args + ["-"], stdin=p1.stdout, stdout=sp.DEVNULL, stderr=sp.DEVNULL, env=env)
                if p1.stdout: p1.stdout.close()
                return

            # Fallback: native players (no fade)
            use_aplay = Path(p).suffix.lower() == ".wav" and os.access(aplay, os.X_OK)
            if use_aplay:
                args = [aplay, "-q"]
                if device_name: args += ["-D", device_name]
            else:
                # For mp3, skip first frame to avoid header tick if mpg123 used
                args = [mpg123, "-q", "-k", "1"] if os.access(mpg123, os.X_OK) else [aplay, "-q"]
                if device_name and args[0].endswith("mpg123"):
                    args += ["-a", device_name]
            args.append(str(p))

            # Capture stderr so we see ALSA errors if nothing plays
            log.info(f"Executing {args}")
            proc = sp.Popen(args, env=env, stdout=sp.PIPE, stderr=sp.PIPE, text=True)
            out, err = proc.communicate(timeout=180)
            if proc.returncode != 0:
                log.info("Audio player rc=%s args=%s stderr=%s", proc.returncode, args, (err or "").strip())
            else:
                log.info("Played audio: %s", p.name)
        except Exception as e:
            log.exception("Audio playback failed: %s", e)
        finally:
            _SPEAKER_LOCK.release()

    threading.Thread(target=_worker, name="audio-play", daemon=True).start()


# ---------- Email ----------
def send_email(config: dict, recipients: list[dict]) -> dict:
    """
    SMTP email fan-out (sequential). config keys:
      - server, port, username, password, notify_text
    recipients: list of {email, ...}
    """
    now = datetime.now().astimezone()
    subject = "Ervin Glassworks FirePi Notification"
    body = (config.get("notify_text") or "").strip()

    dest: list[str] = []
    for r in recipients or []:
        email = (r.get("email") or "").strip()
        if email and _valid_email(email) and email not in dest:
            dest.append(email)

    results = {"sent": [], "failed": {}}
    if not dest:
        return results

    host = (config.get("server") or "").strip()
    port = int(config.get("port") or 0)
    user = (config.get("username") or "").strip()
    pwd  = (config.get("password") or "").strip()
    if not host or not port or not user or not pwd:
        raise Exception("Failed to send email notifications. SMTP server/port/username/password not set!")

    server = None
    try:
        if port == 465:
            server = smtplib.SMTP_SSL(host, port, timeout=15)
        else:
            server = smtplib.SMTP(host, port, timeout=15)
            server.ehlo()
            try:
                server.starttls()
                server.ehlo()
            except smtplib.SMTPException:
                pass

        server.login(user, pwd)

        date_hdr = format_datetime(now)
        from_hdr = formataddr(("Ervin Glassworks", user))
        subj = subject.strip()

        for rcpt in dest:
            msg = MIMEText(f"{date_hdr}\n\n{body}", _charset="utf-8")
            msg["Subject"] = subj
            msg["From"] = from_hdr
            msg["To"] = rcpt
            msg["Date"] = date_hdr
            try:
                server.sendmail(user, [rcpt], msg.as_string())
                results["sent"].append(rcpt)
            except Exception as e:
                results["failed"][rcpt] = str(e)
    finally:
        try:
            if server:
                server.quit()
        except Exception:
            pass

    return results


# ---------- Phone/SMS (Twilio & ClickSend) ----------
def _build_numbers_for_call(recipients: list[dict]) -> list[str]:
    return [ (r.get("phone") or "").strip() for r in (recipients or []) if (r.get("phone") or "").strip() ]


def _build_numbers_for_sms(recipients: list[dict]) -> list[str]:
    out: list[str] = []
    for r in recipients or []:
        n = (r.get("phone") or "").strip()
        if n and r.get("receive_sms"):
            out.append(n)
    return out


# --- Twilio ---
def twilio_broadcast_calls(config: dict, numbers: list[str], *, message: str) -> dict:
    try:
        from twilio.rest import Client as TwilioClient
        from twilio.base.exceptions import TwilioRestException
    except Exception as e:
        return {"error": f"twilio library not installed: {e}"}

    acc_sid = (config.get("username") or "").strip()
    api_key = (config.get("token") or "").strip()
    api_sec = (config.get("api_secret") or "").strip()
    from_num = (config.get("source_number") or "").strip()
    if not (acc_sid and api_key and api_sec and from_num):
        return {"error": "Twilio credentials or from number missing"}

    client = TwilioClient(api_key, api_sec, acc_sid)
    twiml = f"<Response><Say voice='alice' language='en-US'>{escape(message)}</Say></Response>"

    out = []
    for to in numbers:
        try:
            call = client.calls.create(to=to, from_=from_num, twiml=twiml, machine_detection="Enable")
            out.append({"to": to, "call_sid": call.sid})
        except TwilioRestException as e:
            out.append({"to": to, "error": str(e), "status": getattr(e, "status", None), "code": getattr(e, "code", None)})
        except Exception as e:
            out.append({"to": to, "error": str(e)})
    return {"provider": "twilio", "result": out}


def twilio_broadcast_sms(config: dict, numbers: list[str], *, body: str) -> dict:
    try:
        from twilio.rest import Client as TwilioClient
        from twilio.base.exceptions import TwilioRestException
    except Exception as e:
        return {"error": f"twilio library not installed: {e}"}

    acc_sid = (config.get("username") or "").strip()
    api_key = (config.get("token") or "").strip()
    api_sec = (config.get("api_secret") or "").strip()
    from_num = (config.get("source_number") or "").strip()
    if not (acc_sid and api_key and api_sec and from_num):
        return {"error": "Twilio credentials or from number missing"}

    client = TwilioClient(api_key, api_sec, acc_sid)
    out = []
    for to in numbers:
        try:
            msg = client.messages.create(to=to, from_=from_num, body=body)
            out.append({"to": to, "sid": msg.sid})
        except TwilioRestException as e:
            out.append({"to": to, "error": str(e), "status": getattr(e, "status", None), "code": getattr(e, "code", None)})
        except Exception as e:
            out.append({"to": to, "error": str(e)})
    return {"provider": "twilio", "result": out}


# --- ClickSend ---
def clicksend_send_sms(config: dict, recipients: list[dict]) -> dict:
    try:
        import clicksend_client
        from clicksend_client import SmsMessage, SmsMessageCollection, SmsApi
        from clicksend_client.rest import ApiException
    except Exception as e:
        return {"error": f"clicksend_client not installed: {e}"}

    username = (config.get("username") or "").strip()
    api_key  = (config.get("api_key") or "").strip()
    sender   = (config.get("from") or "").strip()
    body     = (config.get("notify_text") or "").strip()

    dest = _build_numbers_for_sms(recipients)
    if not dest:
        return {"sent": [], "failed": {}, "note": "no sms recipients"}

    cfg = clicksend_client.Configuration()
    cfg.username = username
    cfg.password = api_key
    api = SmsApi(clicksend_client.ApiClient(cfg))

    messages = [SmsMessage(source="python", body=body, to=to, from_=sender if sender else None) for to in dest]
    try:
        resp = api.sms_send_post(SmsMessageCollection(messages=messages))
        return {"provider": "clicksend", "result": resp.to_dict() if hasattr(resp, "to_dict") else str(resp)}
    except ApiException as e:
        return {"provider": "clicksend", "error": str(e)}
    except Exception as e:
        return {"provider": "clicksend", "error": str(e)}


def clicksend_call_out(config: dict, recipients: list[dict]) -> dict:
    try:
        import clicksend_client
        from clicksend_client import VoiceApi, VoiceMessage, VoiceMessageCollection
        from clicksend_client.rest import ApiException
    except Exception as e:
        return {"error": f"clicksend_client not installed: {e}"}

    username = (config.get("username") or "").strip()
    api_key  = (config.get("api_key")  or "").strip()
    caller   = (config.get("voice_from") or "").strip()
    body     = (config.get("notify_text") or "").strip()

    dest = _build_numbers_for_call(recipients)
    if not dest:
        return {"sent": [], "failed": {}, "note": "no voice recipients"}

    cfg = clicksend_client.Configuration()
    cfg.username = username
    cfg.password = api_key
    api = VoiceApi(clicksend_client.ApiClient(cfg))

    messages = [VoiceMessage(to=to, body=body, source="python", caller=caller if caller else None) for to in dest]
    try:
        resp = api.voice_send_post(VoiceMessageCollection(messages=messages))
        return {"provider": "clicksend", "result": resp.to_dict() if hasattr(resp, "to_dict") else str(resp)}
    except ApiException as e:
        return {"provider": "clicksend", "error": str(e)}
    except Exception as e:
        return {"provider": "clicksend", "error": str(e)}


# ---------- Provider switchers ----------
def provider_call_out(cfg: dict, *, message: str, recipients: list[dict]) -> dict:
    """
    Place voice calls via the configured provider using `message` TTS content.
    Expects cfg to have either:
      - telephony_provider='twilio' and cfg['twilio'] creds, or
      - telephony_provider='clicksend' and cfg['clicksend'] creds.
    """
    prov = (cfg.get("telephony_provider") or cfg.get("provider") or "twilio").strip().lower()
    if prov == "clicksend":
        cs = dict(cfg.get("clicksend") or {})
        if message:
            cs["notify_text"] = message
        return clicksend_call_out(cs, recipients)
    # Twilio by default
    numbers = _build_numbers_for_call(recipients)
    return twilio_broadcast_calls(cfg.get("twilio") or {}, numbers, message=message)


def provider_send_sms(cfg: dict, *, body: str, recipients: list[dict]) -> dict:
    """
    Send SMS via the configured provider using `body`.
    """
    prov = (cfg.get("telephony_provider") or cfg.get("provider") or "twilio").strip().lower()
    if prov == "clicksend":
        return clicksend_send_sms(cfg.get("clicksend") or {}, recipients)
    numbers = _build_numbers_for_sms(recipients)
    return twilio_broadcast_sms(cfg.get("twilio") or {}, numbers, body=body)