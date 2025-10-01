sudo systemd-run --unit firepi-ap-provision --on-active=5s --property=Type=oneshot bash -lc '
  PSK="FirePiTest123"   # <-- change if you want
  set -e -o pipefail

  # pick Wi-Fi iface
  IFACE=$(nmcli -t -f DEVICE,TYPE dev status | awk -F: '"'"'$2=="wifi"{print $1; exit}'"'"'); [ -z "$IFACE" ] && IFACE=wlan0

  # persist PSK for FirePi scripts
  echo "$PSK" > /var/lib/firepi/ap_psk; chmod 0644 /var/lib/firepi/ap_psk

  # clean any old hotspot profile
  nmcli con down Hotspot 2>/dev/null || true
  nmcli con delete Hotspot 2>/dev/null || true
  nmcli con delete FirePiAP 2>/dev/null || true

  # create explicit, compatible AP profile
  nmcli con add type wifi ifname "$IFACE" con-name FirePiAP autoconnect no ssid "FirePi-TEST"
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
    wifi-sec.pmf disable

  # bring it up (this will drop your SSH)
  nmcli con up FirePiAP
'
