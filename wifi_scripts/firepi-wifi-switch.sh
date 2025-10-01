#!/usr/bin/env bash
set -e -o pipefail

# Resolve APP_HOME
if [[ -z "${APP_HOME:-}" ]]; then
  SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  APP_HOME="$(cd "${SCRIPT_DIR}/.." && pwd)"
fi

INSTANCE_DIR="${APP_HOME}/instance"
mkdir -p "${INSTANCE_DIR}"

# Pending credentials file path (can be overridden via env)
PENDING="${FIREPI_PENDING_WIFI:-${INSTANCE_DIR}/pending_wifi.json}"

LOG="${INSTANCE_DIR}/wifi_switch.log"
exec >>"$LOG" 2>&1
echo "[$(date -Is)] starting switch; PENDING=$PENDING"

IFACE=$(nmcli -t -f DEVICE,TYPE dev status | awk -F: '$2=="wifi"{print $1; exit}'); [[ -z "$IFACE" ]] && IFACE=wlan0

if [[ ! -f "$PENDING" ]]; then
  logger -t firepi "pending wifi not found at $PENDING"
  exit 2
fi

SSID=$(jq -r .ssid "$PENDING" 2>/dev/null || echo "")
PSK=$(jq -r .psk  "$PENDING" 2>/dev/null || echo "")
logger -t firepi "Switching WiFi to SSID='${SSID}'"

# Bring AP down
nmcli con down FirePiAP 2>/dev/null || true
nmcli con delete FirePiAP 2>/dev/null || true
nmcli con down Hotspot 2>/dev/null || true
nmcli con delete Hotspot 2>/dev/null || true

# Attempt connect
if [[ -n "$PSK" && "$PSK" != "null" ]]; then
  nmcli dev wifi connect "$SSID" password "$PSK" ifname "$IFACE" || true
else
  nmcli dev wifi connect "$SSID" ifname "$IFACE" || true
fi

# Wait until connected with non-AP IP
for i in {1..30}; do
  STATE="$(nmcli -t -f STATE g 2>/dev/null || true)"
  IP=$(nmcli -t -f IP4.ADDRESS dev show "$IFACE" 2>/dev/null | awk -F: 'NR==1{print $2}')
  if [[ "$STATE" == "connected" && -n "$IP" && "$IP" != 10.42.* ]]; then
    logger -t firepi "WiFi switch success: ${SSID} ${IP}"
    exit 0
  fi
  sleep 1
done

rm -f "$PENDING" || true

# Failed -> bring AP back for 30 min
logger -t firepi "WiFi switch failed; re-enabling AP"
"${APP_HOME}/wifi_scripts/firepi-softap.sh" start 30min || true
exit 1
