from __future__ import annotations
import subprocess as sp
import time
from typing import Dict, Any, List

def _run(args: list[str], timeout: int = 20, check: bool = True) -> str:
    r = sp.run(args, capture_output=True, text=True, timeout=timeout)
    if check and r.returncode != 0:
        raise RuntimeError(r.stderr.strip() or r.stdout.strip() or f"Command failed: {' '.join(args)}")
    return r.stdout.strip()

def status() -> Dict[str, Any]:
    try:
        state = _run(["nmcli","-t","-f","STATE","g"], check=False) or "unknown"
    except Exception:
        state = "unknown"
    ip = ""
    try:
        show = _run(["nmcli","-t","-f","GENERAL.CONNECTION,IP4.ADDRESS","dev","show","wlan0"], check=False)
        # looks like: GENERAL.CONNECTION:<ssid>\nIP4.ADDRESS[1]:10.42.0.1/24
        for line in show.splitlines():
            if line.startswith("IP4.ADDRESS"):
                ip = line.split(":",1)[-1]
    except Exception:
        pass
    return {"state": state, "ip": ip}

def scan() -> List[Dict[str, Any]]:
    try:
        _run(["nmcli","device","wifi","rescan"], check=False, timeout=10)
    except Exception:
        pass
    out = _run(["nmcli","-t","-f","SSID,SIGNAL,SECURITY","device","wifi","list"], check=False, timeout=10)
    nets = []
    seen = set()
    for line in (out or "").splitlines():
        parts = line.split(":")
        if not parts: 
            continue
        ssid = parts[0].strip()
        if not ssid or ssid in seen:
            continue
        seen.add(ssid)
        sig = 0
        try:
            sig = int((parts[1] or "0").strip())
        except Exception:
            pass
        sec = (parts[2] if len(parts) > 2 else "").strip() or "UNKNOWN"
        nets.append({"ssid": ssid, "signal": sig, "security": sec})
    nets.sort(key=lambda x: (-x["signal"], x["ssid"]))
    return nets

def connect(ssid: str, psk: str, wait_s: int = 20) -> Dict[str, Any]:
    ssid = (ssid or "").strip()
    psk  = (psk or "").strip()
    if not ssid:
        return {"status":"error","error":"Missing SSID"}
    # Try create or modify saved connection
    exists = False
    try:
        _run(["nmcli","-g","NAME","con","show", ssid], check=True)
        exists = True
    except Exception:
        exists = False
    if not exists:
        args = ["nmcli","dev","wifi","connect", ssid]
        if psk: args += ["password", psk]
        _run(args, check=True, timeout=25)
    else:
        if psk:
            _run(["nmcli","con","modify", ssid, f"wifi-sec.psk={psk}"], check=True)
        _run(["nmcli","con","up", ssid], check=True)

    # Wait for connected state
    for _ in range(max(1, wait_s)):
        st = status().get("state","")
        if st == "connected":
            # Tear down AP if present
            try:
                sp.run(["/usr/local/bin/firepi-softap.sh","stop"], check=False)
            except Exception:
                pass
            return {"status":"ok"}
        time.sleep(1)
    return {"status":"error","error":"Timeout waiting for connection"}

def forget(ssid: str) -> Dict[str, Any]:
    ssid = (ssid or "").strip()
    if not ssid:
        return {"status":"error","error":"Missing SSID"}
    sp.run(["nmcli","con","delete", ssid], check=False)
    return {"status":"ok"}
