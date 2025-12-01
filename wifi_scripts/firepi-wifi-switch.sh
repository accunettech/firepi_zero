#!/usr/bin/env bash
set -Eeuo pipefail
APP_HOME="${APP_HOME:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
PENDING="${FIREPI_PENDING_WIFI:-${APP_HOME}/instance/pending_wifi.json}"
LOG="${APP_HOME}/instance/wifi_switch.log"; mkdir -p "${APP_HOME}/instance"; exec >>"$LOG" 2>&1
LOCK="${APP_HOME}/instance/wifi_switch.lock"
COOLDOWN="${APP_HOME}/instance/sta_recent_ok"

exec 9>"$LOCK"
flock -n 9 || { echo "[$(date -Is)] another switch in progress"; exit 0; }

echo "[$(date -Is)] starting switch; PENDING=$PENDING uid=$(id -u) user=$(id -un)"

IFACE=$(nmcli -t -f DEVICE,TYPE dev status | awk -F: '$2=="wifi"{print $1; exit}'); [[ -z "$IFACE" ]] && IFACE=wlan0
[[ -f "$PENDING" ]] || { echo "no pending file"; exit 2; }

SSID=$(jq -r '.ssid // empty' "$PENDING" 2>/dev/null || python3 -c 'import json,sys;print(json.load(open(sys.argv[1])).get("ssid",""))' "$PENDING")
PSK=$(jq -r '.psk  // empty' "$PENDING" 2>/dev/null || python3 -c 'import json,sys;print(json.load(open(sys.argv[1])).get("psk",""))' "$PENDING")
[[ -z "$SSID" ]] && { echo "no SSID in pending"; rm -f "$PENDING"; exit 2; }

nmcli con mod FirePiAP connection.autoconnect no 2>/dev/null || true
nmcli con down FirePiAP 2>/dev/null || true
nmcli con delete FirePiAP 2>/dev/null || true
nmcli con mod Hotspot connection.autoconnect no 2>/dev/null || true
nmcli con down Hotspot 2>/dev/null || true
nmcli con delete Hotspot 2>/dev/null || true

CON="FirePiSTA-${SSID}"
nmcli con show "$CON" >/dev/null 2>/dev/null || CON=""
if [[ -z "$CON" ]]; then
  if [[ -n "$PSK" ]]; then
    nmcli -w 20 dev wifi connect "$SSID" password "$PSK" ifname "$IFACE" name "FirePiSTA-${SSID}"
  else
    nmcli -w 20 dev wifi connect "$SSID" ifname "$IFACE" name "FirePiSTA-${SSID}"
  fi
  CON="FirePiSTA-${SSID}"
fi

nmcli con mod "$CON" connection.autoconnect yes connection.autoconnect-priority 100 ipv6.method ignore || true

for i in {1..20}; do
  IP=$(nmcli -g IP4.ADDRESS dev show "$IFACE" | head -1 | cut -d/ -f1)
  STATE=$(nmcli -g GENERAL.STATE dev show "$IFACE" | cut -d' ' -f1)
  echo "waiting IP... state=$STATE ip=${IP:-none}"
  [[ -n "$IP" && "$IP" != 10.42.* && "$STATE" -ge 100 ]] && break
  sleep 1
done

if [[ -z "$IP" || "$IP" == 10.42.* || "$STATE" -lt 100 ]]; then
  echo "STA did not come up, reverting to AP"
  "${APP_HOME}/wifi_scripts/firepi-softap.sh" start 30min || true
  rm -f "$PENDING" || true
  exit 1
fi

echo "WiFi switch success: $SSID $IP"
"${APP_HOME}/wifi_scripts/firepi-softap.sh" stop || true
rm -f "$PENDING" || true
touch "$COOLDOWN" || true
exit 0
