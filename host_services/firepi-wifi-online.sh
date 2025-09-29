#!/usr/bin/env bash
set -euo pipefail

# Wait a bit for NM
sleep 4

# Try to auto-connect any known Wi-Fi
nmcli -t -f NAME,TYPE,DEVICE con show | grep -q ':wifi:' || true

# If already connected, exit OK
if nmcli -t -f WIFI g | grep -q enabled && nmcli -t -f STATE g | grep -q connected; then
  exit 0
fi

# Try connect to best-known automatically
nmcli device wifi rescan || true
# NM auto-connects saved connections; give it a few seconds
for i in {1..12}; do
  state="$(nmcli -t -f STATE g || true)"
  if [[ "$state" == "connected" ]]; then exit 0; fi
  sleep 2
done

# Not connected: start the AP window (separate unit)
systemctl start firepi-softap@15min.service
