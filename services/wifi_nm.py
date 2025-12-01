# services/wifi_nm.py
from __future__ import annotations
import os, json, shlex, subprocess as sp
from pathlib import Path
from typing import Dict, Any, List

APP_ROOT = Path(os.environ.get("FIREPI_APP_HOME", "")).expanduser() or Path(__file__).resolve().parents[1]
INSTANCE_DIR = APP_ROOT / "instance"
INSTANCE_DIR.mkdir(parents=True, exist_ok=True)
PENDING_WIFI = INSTANCE_DIR / "pending_wifi.json"

def _sh(cmd: str, timeout: int = 8):
    try:
        return sp.run(cmd, shell=True, text=True, stdout=sp.PIPE, stderr=sp.PIPE, timeout=timeout)
    except Exception as e:
        class R: pass
        r = R(); r.returncode = 124; r.stdout = ""; r.stderr = str(e)
        return r

def _wifi_iface() -> str:
    r = _sh("nmcli -t -f DEVICE,TYPE dev status")
    if r.returncode == 0:
        for line in r.stdout.splitlines():
            parts = line.split(":")
            if len(parts) >= 2 and parts[1].strip() == "wifi":
                return parts[0].strip()
    return "wlan0"

def _active_conn_name(iface: str) -> str | None:
    r = _sh(f"nmcli -t -g GENERAL.CONNECTION device show {shlex.quote(iface)}")
    return r.stdout.strip() if r.returncode == 0 else None

def _nm_ssid_from_conn(name: str | None) -> str | None:
    if not name:
        return None
    r = _sh(f"nmcli -t -g 802-11-wireless.ssid connection show {shlex.quote(name)}")
    s = r.stdout.strip() if r.returncode == 0 else ""
    return s or None

def _essid_iwgetid(iface: str) -> str | None:
    r = _sh(f"iwgetid {shlex.quote(iface)} -r")
    s = r.stdout.strip() if r.returncode == 0 else ""
    return s or None

def _essid_active_scan() -> str | None:
    r = _sh("nmcli -t -f ACTIVE,SSID dev wifi")
    if r.returncode == 0:
        for line in r.stdout.splitlines():
            parts = line.split(":", 1)
            if parts and parts[0].strip() == "yes":
                return parts[1].strip() if len(parts) > 1 else None
    return None

def _conn_mode(name: str | None) -> str | None:
    if not name:
        return None
    r = _sh(f"nmcli -t -g 802-11-wireless.mode connection show {shlex.quote(name)}")
    if r.returncode == 0:
        mode = r.stdout.strip().lower()
        if mode in ("ap", "infrastructure", "adhoc"):
            return "ap" if mode == "ap" else "sta"
    return None

def _ip4_addr_of(iface: str) -> str | None:
    r = _sh(f"nmcli -t -g IP4.ADDRESS device show {shlex.quote(iface)}")
    if r.returncode == 0:
        s = r.stdout.strip()
        cidr = s.splitlines()[0] if s else ""
        if cidr and "/" in cidr:
            return cidr.split("/", 1)[0]
        return cidr or None
    r = _sh("hostname -I")
    if r.returncode == 0:
        ip = (r.stdout.strip().split() + [""])[0]
        return ip or None
    return None

def status() -> Dict[str, Any]:
    iface = _wifi_iface()
    state_raw = "unknown"
    connection = None

    r = _sh("nmcli -t -f DEVICE,STATE,CONNECTION dev status")
    if r.returncode == 0:
        for line in r.stdout.splitlines():
            parts = line.split(":")
            if len(parts) >= 2 and parts[0].strip() == iface:
                # parts: [DEVICE, STATE, CONNECTION?]
                state_raw = parts[1].strip().lower()
                connection = parts[2].strip() if len(parts) >= 3 else None
                break

    if "connected" in state_raw:
        state_lbl = "connected"
    elif "connecting" in state_raw or "config" in state_raw:
        state_lbl = "connecting"
    elif "disconnected" in state_raw or state_raw == "unavailable":
        state_lbl = "disconnected"
    else:
        state_lbl = state_raw or "unknown"

    conn_name = _active_conn_name(iface)
    essid = _nm_ssid_from_conn(conn_name) or _essid_iwgetid(iface) or _essid_active_scan()
    mode = _conn_mode(conn_name) or ("ap" if (connection or "").lower().startswith("firepi") else "sta")

    out: Dict[str, Any] = {
        "iface": iface,
        "state": state_lbl,
        "connection": connection or conn_name or "",
        "mode": mode,
        "ip": _ip4_addr_of(iface) or "",
        "ssid": "",
        "essid": essid or "",
    }
    if essid:
        out["ssid"] = essid
    else:
        out["ssid"] = (connection or conn_name or "").strip()

    if out["mode"] == "ap":
        ap_ssid = _nm_ssid_from_conn(conn_name) or essid or out["ssid"]
        out["ap_ssid"] = ap_ssid

    return out

def scan() -> Dict[str, Any]:
    r = _sh("nmcli -t -f IN-USE,SSID,SIGNAL,SECURITY dev wifi list --rescan yes")
    networks: List[Dict[str, Any]] = []
    if r.returncode == 0:
        for line in r.stdout.splitlines():
            parts = (line + '::::').split(':', 3)
            inuse, ssid, signal, sec = [p.strip() for p in parts[:4]]
            if not ssid:
                continue
            networks.append({
                "active": (inuse == "*") or (inuse.lower() in ("yes", "on", "true")),
                "ssid": ssid,
                "signal": int(signal) if signal.isdigit() else None,
                "security": sec or "",
            })
    return {"networks": networks}

def connect(ssid: str, psk: str) -> Dict[str, Any]:
    ssid = (ssid or "").strip()
    psk  = (psk or "").strip()
    if not ssid:
        return {"ok": False, "error": "missing ssid"}
    import time
    data = {"ssid": ssid, "psk": psk, "ts": int(time.time())}
    try:
        PENDING_WIFI.write_text(json.dumps(data))
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}

def forget(ssid: str) -> Dict[str, Any]:
    ssid = (ssid or "").strip()
    if not ssid:
        return {"ok": False, "error": "missing ssid"}
    r = _sh("nmcli -t -f NAME,TYPE connection show")
    if r.returncode != 0:
        return {"ok": False, "error": "nmcli list failed"}
    ok = True
    for line in r.stdout.splitlines():
        parts = line.split(":")
        if len(parts) < 2 or parts[1].strip() != "wifi":
            continue
        prof = parts[0].strip()
        r2 = _sh(f"nmcli -t -g 802-11-wireless.ssid connection show {shlex.quote(prof)}")
        if r2.returncode == 0 and r2.stdout.strip() == ssid:
            r3 = _sh(f"nmcli connection delete {shlex.quote(prof)}")
            ok = ok and (r3.returncode == 0)
    return {"ok": ok}
