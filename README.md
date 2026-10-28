# mihomo-lite

[![Test](https://github.com/wangmancang520/mihomo-lite/actions/workflows/test.yml/badge.svg)](https://github.com/wangmancang520/mihomo-lite/actions/workflows/test.yml)

面向 Ubuntu/Debian + systemd 的轻量 Mihomo 管理脚本，支持 x86_64 和 ARM64。提供中文分组菜单、订阅自动更新、国内镜像下载、本地配置覆盖、旧版迁移和管理脚本自更新。核心使用官方二进制，不捆绑、不修改核心。

## 一键安装 / 从旧版升级

使用 root 执行，国内镜像入口：

```bash
bash <(curl -fsSL --connect-timeout 15 --max-time 120 https://ghfast.top/https://raw.githubusercontent.com/wangmancang520/mihomo-lite/main/install.sh)
```

可直接访问 GitHub 时：

```bash
bash <(curl -fsSL --connect-timeout 15 --max-time 120 https://raw.githubusercontent.com/wangmancang520/mihomo-lite/main/install.sh)
```

安装器下载并校验管理脚本，补齐必要的系统依赖，然后打开菜单。新机器选择“首次安装向导”，按提示粘贴完整 Mihomo 订阅。订阅地址隐藏输入，不需要写进命令行。

**已有 0.1/0.2 安装也运行同一条命令。** 安装器保留现有订阅地址、核心、配置、运行状态、开机自启和定时器，不重复安装代理，也不主动重启。旧版配置中的监听地址、端口、Docker 白名单、认证、DNS/TUN、控制接口等网络设置会迁移到本地覆盖文件。迁移前备份到 `/var/lib/mihomo-lite/backups/`。

首次安装时若无法访问公共下载代理，可下载 `install.sh` 后使用自己可用的 HTTPS 镜像前缀：

```bash
MIHOMO_DOWNLOAD_MIRRORS='https://your-mirror.example/' bash install.sh
```

前缀需要支持 `https://镜像/https://github.com/...` 及 raw GitHub 地址。公共镜像可达性取决于机房网络，不承诺永久可用。安装器和管理脚本校验 SHA256 可发现损坏或版本不匹配；元数据与代码来自相同第三方镜像时，不能代替独立的来源认证，因此应使用你信任的下载源。

无交互地只安装/迁移管理脚本：

```bash
bash install.sh --no-menu
```

安装器通过 apt 安装缺少的依赖，不会使用 root pip。目标需为运行 systemd 的 Linux；Docker 容器内通常不满足此条件。

## 菜单

```bash
mihomo-lite
```

| 主菜单 | 操作 |
|---|---|
| 1. 安装 / 状态 | 未安装时为向导，已安装时查看状态 |
| 2. 服务控制 | 启动、停止、重启、开机自启开关 |
| 3. 订阅管理 | 更新、更换地址、定时任务 |
| 4. 日志与诊断 | 运行/订阅/GEO 日志、配置校验 |
| 5. 设置与维护 | 下载镜像、GEO、核心升级、自动更新、本地覆盖、访问范围、管理脚本更新 |

各级菜单 `0` 返回，主菜单 `0` 退出。取消、操作失败和 Ctrl+C 返回菜单；更新中断时先恢复旧文件。日志、状态和定时任务禁用分页器。完整文件编辑作为高级操作保留，进入前显示 nano/vi 的退出方法。

## 订阅原文与本地覆盖

| 文件 | 用途 |
|---|---|
| `/etc/mihomo-lite.env` | 订阅 URL、下载镜像、核心版本等管理设置，权限 600 |
| `/etc/mihomo-lite/subscription.yaml` | 下载的完整订阅原文，逐字节保存，可能为 JSON |
| `/etc/mihomo-lite/overrides.yaml` | 本地设置，订阅更新不会覆盖 |
| `/etc/mihomo-lite/config.yaml` | 合并后供 Mihomo 运行的配置 |

字典递归合并，列表和标量整体替换。覆盖为空时运行配置与订阅原文一致。不要再直接修改 `config.yaml`：需要长期保留的设置写入 `overrides.yaml`，随后执行 `apply-overrides`，或使用菜单中的覆盖编辑器。覆盖编辑器在临时文件中编辑，校验及启动检查通过才应用；失败恢复旧配置。

旧版升级时，首次保存的 `subscription.yaml` 是原运行配置的快照；第一次正常订阅更新后才成为远程订阅的最新原文。迁移不覆盖现有 `overrides.yaml`，再次运行安装器也不重新导入旧设置。

### 允许 CMS 等 Docker 容器使用宿主机代理

先确定实际 Docker 网段：

```bash
docker network inspect bridge --format '{{range .IPAM.Config}}{{.Subnet}}{{end}}'
```

假设返回 `172.17.0.0/16`：

```bash
mihomo-lite set-access docker 172.17.0.0/16
```

该操作设置允许本机和指定 Docker 网段连接，保留原有端口、认证及其他配置。订阅更新后仍然生效。实际代理地址使用宿主机在该网桥上的地址，例如 CMS 填 `http://172.17.0.1:7890`，其中端口以你的配置为准。

恢复仅本机访问：

```bash
mihomo-lite set-access local
```

`allow-lan` 与监听地址控制代理入口；公网访问还受防火墙与云安全组影响。该脚本不主动改防火墙，也不自动配置 TUN 路由。容器内访问宿主机的地址、Docker daemon 拉镜像代理、容器内部应用代理是不同配置。

## 日常命令

```bash
mihomo-lite status
mihomo-lite update                 # 更新订阅，合并本地覆盖
mihomo-lite update-geo             # 更新 GEO 数据
mihomo-lite check
mihomo-lite start
mihomo-lite stop
mihomo-lite restart
mihomo-lite timers
mihomo-lite timer-on
mihomo-lite timer-off
mihomo-lite boot-on
mihomo-lite boot-off
mihomo-lite downloads
mihomo-lite edit-overrides
mihomo-lite apply-overrides
mihomo-lite self-update            # 管理脚本更新，与核心升级独立
mihomo-lite update-core v1.19.33    # 示例：显式选择核心版本
mihomo-lite --version
```

管理脚本自更新读取仓库 `release.json`，下载指定版本 Release 资产；检查版本、SHA256 和 Bash 语法，备份后原子替换。失败保持旧管理脚本；拒绝自动降级。核心不跟随管理脚本自动升级。相同版本重复安装可修复管理脚本；`self-update` 检测同版本时不会重复部署。

管理脚本更新不重写现有 systemd unit；当前 service/timer 命令及路径保持兼容。新版本菜单在重新打开后生效。

## 自动更新与下载

- 默认每天 00、06、12、18 点更新订阅，周日 04:20 更新 GEO；随机延迟最多 3 分钟，使用 VPS 系统时区。
- 内容没有变化时不重启。服务原先停止时只更新文件，不主动启动。
- 下载、配置校验失败保留旧版。提交后检查进程 8 秒；启动失败或崩溃循环则回滚并尝试恢复旧服务。进程检查不代表所有节点和网络请求可用。
- 维护任务使用互斥文件锁；等待订阅输入不占用更新锁。
- 核心/默认 GEO 下载依次尝试配置镜像和官方直连。订阅 URL 不发给 GitHub 镜像。
- `DOWNLOAD_PROXY` 可设置为本机代理；脚本借用代理失败后尝试直连。
- 默认核心 v1.19.33 内置两个架构的官方 SHA256；其他版本可用 `CORE_SHA256` 指定对应压缩包哈希。
- GEO 包括 Country.mmdb、GeoIP.dat、GeoSite.dat、ASN.mmdb；地址优先级为管理设置 > 有效配置的 geox-url > 默认来源。
- proxy-providers、rule-providers、UI 等额外资源仍按 Mihomo 配置管理，需要在国内可访问；不会自动改写其地址。

修改定时计划请编辑对应 `.timer` 文件的 `OnCalendar`，再执行 `systemctl daemon-reload` 并重启对应 timer。env 中的 SCHEDULE 仅用于初次生成 unit，升级保留现有计划。

## 备份与排错

升级备份位于 `/var/lib/mihomo-lite/backups/manager-upgrade.*`，包含旧管理脚本、管理设置及已有配置。若需回退管理脚本，将该目录的 `manager` 复制回 `/usr/local/sbin/mihomo-lite`。回退到旧版后将失去新版本覆盖功能，应同时暂停订阅 timer，防止旧版更新覆盖运行配置。

```bash
journalctl -u mihomo-lite.service -n 100 --no-pager
journalctl -u mihomo-lite-update.service -n 100 --no-pager
cat /var/lib/mihomo-lite/last-check.log
```

设置与检查日志可能含订阅、节点和认证信息，分享前脱敏。普通订阅/数据库/核心更新保留当前事务的回滚副本，不作为长期历史备份。

## 测试与发布

测试与结果见 [TEST-REPORT.md](TEST-REPORT.md)，GitHub Actions 在 Ubuntu 24.04 上运行真实核心和 systemd 集成测试。此结果不等于已验证你的 Ubuntu 26 VPS、TUN、Docker 路由或国内镜像可达性。

```bash
python3 tests/test_local.py
MIHOMO_TEST_REAL_CORE=/absolute/path/to/mihomo python3 tests/test_local.py
MIHOMO_TEST_REAL_CORE=/absolute/path/to/mihomo python3 tests/test_native.py
```

需要 Python 3、PyYAML、Bash 和 curl。`tests/test_systemd.sh` 只允许在一次性 GitHub Actions runner 中运行，拒绝修改普通主机。

维护者发布新版本：修改脚本 `VERSION` 与更新记录，运行 `python3 scripts/build_release.py`，提交生成的 `release.json`；创建对应 `vX.Y.Z` Release，并上传 `dist/` 生成的资产。同版本已发布资产应保持不可变。发布时先准备并验证资产，再将 Release 设为公开可用。

MIT License。Mihomo 核心按其自身许可证分发，本仓库不包含核心二进制。
