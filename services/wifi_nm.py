import subprocess as sp
import os, json, time
from typing import Dict, Any
from pathlib import Path

def _run(cmd, timeout: int = 6):
    try:
        return sp.run(cmd, capture_output=True, text=True, timeout=timeout)
    except sp.TimeoutExpired:
        return sp.CompletedProcess(args=cmd, returncode=124, stdout="", stderr="timeout")

def _wifi_iface() -> str:
    r = _run(["nmcli", "-t", "-f", "DEVICE,TYPE", "dev", "status"], timeout=3)
    for line in (r.stdout or "").splitlines():
        parts = line.split(":")
        if len(parts) >= 2 and parts[1] == "wifi":
            return parts[0]
    return "wlan0"

def status() -> Dict[str, Any]:
    iface = _wifi_iface()
    r = _run(["nmcli", "-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "dev", "status"], timeout=3)
    ssid = ""
    state = "disconnected"
    for line in (r.stdout or "").splitlines():
        dev, typ, st, conn = (line.split(":") + ["", "", "", ""])[:4]
        if typ == "wifi" and dev == iface:
            state = st or "disconnected"
            ssid = conn or ""
    ap_active = ssid in ("FirePiAP", "Hotspot")
    ip = _run(["bash", "-lc", f"ip -4 addr show {iface} | awk '/inet /{{print $2}}' | cut -d/ -f1"], timeout=3).stdout.strip()
    ap_psk = ""
    if ap_active:
        try:
            with open("/var/lib/firepi/ap_psk", "r") as f:
                ap_psk = f.read().strip()
        except Exception:
            pass
    return {"status": "ok", "mode": "ap" if ap_active else "sta", "state": state, "ip": ip, "ssid": ssid, "ap_psk": ap_psk}

def scan() -> Dict[str, Any]:
    ract = _run(["nmcli", "-t", "-f", "NAME,TYPE", "con", "show", "--active"], timeout=3)
    ap_active = any((ln.startswith("FirePiAP:wifi") or ln.startswith("Hotspot:wifi")) for ln in (ract.stdout or "").splitlines())
    rescan_args = [] if not ap_active else ["--rescan", "no"]
    if not ap_active:
        _run(["nmcli", "device", "wifi", "rescan"], timeout=5)
    r = _run(["nmcli", "-t", "-f", "SSID,SIGNAL,SECURITY", "dev", "wifi", "list"] + rescan_args, timeout=6)
    nets = []
    seen = set()
    for line in (r.stdout or "").splitlines():
        if not line:
            continue
        ssid, sig, sec = (line.split(":") + ["", "", ""])[:3]
        if not ssid or ssid in seen:
            continue
        seen.add(ssid)
        try:
            sig_i = int(sig) if sig else 0
        except ValueError:
            sig_i = 0
        nets.append({"ssid": ssid, "signal": sig_i, "security": sec or ""})
    return {"status": "ok", "ap_mode": ap_active, "networks": nets, "results": nets}

def connect(ssid: str, psk: str, wait_s: int = 0) -> Dict[str, Any]:
    ssid = (ssid or "").strip()
    psk  = (psk or "").strip()
    if not ssid:
        return {"status": "error", "error": "Missing SSID"}

    # Write pending creds in app-local instance dir
    app_root = Path(__file__).resolve().parents[1]   # /home/chris/firepi
    instance_dir = Path(os.environ.get("FIREPI_INSTANCE_DIR", app_root / "instance"))
    instance_dir.mkdir(parents=True, exist_ok=True)
    pending_path = instance_dir / "pending_wifi.json"
    pending_path.write_text(json.dumps({"ssid": ssid, "psk": psk, "ts": time.time()}))

    # Context for the UI (read AP PSK from instance if present)
    ap_ssid = "FirePi-AP"
    try:
        cpu = Path("/proc/cpuinfo").read_text()
        if "Serial" in cpu:
            ap_ssid = "FirePi-" + cpu.split("Serial")[-1].split("\n")[0].split(":")[-1].strip()[-4:]
    except Exception:
        pass
    ap_psk = ""
    for p in [instance_dir / "ap_psk"]:
        if p.exists():
            ap_psk = p.read_text().strip()
            break

    # Spawn the switch script directly (non-root)
    #script = app_root / "wifi_scripts" / "firepi-wifi-switch.sh"
    #if not script.exists():
    #    return {"status": "error", "error": f"switch script not found: {script}"}

    #env = dict(os.environ, APP_HOME=str(app_root), FIREPI_PENDING_WIFI=str(pending_path))
    #try:
    #    sp.Popen([str(script)], env=env, start_new_session=True)
    #except Exception as e:
    #    return {"status": "error", "error": f"spawn failed: {e}"}

    # Return immediately; frontend should expect AP drop / reconnection
    return {"status": "ok", "mode": "transitioning", "ap_ssid": ap_ssid, "ap_psk": ap_psk}