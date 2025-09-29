#!/usr/bin/env bash
set -euo pipefail

AP_SSID="FirePi-$(cat /proc/cpuinfo | awk -F': ' '/Serial/ {print substr($2, length($2)-3)}')"
AP_PSK_FILE="/var/lib/firepi/ap_psk"
mkdir -p "$(dirname "$AP_PSK_FILE")"
if [[ ! -s "$AP_PSK_FILE" ]]; then head -c 12 /dev/urandom | base64 | tr -dc 'A-Za-z0-9' | head -c 12 > "$AP_PSK_FILE"; fi
AP_PSK="$(cat "$AP_PSK_FILE")"

case "${1:-}" in
  start)
    DURATION="${2:-15min}"
    # Kill any previous AP connection
    nmcli -t -f NAME,TYPE con show | awk -F: '$2=="wifi" && $1 ~ /^FirePi-/{print $1}' | xargs -r -I{} nmcli con delete "{}" || true

    # Create a Wi-Fi hotspot (shared)
    nmcli dev wifi hotspot ifname wlan0 ssid "$AP_SSID" password "$AP_PSK" || true
    echo "SoftAP up: SSID=$AP_SSID PSK=$AP_PSK (window $DURATION)"

    # Optionally print URL:
    echo "Open http://firepi.local or http://10.42.0.1 in your browser."

    # Set a timer to stop (if STA connect doesn’t do it earlier)
    systemd-run --unit firepi-softap-timer --on-active="$DURATION" --property=Type=oneshot /usr/local/bin/firepi-softap.sh stop
    ;;

  stop)
    # When STA is connected, this tears down shared hotspot and returns to normal
    nmcli connection down Hotspot || true
    nmcli connection delete Hotspot || true
    systemctl stop firepi-softap-timer.service 2>/dev/null || true
    echo "SoftAP stopped."
    ;;

  *)
    echo "usage: $0 {start [duration]|stop}" ; exit 2
    ;;
esac
