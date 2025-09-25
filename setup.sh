#!/usr/bin/env bash
set -euo pipefail

# ============================================
# FirePi setup
# - Installs OS deps (python, alsa-utils, mpg123, sox, git)
# - Enables I2S DAC overlay (default: hifiberry-dac) & disables onboard audio
# - Writes /etc/asound.conf with softvol "PCM" + mono->stereo route
# - Disables pigpiod (avoids I2S conflicts)
# - Creates & enables a systemd unit for the app (not started until reboot)
# ============================================

# Defaults (override with flags)
APP_DIR="$(pwd)"
SERVICE_NAME="firepi"
OVERLAY="hifiberry-dac"    # I2S overlay for HiFiBerry DAC class devices
PYTHON_BIN=""              # autodetect venv or /usr/bin/python3

print_usage() {
  cat <<EOF
Usage: sudo bash $0 [--app-dir PATH] [--service-name NAME] [--overlay NAME]

Options:
  --app-dir PATH        Path to your app directory (contains app.py). Default: current directory
  --service-name NAME   Systemd unit name (NAME.service). Default: firepi
  --overlay NAME        I2S overlay to enable. Default: hifiberry-dac

Examples:
  sudo bash $0 --app-dir /home/pi/firepi
  sudo bash $0 --app-dir /home/pi/firepi --overlay hifiberry-dac
EOF
}

# ---------- arg parsing ----------
while [[ $# -gt 0 ]]; do
  case "$1" in
    --app-dir)       APP_DIR="$2"; shift 2 ;;
    --service-name)  SERVICE_NAME="$2"; shift 2 ;;
    --overlay)       OVERLAY="$2"; shift 2 ;;
    -h|--help)       print_usage; exit 0 ;;
    *) echo "Unknown arg: $1"; print_usage; exit 1 ;;
  esac
done

# ---------- root check ----------
if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  echo "Please run as root (use sudo)." >&2
  exit 1
fi

# ---------- sanity checks ----------
if [[ ! -d "$APP_DIR" ]]; then
  echo "App directory not found: $APP_DIR" >&2
  exit 1
fi
if [[ ! -f "$APP_DIR/app.py" ]]; then
  echo "app.py not found in $APP_DIR" >&2
  exit 1
fi

RUN_USER="${SUDO_USER:-$USER}"
RUN_GROUP="$(id -gn "$RUN_USER")"

echo "==> Using:"
echo "    APP_DIR        = $APP_DIR"
echo "    SERVICE_NAME   = $SERVICE_NAME"
echo "    OVERLAY        = $OVERLAY"
echo "    RUN_USER/GROUP = $RUN_USER:$RUN_GROUP"
echo

# ---------- install OS deps ----------
echo "==> Installing OS packages..."
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y \
  python3 \
  python3-venv \
  python3-pip \
  git \
  alsa-utils \
  mpg123 \
  sox

# ---------- disable pigpiod (avoids I2S conflicts) ----------
echo "==> Disabling pigpiod (if present)..."
if systemctl list-unit-files | grep -q '^pigpiod\.service'; then
  systemctl disable --now pigpiod.service || true
fi

# ---------- pick python ----------
if [[ -x "$APP_DIR/.venv/bin/python" ]]; then
  PYTHON_BIN="$APP_DIR/.venv/bin/python"
else
  PYTHON_BIN="/usr/bin/python3"
fi
echo "==> Python: $PYTHON_BIN"

# ---------- ensure user in audio group ----------
if ! id -nG "$RUN_USER" | tr ' ' '\n' | grep -qx audio; then
  echo "==> Adding $RUN_USER to 'audio' group..."
  usermod -aG audio "$RUN_USER"
fi

# ---------- configure /boot/*/config.txt ----------
CONFIG_TXT=""
for f in /boot/firmware/config.txt /boot/config.txt; do
  if [[ -f "$f" ]]; then CONFIG_TXT="$f"; break; fi
done
if [[ -z "$CONFIG_TXT" ]]; then
  CONFIG_TXT="/boot/firmware/config.txt"
  touch "$CONFIG_TXT"
fi

echo "==> Updating $CONFIG_TXT (disable onboard audio, add overlay)..."
cp -a "$CONFIG_TXT" "$CONFIG_TXT.bak.$(date +%Y%m%d-%H%M%S)"

# Disable onboard audio
if grep -Eq '^\s*dtparam=audio=' "$CONFIG_TXT"; then
  sed -i 's/^\s*dtparam=audio=.*/dtparam=audio=off/g' "$CONFIG_TXT"
else
  echo "dtparam=audio=off" >> "$CONFIG_TXT"
fi

# Remove duplicates of this overlay, then add desired overlay
sed -i "/^dtoverlay=${OVERLAY//\//\\/}/d" "$CONFIG_TXT"
echo "dtoverlay=${OVERLAY}" >> "$CONFIG_TXT"

# ---------- detect ALSA card index ----------
detect_card() {
  local idx="" id=""
  if aplay -l >/tmp/aplay_list 2>/dev/null; then
    # Prefer known I2S DACs
    while IFS= read -r line; do
      if [[ "$line" =~ ^card[[:space:]]+([0-9]+):[[:space:]]*([^[:space:]]+) ]]; then
        local n="${BASH_REMATCH[1]}"
        local ident="${BASH_REMATCH[2]}"
        local lc
        lc="$(echo "$ident" | tr '[:upper:]' '[:lower:]')"
        if [[ "$lc" =~ sndrpihifiberry|hifiberry|iqaudio|pcm5102|i2s|max98357 ]]; then
          idx="$n"; id="$ident"; break
        fi
      fi
    done < /tmp/aplay_list

    # Fallback: first card
    if [[ -z "$idx" ]]; then
      if [[ $(awk '/^card [0-9]+:/{print $2; exit}' /tmp/aplay_list) =~ ([0-9]+) ]]; then
        idx="${BASH_REMATCH[1]}"
      fi
      id="$(awk -F'[:, ]+' '/^card [0-9]+:/{print $3; exit}' /tmp/aplay_list)"
    fi
  fi
  echo "${idx:-0}|${id:-unknown}"
}

CARD_INDEX="0"
CARD_ID="unknown"
IFS='|' read -r CARD_INDEX CARD_ID <<<"$(detect_card)"
echo "==> ALSA card index = $CARD_INDEX (id: $CARD_ID)"

# ---------- write /etc/asound.conf ----------
ASOUND="/etc/asound.conf"
echo "==> Writing $ASOUND ..."
cp -a "$ASOUND" "$ASOUND.bak.$(date +%Y%m%d-%H%M%S)" 2>/dev/null || true

cat >"$ASOUND" <<EOF
# Auto-generated by FirePi setup

# Hardware: I2S DAC (card ${CARD_INDEX}, device 0)
pcm.i2s_hw {
  type hw
  card ${CARD_INDEX}
  device 0
}

# Route mono -> both channels (duplicate L to L+R)
pcm.route_out {
  type route
  slave.pcm "i2s_hw"
  slave.channels 2
  ttable.0.0 1   # L -> L
  ttable.0.1 1   # L -> R
}

# Software volume control "PCM" inserted on default path
# (This creates a simple mixer control for amixer/our UI.)
pcm.!default {
  type softvol
  slave.pcm "route_out"
  control { name "PCM"; card ${CARD_INDEX} }
  min_dB -51.0
  max_dB 0.0
}

ctl.!default {
  type hw
  card ${CARD_INDEX}
}
EOF

# ---------- create systemd unit (enabled, not started) ----------
UNIT_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
echo "==> Writing ${UNIT_FILE} ..."
cat >"$UNIT_FILE" <<EOF
[Unit]
Description=FirePi Monitor
After=network-online.target sound.target
Wants=network-online.target

[Service]
Type=simple
User=${RUN_USER}
Group=${RUN_GROUP}
WorkingDirectory=${APP_DIR}
Environment=PYTHONUNBUFFERED=1
ExecStart=${PYTHON_BIN} ${APP_DIR}/app.py
Restart=always
RestartSec=2
# Give ALSA init a moment after boot
ExecStartPre=/bin/sleep 2

[Install]
WantedBy=multi-user.target
EOF

echo "==> Enabling service (will start after reboot)..."
systemctl daemon-reload
systemctl enable "${SERVICE_NAME}.service"

HOST_NAME="$(hostname -f 2>/dev/null || hostname)"
if [[ "$HOST_NAME" != *.* ]]; then
  HOST_NAME="${HOST_NAME}.local"
fi
HOST_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
SELF_URL_HOST="http://${HOST_NAME}:5000/"
SELF_URL_IP="http://${HOST_IP}:5000/"

# ---------- final notes ----------
echo
echo "=============================================================="
echo "SETUP COMPLETE. A reboot is required for I²S overlay to apply."
echo "=============================================================="
echo
echo "Next steps:"
echo "  1) Reboot now:"
echo "       sudo reboot"
echo
echo "  2) After reboot, verify audio device and service:"
echo "       aplay -l"
echo "       cat /etc/asound.conf"
echo "       amixer -M scontrols         # should show 'Simple mixer control \"PCM\"'"
echo "       amixer -M sget PCM          # get current volume"
echo "       systemctl status ${SERVICE_NAME}.service"
echo "       journalctl -u ${SERVICE_NAME}.service -b --no-pager"
echo
echo "  3) Audio test (use a wav):"
echo "       aplay -D default /path/to/sound.wav"
echo "    or mpg123 -q /path/to/sound.mp3"
echo
echo "  4) Open the app:"
echo "       ${SELF_URL_HOST}"
echo "    or ${SELF_URL_IP}"
echo
echo "Changes made:"
echo "  - Installed: python3-venv, python3-pip, alsa-utils, mpg123, sox, git"
echo "  - Disabled: pigpiod.service"
echo "  - Updated:  ${CONFIG_TXT}  (dtparam=audio=off, dtoverlay=${OVERLAY})"
echo "  - Wrote:    /etc/asound.conf  (softvol 'PCM', mono->stereo route, card index ${CARD_INDEX})"
echo "  - Created:  ${UNIT_FILE}"
echo "  - Enabled:  ${SERVICE_NAME}.service (starts on next boot)"
echo