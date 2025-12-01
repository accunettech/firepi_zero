#!/usr/bin/env bash
set -Eeuo pipefail
APP_HOME="${APP_HOME:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
LOG="${APP_HOME}/instance/wifi_online.log"; mkdir -p "${APP_HOME}/instance"; exec >>"$LOG" 2>&1

IFACE=$(nmcli -t -f DEVICE,TYPE dev status | awk -F: '$2=="wifi"{print $1; exit}'); [[ -z "$IFACE" ]] && IFACE=wlan0
PENDING="${APP_HOME}/instance/pending_wifi.json"
COOLDOWN="${APP_HOME}/instance/sta_recent_ok"
LOCK="${APP_HOME}/instance/wifi_switch.lock"

echo "[$(date -Is)] wifi-online: check begin iface=$IFACE"

[[ -f "$LOCK" ]] && { echo "switch lock present; skipping AP"; exit 0; }
[[ -f "$PENDING" ]] && { echo "pending creds exist; skipping AP"; exit 0; }

is_sta_up() {
  if ! nmcli -t -f NAME,TYPE,DEVICE con show --active | grep -qE ":wifi$"; then return 1; fi
  IP=$(nmcli -g IP4.ADDRESS dev show "$IFACE" | head -1 | cut -d/ -f1)
  [[ -z "$IP" ]] && return 1
  [[ "$IP" == 10.42.* ]] && return 1
  STATE=$(nmcli -g GENERAL.STATE dev show "$IFACE" | cut -d' ' -f1)
  [[ "$STATE" -ge 100 ]]
}

if [[ -f "$COOLDOWN" ]]; then
  now=$(date +%s); then_ts=$(stat -c %Y "$COOLDOWN" 2>/dev/null || stat -f %m "$COOLDOWN")
  age=$(( now - then_ts ))
  if (( age < 300 )); then
    echo "recent STA success ($age s ago); skipping AP"
    exit 0
  fi
fi

if is_sta_up; then
  echo "STA looks good; no AP needed"
  exit 0
fi

echo "No STA; starting SoftAP window"
"${APP_HOME}/wifi_scripts/firepi-softap.sh" start 30min || true
