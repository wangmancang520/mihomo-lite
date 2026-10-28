#!/usr/bin/env bash
# Real service integration. Restricted to a disposable GitHub Actions runner.
set -Eeuo pipefail
[[ ${GITHUB_ACTIONS:-} == true && $EUID == 0 ]] || {
  echo 'CI-only: refuses to change services on a normal machine.' >&2; exit 1;
}
[[ -d /run/systemd/system ]] || { echo 'systemd is required'; exit 1; }
[[ ! -e /etc/systemd/system/mihomo-lite.service ]] || { echo 'Existing install detected; refusing'; exit 1; }
test_root=$(mktemp -d)
http_pid=
cleanup() {
  systemctl disable --now mihomo-lite-update.timer mihomo-lite-update-geo.timer mihomo-lite.service >/dev/null 2>&1 || true
  if [[ -n "$http_pid" ]]; then kill "$http_pid" 2>/dev/null || true; fi
  rm -f /etc/systemd/system/mihomo-lite.service /etc/systemd/system/mihomo-lite-update.{service,timer} /etc/systemd/system/mihomo-lite-update-geo.{service,timer}
  systemctl daemon-reload
  rm -rf "$test_root"
}
trap cleanup EXIT
bash ./mihomo-lite init
install -m 755 "$MIHOMO_TEST_LINUX_CORE" /usr/local/lib/mihomo-lite/mihomo
cat > /etc/mihomo-lite/config.yaml <<'EOF'
mixed-port: 17901
allow-lan: false
bind-address: 127.0.0.1
mode: rule
proxies: []
rules:
  - MATCH,DIRECT
EOF
cat >> /etc/mihomo-lite.env <<'EOF'
SUB_SCHEDULE='2099-01-01 00:00:00'
GEO_SCHEDULE='2099-01-02 00:00:00'
EOF
# Use the production unit generator; remove only the main invocation.
sed '$d' ./mihomo-lite > "$test_root/library.sh"
bash -c 'source "$1"; load_settings; write_units' bash "$test_root/library.sh"
systemd-analyze verify /etc/systemd/system/mihomo-lite*.service /etc/systemd/system/mihomo-lite*.timer
systemctl enable --now mihomo-lite.service mihomo-lite-update.timer mihomo-lite-update-geo.timer
printf 'REAL_SYSTEMD_PROXY_OK\n' > "$test_root/index.html"
python3 -m http.server 19090 --bind 127.0.0.1 --directory "$test_root" > "$test_root/http.log" 2>&1 &
http_pid=$!
for attempt in {1..30}; do
  if curl --noproxy '' --proxy http://127.0.0.1:17901 --max-time 3 -fsS http://127.0.0.1:19090/ > "$test_root/response"; then break; fi
  sleep 1
done
grep -q REAL_SYSTEMD_PROXY_OK "$test_root/response"
old_pid=$(systemctl show mihomo-lite.service -p MainPID --value)
kill -KILL "$old_pid"
new_pid=0
for attempt in {1..30}; do
  sleep 1
  new_pid=$(systemctl show mihomo-lite.service -p MainPID --value)
  if [[ $new_pid != 0 && $new_pid != "$old_pid" ]] && systemctl is-active --quiet mihomo-lite.service; then break; fi
done
[[ $new_pid != 0 && $new_pid != "$old_pid" ]]
systemctl is-active --quiet mihomo-lite-update.timer
systemctl is-active --quiet mihomo-lite-update-geo.timer
systemctl stop mihomo-lite.service
if systemctl is-active --quiet mihomo-lite.service; then exit 1; fi
echo 'PASS: real systemd units, timers, proxy HTTP forwarding, crash restart and stop'
