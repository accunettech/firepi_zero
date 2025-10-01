#!/usr/bin/env bash
set -e -o pipefail

# Resolve APP_HOME from env or relative to this script
if [[ -z "${APP_HOME:-}" ]]; then
  SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  APP_HOME="$(cd "${SCRIPT_DIR}/.." && pwd)"
fi

INSTANCE_DIR="${APP_HOME}/instance"
mkdir -p "${INSTANCE_DIR}"

# Pick Wi‑Fi interface
IFACE=$(nmcli -t -f DEVICE,TYPE dev status | awk -F: '$2=="wifi"{print $1; exit}'); [[ -z "$IFACE" ]] && IFACE=wlan0

# Serial suffix for SSID
SER=$(awk -F': ' '/Serial/ { print $2 }' /proc/cpuinfo)
SER_SUFFIX="${SER: -4}"; [[ -z "$SER_SUFFIX" ]] && SER_SUFFIX="0000"

# Deterministic PSK from MAC last 6 hex chars
MAC=$(cat "/sys/class/net/${IFACE}/address" 2>/dev/null | tr -d ':' | tr '[:lower:]' '[:upper:]')
if [[ -n "$MAC" ]]; then
  MAC_SUFFIX="${MAC: -6}"
else
  TMP="${SER: -6}"
  MAC_SUFFIX="$(printf '%06s' "$TMP" | tr ' ' '0' | tr '[:lower:]' '[:upper:]')"
fi
if [[ ${#MAC_SUFFIX} -lt 6 ]]; then MAC_SUFFIX="$(printf '%06s' "$MAC_SUFFIX" | tr ' ' '0')"; fi

AP_SSID="FirePi-${SER_SUFFIX}-${MAC_SUFFIX}"
AP_PSK="FirePi${MAC_SUFFIX}"

# Persist PSK to app-local instance dir
echo -n "$AP_PSK" > "${INSTANCE_DIR}/ap_psk"
chmod 0644 "${INSTANCE_DIR}/ap_psk"

case "${1:-}" in
  start)
    DURATION="${2:-30min}"
    # Clean any old hotspot profiles
    nmcli con down Hotspot 2>/dev/null || true
    nmcli con delete Hotspot 2>/dev/null || true
    nmcli con down FirePiAP 2>/dev/null || true
    nmcli con delete FirePiAP 2>/dev/null || true

    nmcli con add type wifi ifname "$IFACE" con-name FirePiAP autoconnect no ssid "$AP_SSID"
    nmcli con modify FirePiAP       802-11-wireless.mode ap       802-11-wireless.band bg       802-11-wireless.channel 6       ipv4.method shared       ipv6.method ignore       wifi-sec.key-mgmt wpa-psk       wifi-sec.psk "$AP_PSK"       wifi-sec.group ccmp       wifi-sec.pairwise ccmp       wifi-sec.pmf disable

    nmcli con up FirePiAP

    echo "SoftAP up: SSID=$AP_SSID  PSK=$AP_PSK"
    echo "Open http://10.42.0.1 or http://firepi.local to configure."

    # Auto-stop after window
    systemd-run --unit firepi-softap-timer --on-active="$DURATION" --property=Type=oneshot "${BASH_SOURCE[0]}" stop >/dev/null 2>&1 || true
    ;;
  stop)
    nmcli con down FirePiAP 2>/dev/null || true
    nmcli con delete FirePiAP 2>/dev/null || true
    systemctl stop firepi-softap-timer.service >/dev/null 2>&1 || true
    ;;
  *)
    echo "usage: $0 {start [duration]|stop}" ; exit 2 ;;
esac
