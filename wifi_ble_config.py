#!/usr/bin/env python3
# FirePi: Configure Wi-Fi over BLE (Bluezero legacy ID-based API; robust wpa_cli + WWR)

import subprocess, time, sys, shutil
from bluezero import peripheral
from gi.repository import GLib  # python3-gi

# UUIDs (service + characteristics)
WIFI_SERVICE_UUID = '12345678-1234-5678-1234-56789abcdef0'
SSID_UUID         = '12345678-1234-5678-1234-56789abcdef1'
PASS_UUID         = '12345678-1234-5678-1234-56789abcdef2'
APPLY_UUID        = '12345678-1234-5678-1234-56789abcdef3'
STATUS_UUID       = '12345678-1234-5678-1234-56789abcdef4'

# Numeric IDs required by your Bluezero build
SRV_ID   = 0
CHR_SSID = 0
CHR_PASS = 1
CHR_APPLY= 2
CHR_STAT = 3

state = {'ssid': '', 'pass': '', 'status': 'Idle'}
p = None  # set in main()


# ---------------- shell helpers ----------------
def _sh(cmd: list[str] | str):
    return subprocess.run(cmd, shell=isinstance(cmd,str),
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

def _adapter_addr() -> str:
    out = _sh("bluetoothctl list | awk '/Controller/{print $2; exit}'").stdout.strip()
    if out: return out
    out = _sh("hciconfig -a | awk '/BD Address:/{print $3; exit}'").stdout.strip()
    if out: return out
    raise RuntimeError("No Bluetooth adapter address found")

def _which_wpa_cli() -> str:
    for path in ("/usr/sbin/wpa_cli", "/sbin/wpa_cli", shutil.which("wpa_cli")):
        if path and shutil.which(path) or (path and _sh(f"test -x {path} && echo ok").stdout.strip()=="ok"):
            return path if isinstance(path, str) else "wpa_cli"
    return "wpa_cli"  # fallback to PATH

def _current_ssid() -> str:
    ssid = _sh("iwgetid -r 2>/dev/null").stdout.strip()
    if ssid: return ssid
    ssid = _sh("wpa_cli -i wlan0 status 2>/dev/null | awk -F= '/^ssid=/{print $2; exit}'").stdout.strip()
    return ssid


# --------------- Wi-Fi + status ---------------
def set_status(msg: str):
    state['status'] = msg  # client re-reads Status; no notify push

def on_ssid_read(_=None):   return state['ssid'].encode('utf-8')
def on_status_read(_=None): return state['status'].encode('utf-8')
def on_ssid_write(v: bytes, _=None):
    state['ssid'] = v.decode('utf-8').strip()
    print(f"[BLE] SSID write: '{state['ssid']}'", file=sys.stderr)
def on_pass_write(v: bytes, _=None):
    state['pass'] = v.decode('utf-8')
    print(f"[BLE] PASS write: {len(state['pass'])} chars", file=sys.stderr)

def on_apply_write(_v: bytes, _=None):
    print("[BLE] Apply write received", file=sys.stderr)
    set_status('Applying…')
    ok, msg = apply_wifi()
    set_status(('Success: ' if ok else 'Error: ') + msg)
    print(f"[BLE] Apply result: {state['status']}", file=sys.stderr)


def apply_wifi() -> tuple[bool, str]:
    ssid, pw = state['ssid'], state['pass']
    if not ssid:
        return False, 'SSID empty'
    if pw and len(pw) < 8:
        return False, 'Password too short'

    wpa = _which_wpa_cli()
    def w(args: list[str]) -> str:
        cp = subprocess.run([wpa, "-i", "wlan0", *args],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        return cp.stdout.strip()

    # Make sure we can talk to wpa_supplicant (socket perms)
    ping = w(["ping"])
    if "PONG" not in ping:
        return False, f"wpa_cli unreachable ({ping})"

    # Disable others to avoid racing priorities; then create a fresh network id
    w(["disable_network", "all"])

    add_out = w(["add_network"])
    try:
        net_id = add_out.strip()
        # Some builds return just the id, others "Network id N"
        if not net_id.isdigit():
            net_id = "".join(ch for ch in net_id if ch.isdigit())
        net_id_int = int(net_id)
    except Exception:
        return False, f"could not parse add_network output: {add_out!r}"

    # Set SSID/PSK (or open)
    set_ssid = w(["set_network", str(net_id_int), "ssid", f"\"{ssid}\""])
    if pw:
        set_psk = w(["set_network", str(net_id_int), "psk", f"\"{pw}\""])
    else:
        set_psk = w(["set_network", str(net_id_int), "key_mgmt", "NONE"])

    # Select & enable it
    sel = w(["select_network", str(net_id_int)])
    en  = w(["enable_network", str(net_id_int)])
    sv  = w(["save_config"])
    rc  = w(["reconfigure"])

    # Wait up to ~12s for IPv4
    for _ in range(12):
        ip = _sh("ip -4 addr show wlan0 | awk '/inet /{print $2}'").stdout.strip()
        if ip:
            return True, f'Connected: {ip}'
        time.sleep(1)

    # If failed, report last few wpa_supplicant lines
    log = _sh('journalctl -u wpa_supplicant -n 15 --no-pager').stdout.strip()
    return False, 'Failed to connect'

# --------------------- main -------------------
def main():
    global p
    adapter = _adapter_addr()

    # ctor variants
    last_err = None
    for args in ((adapter, 'FirePi Config', 0x0080),
                 (adapter, 'FirePi Config'),
                 (adapter,)):
        try:
            p = peripheral.Peripheral(*args)
            break
        except Exception as e:
            last_err = e
    else:
        raise RuntimeError(f"Peripheral ctor failed: {last_err}")

    # Initialize with current SSID if we can read it
    cur = _current_ssid()
    if cur:
        state['ssid'] = cur
        state['status'] = f"Idle (current: {cur})"
    else:
        state['status'] = "Idle (current: unknown)"

    print("Registering GATT…", file=sys.stderr)
    p.add_service(SRV_ID, WIFI_SERVICE_UUID, True)
    print("  service added", file=sys.stderr)

    # IMPORTANT: include 'write-without-response' to satisfy some iOS paths
    p.add_characteristic(SRV_ID, CHR_SSID,  SSID_UUID,   state['ssid'].encode('utf-8'), False,
                         ['read','write','write-without-response','encrypt-write'], on_ssid_read, on_ssid_write, None)
    print("  chr SSID added", file=sys.stderr)

    p.add_characteristic(SRV_ID, CHR_PASS,  PASS_UUID,   b'', False,
                         ['write','write-without-response','encrypt-write'], None, on_pass_write, None)
    print("  chr PASS added", file=sys.stderr)

    p.add_characteristic(SRV_ID, CHR_APPLY, APPLY_UUID,  b'', False,
                         ['write','write-without-response','encrypt-write'], None, on_apply_write, None)
    print("  chr APPLY added", file=sys.stderr)

    p.add_characteristic(SRV_ID, CHR_STAT,  STATUS_UUID, state['status'].encode('utf-8'), False,
                         ['read'], on_status_read, None, None)
    print("  chr STATUS added", file=sys.stderr)

    # Best-effort: older build has no helper; skip if missing
    try:
        p.add_advertisement_service_uuid(WIFI_SERVICE_UUID)
        print("  adv uuid added", file=sys.stderr)
    except Exception:
        pass

    # Start advertising / register app using whatever this build exposes
    for meth in ('publish', 'start', 'register', 'register_app'):
        if hasattr(p, meth):
            try:
                getattr(p, meth)()
                print("Advertisement registered", file=sys.stderr)
                break
            except Exception as e:
                print(f"{meth}() failed: {e}", file=sys.stderr)

    # GLib main loop
    loop = GLib.MainLoop()
    print("Main loop running", file=sys.stderr)
    loop.run()


if __name__ == '__main__':
    try:
        main()
    except Exception as e:
        print("FATAL:", e, file=sys.stderr)
        raise
