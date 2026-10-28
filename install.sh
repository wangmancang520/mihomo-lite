#!/usr/bin/env bash
# Install/update only the manager first; a legacy proxy is never reinstalled.
set -Eeuo pipefail
umask 077
REPOSITORY=wangmancang520/mihomo-lite
SELF=/usr/local/sbin/mihomo-lite
SCRATCH=
cleanup() { [[ -z "$SCRATCH" ]] || rm -rf -- "$SCRATCH"; }
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
die() { printf '%s\n' "$*" >&2; exit 1; }
[[ $(uname -s) == Linux ]] || die '需要 Linux + systemd（Ubuntu/Debian）。'
(( EUID == 0 )) || die '请使用 root，或 sudo bash install.sh。'
[[ -d /run/systemd/system ]] || die '需要运行中的 systemd。'
NO_MENU=0
while (($#)); do
  case $1 in
    --no-menu) NO_MENU=1 ;;
    -h|--help) printf '%s\n' '用法：bash install.sh [--no-menu]；已安装时安全迁移，仅替换管理脚本。'; exit 0 ;;
    *) die "未知选项：$1" ;;
  esac
  shift
done

# Dependencies come from the operating system package manager, never pip as root.
missing=0
for tool in curl python3 gzip sha256sum timeout flock; do
  command -v "$tool" >/dev/null || missing=1
done
if command -v python3 >/dev/null; then
  python3 -c 'import yaml' 2>/dev/null || missing=1
fi
if ((missing)); then
  command -v apt-get >/dev/null || die '请先安装 curl python3 python3-yaml gzip coreutils util-linux。'
  apt-get update
  apt-get install -y ca-certificates curl python3 python3-yaml gzip coreutils util-linux
fi
SCRATCH=$(mktemp -d /tmp/mihomo-lite-installer.XXXXXXXX)
fetch() {
  local url=$1 output=$2 prefix
  # Override with a space-separated list of trusted HTTPS proxy prefixes.
  for prefix in ${MIHOMO_DOWNLOAD_MIRRORS:-https://ghfast.top/ https://gh-proxy.com/}; do
    [[ $prefix == https://* ]] || die '下载镜像必须使用 HTTPS。'
    if curl --disable --proto '=https' --proto-redir '=https' --fail --silent --location \
      --connect-timeout 12 --max-time 120 --retry 1 \
      "${prefix%/}/$url" --output "$output"; then
      [[ ! -s "$output" ]] || return 0
    fi
  done
  curl --disable --proto '=https' --proto-redir '=https' --fail --silent --location \
    --connect-timeout 12 --max-time 120 --retry 1 "$url" --output "$output" \
    && [[ -s "$output" ]]
}

printf '%s\n' '正在读取管理脚本发布版本…'
fetch "https://raw.githubusercontent.com/$REPOSITORY/main/release.json" "$SCRATCH/release.json" || die '无法获取发布版本，未修改现有安装。'
python3 - "$SCRATCH/release.json" "$SELF" > "$SCRATCH/fields" <<'PY'
import json, re, sys
from pathlib import Path
m = json.load(open(sys.argv[1])); version, sha = m.get('version',''), m.get('sha256','')
if not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+',version) or not re.fullmatch(r'[a-f0-9]{64}',sha):
    raise SystemExit('发布元数据无效')
installed = Path(sys.argv[2])
if installed.exists():
    match = re.search(r'^VERSION=[\'"]?([0-9]+\.[0-9]+\.[0-9]+)', installed.read_text(), re.M)
    if match and tuple(map(int,version.split('.'))) < tuple(map(int,match[1].split('.'))):
        raise SystemExit('拒绝降级已有管理脚本')
print(version); print(sha)
PY
version=$(sed -n '1p' "$SCRATCH/fields")
expected=$(sed -n '2p' "$SCRATCH/fields")
fetch "https://github.com/$REPOSITORY/releases/download/v$version/mihomo-lite" "$SCRATCH/mihomo-lite" || die '下载失败，旧版保持不变。'
actual=$(sha256sum "$SCRATCH/mihomo-lite"); actual=${actual%% *}
[[ $actual == "$expected" ]] || die 'SHA256 校验失败，旧版保持不变。'
bash -n "$SCRATCH/mihomo-lite"
[[ $(bash "$SCRATCH/mihomo-lite" --version) == "$version" ]] || die '管理脚本版本不符。'
# migrate backs up the old manager and captures legacy network settings. It does
# not restart the proxy, change its core, fetch a subscription, or reset timers.
bash "$SCRATCH/mihomo-lite" migrate
printf '%s\n' '管理脚本安装/升级完成。已保留已有订阅、核心、配置和服务。'
if (( ! NO_MENU )) && [[ -t 0 && -t 1 ]]; then
  bash "$SELF" menu
else
  printf '%s\n' '执行 sudo mihomo-lite 打开菜单；首次使用选择安装向导。'
fi
