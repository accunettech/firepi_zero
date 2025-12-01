\
#!/usr/bin/env bash
# wifi_scripts/firepi-softap.sh
# Start a SoftAP ONLY if Wi‑Fi STA is not connected. Auto-teardown after N minutes.
set -Eeuo pipefail

MINUTES="${1:-30}"

# Derive APP_HOME from script location (../)
APP_HOME="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export APP_HOME

pick_iface() {
  nmcli -t -f DEVICE,TYPE dev status | awk -F: '$2=="wifi"{print $1; exit}'
}

wait_for_nm() {
  if command -v nm-online >/dev/null 2>&1; then
    nm-online -q -t 25 || true
  else
    sleep 5
  fi
}

wifi_connected() {
  local ifc="$1"
  nmcli -t -f DEVICE,STATE dev status | grep -q "^${ifc}:connected"
}

main() {
  wait_for_nm
  IFACE="$(pick_iface)"; IFACE="${IFACE:-wlan0}"

  if wifi_connected "$IFACE"; then
    echo "[softap] STA already connected on ${IFACE}; not starting AP"
    exit 0
  fi

  # Deterministic SSID/PSK from MAC
  MAC="$(cat "/sys/class/net/${IFACE}/address" 2>/dev/null || echo 00:00:00:00:00:00)"
  HEX="${MAC//:/}"
  TAIL="${HEX:6:6}"
  TAIL_UP="$(echo "$TAIL" | tr '[:lower:]' '[:upper:]')"
  SSID="FirePi-${TAIL_UP}"
  PSK="FirePi${TAIL_UP}"

  # Create/modify AP profile
  if ! nmcli -g NAME c show FirePiAP >/dev/null 2>&1; then
    nmcli con add type wifi ifname "$IFACE" con-name FirePiAP ssid "$SSID"
  fi

  nmcli con modify FirePiAP \
    802-11-wireless.mode ap \
    802-11-wireless.band bg \
    802-11-wireless.channel 6 \
    ipv4.method shared \
    ipv6.method ignore \
    wifi-sec.key-mgmt wpa-psk \
    wifi-sec.psk "$PSK" \
    wifi-sec.group ccmp \
    wifi-sec.pairwise ccmp \
    802-11-wireless.pmf disable \
    connection.autoconnect no

  echo "[softap] Bringing up AP '${SSID}' on ${IFACE} for ${MINUTES} minutes"
  nmcli -w 15 con up FirePiAP

  WAV="${APP_HOME}/audio/stock/ready_to_connect.wav"
  if [[ -f "$WAV" ]]; then
    ( aplay "$WAV" >/dev/null 2>&1 || true ) &
  fi

  # Sleep for requested minutes, then tear down AP if still not on STA
  sleep "$((MINUTES*60))" || true

  if wifi_connected "$IFACE"; then
    echo "[softap] STA connected; leaving AP state as-is (NM may have already torn it down)"
    exit 0
  fi

  echo "[softap] Timeout reached; tearing down AP"
  nmcli -w 10 con down FirePiAP || true
}

main "$@"