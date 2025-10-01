#!/usr/bin/env bash
set -e -o pipefail

# Resolve APP_HOME
if [[ -z "${APP_HOME:-}" ]]; then
  SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  APP_HOME="$(cd "${SCRIPT_DIR}/.." && pwd)"
fi

INSTANCE_DIR="${APP_HOME}/instance"
mkdir -p "${INSTANCE_DIR}"

sleep 4
nmcli device wifi rescan >/dev/null 2>&1 || true

for i in {1..6}; do
  STATE="$(nmcli -t -f STATE g 2>/dev/null || true)"
  if [[ "$STATE" == "connected" ]]; then exit 0; fi
  sleep 2
done

# Not connected: start AP window
systemctl start firepi-softap@30min.service || true

# Play onboarding audio if present
if [[ -f "${INSTANCE_DIR}/onboarding.wav" ]]; then
  aplay -q "${INSTANCE_DIR}/onboarding.wav" >/dev/null 2>&1 || true
elif [[ -f "${APP_HOME}/audio/stock/ready_to_connect.wav" ]]; then
  aplay -q "${APP_HOME}/audio/stock/ready_to_connect.wav" >/dev/null 2>&1 || true
fi
