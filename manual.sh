APP_HOME=/home/chris/firepi
echo '{"ssid":"wSecLan","psk":"sweetac!"}' > "$APP_HOME/instance/pending_wifi.json"
APP_HOME="$APP_HOME" FIREPI_PENDING_WIFI="$APP_HOME/instance/pending_wifi.json" \
  "$APP_HOME/wifi_scripts/firepi-wifi-switch.sh"
