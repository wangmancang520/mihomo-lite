# 测试报告：mihomo-lite 0.3.0

日期：2026-10-28。

## 本地执行结果

- 54/54 项脚本入口及伪终端交互测试通过，配置校验接入官方原生 Mihomo v1.19.33。
- 3/3 项原生核心测试通过：版本检查、错误配置拒绝、真实本地 HTTP 代理转发。
- Bash/Python 语法检查通过。Release 使用标准 USTAR 格式，核对包内文件字节，不携带 macOS 扩展属性或 `._` 文件。

本地环境为 macOS ARM64、Bash 3.2、Python 3.12、PyYAML 6.0.3。54 项测试从真实脚本入口运行，不替换菜单函数；systemd、下载和 Linux 平台接口在隔离环境中模拟。文件操作、SHA256、gzip、设置保存、合并与事务回滚执行真实实现，使用 fcntl 进行真实文件锁测试。GEO 与管理脚本安装包使用可控夹具，不把它们计为真实外网下载测试。

官方 macOS ARM64 核心压缩包 SHA256 与 GitHub 发布元数据匹配：`060b64b0f2c9a310831db0fe4adb2878de67bfa606c311813c1f13966aa504b6`。

## 重点回归范围

- 所有主菜单、二/三级菜单、非法输入、回车/0 取消、EOF、Ctrl+C 和失败返回。
- 隐藏订阅输入不回显，中断后恢复终端；特殊字符安全保存。
- 服务、日志、自动更新及开机自启操作；设置损坏/缺失时仍可停止和诊断。
- 安装、原文保存、内容无变化、手动停止后更新、配置错误、启动失败、崩溃循环、事务中断和互斥锁。
- 旧版 0.2 实际管理脚本备份，兼容原安装路径；保留核心、管理设置、运行配置、服务状态和 Docker 访问配置。
- 订阅原文和本地覆盖分离；新订阅仍保留 Docker 网段、递归字典合并和列表替换；拒绝全公网访问配置。
- 自更新成功与备份、哈希不符、防降级、同版本跳过。
- 一键安装器全新初始化、旧版升级不重启、哈希不符保护原安装。
- 镜像失败后直连、下载代理失败后直连、订阅 URL 不发送给 GitHub 镜像。

原始结果见 `tests/local-results.txt` 和 `tests/native-results.txt`，脚本自带测试代码。

## Ubuntu CI

[GitHub Actions](https://github.com/wangmancang520/mihomo-lite/actions/workflows/test.yml) 在 Ubuntu 24.04 上运行同一组流程测试、Linux 原生核心测试，以及真实 systemd 集成：生成并验证 unit、启动服务、HTTP 转发、杀死核心后自动重启、timer 激活和停止服务。各次提交以 Actions 状态为准。

`tests/test_systemd.sh` 限制为一次性 GitHub Actions runner 使用，拒绝在普通主机上修改服务。此前发布的 0.2 本地报告不应被解释为完成了 Linux systemd 实机测试。

## 未覆盖

Ubuntu 26 目标 VPS、用户订阅的可用节点、TUN 路由、Docker 实际网桥/防火墙，以及国内各机房到公共镜像的可达性仍取决于部署环境。测试不使用用户的真实订阅或 VPS。

复现方式见 README 的“测试与发布”。
