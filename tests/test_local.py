#!/usr/bin/env python3
"""Full entry-point and PTY tests; OS/service/download boundaries are fixtures.

Run with Python 3 + PyYAML: python3 tests/test_local.py
Optional MIHOMO_TEST_REAL_CORE=/path/to/native/mihomo uses its real config parser.
Never writes /etc, installs a real service, or uses a real subscription.
"""
import fcntl
import gzip
import hashlib
import json
import os
from pathlib import Path
import pty
import select
import shlex
import signal
import subprocess
import sys
import tempfile
import termios
import time
import unittest

SOURCE = Path(__file__).resolve().parents[1] / 'mihomo-lite'
REAL_CORE = os.environ.get('MIHOMO_TEST_REAL_CORE', '')

SHIM = r'''
import fcntl, json, os, pathlib, shutil, subprocess, sys, time
r = pathlib.Path(os.environ['ML_FIXTURE'])
name = pathlib.Path(sys.argv[0]).name
a = sys.argv[1:]
if name == 'python3':
    os.execv(os.environ['ML_PYTHON'], [os.environ['ML_PYTHON']] + a)
if name == 'uname':
    print('arm64' if '-m' in a else 'Linux')
elif name == 'stat':
    p = pathlib.Path(a[-1])
    print('0' if a[1] == '%u' else oct(p.stat().st_mode & 0o777)[2:])
elif name == 'readlink':
    print(pathlib.Path(a[-1]).resolve())
elif name == 'flock':
    fd = int(a[-1])
    end = time.monotonic() + 0.3
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except BlockingIOError:
            if time.monotonic() > end: sys.exit(1)
            time.sleep(.02)
elif name == 'timeout':
    try: sys.exit(subprocess.run(a[1:], timeout=int(a[0])).returncode)
    except subprocess.TimeoutExpired: sys.exit(124)
elif name == 'sleep':
    pass  # process stability uses fixture state, not a real service
elif name == 'systemd-analyze':
    if a[0] != 'calendar' or not a[1] or '\n' in a[1]: sys.exit(1)
elif name == 'journalctl':
    with (r/'calls').open('a') as f: f.write(json.dumps([name]+a)+'\n')
    assert '--no-pager' in a or '-f' in a, a
    print('fixture journal log')
elif name == 'systemctl':
    with (r/'calls').open('a') as f: f.write(json.dumps([name]+a)+'\n')
    if (r/'sleep-action').exists() and any(v in a for v in ('start','restart')):
        (r/'sleep-action').unlink()
        (r/'sleep-entered').touch()
        time.sleep(30)
    a = [v for v in a if v != '--no-pager']
    state_path=r/'system-state.json'
    state=json.loads(state_path.read_text()) if state_path.exists() else {'active':False,'enabled':False,'timers':False}
    cmd=a[0]
    result=0
    if cmd == 'is-active':
        if '--quiet' not in a: print('active' if state['active'] else 'inactive')
        result=0 if state['active'] else 3
    elif cmd == 'status':
        print('active' if state['active'] else 'inactive')
        result=0 if state['active'] else 3
    elif cmd in ('start','restart'):
        marker=r/'fail-once'
        if marker.exists(): marker.unlink(); state['active']=False; result=1
        else: state['active']=True
    elif cmd == 'stop': state['active']=False
    elif cmd == 'enable':
        if any(v.endswith('.timer') for v in a): state['timers']=True
        else: state['enabled']=True
    elif cmd == 'disable':
        if any(v.endswith('.timer') for v in a): state['timers']=False
        else: state['enabled']=False
    elif cmd == 'show': print('1' if (r/'crash-loop').exists() else '0')
    elif cmd == 'list-timers': print('fixture timers')
    elif cmd not in ('daemon-reload','reset-failed'): raise AssertionError(a)
    state_path.write_text(json.dumps(state))
    sys.exit(result)
elif name == 'curl':
    if (r/'fail-download').exists(): sys.exit(22)
    params={}
    if '--config' in a:
        conf=pathlib.Path(a[a.index('--config')+1])
        for line in conf.read_text().splitlines():
            k,v=line.split(' = ',1); params[k]=json.loads(v)
    else:
        params={'url':next(v for v in a if v.startswith('https://')), 'proxy':''}
    url=params['url']
    with (r/'requests').open('a') as f: f.write(json.dumps(params)+'\n')
    if (r/'fail-mirror').exists() and url.startswith('https://mirror.test/'):
        sys.exit(22)
    if (r/'fail-proxy').exists() and params['proxy']:
        sys.exit(7)
    out=pathlib.Path(a[a.index('--output')+1])
    if url.endswith('/release.json'):
        out.write_bytes((r/'release.json').read_bytes())
    elif url.endswith('/mihomo-lite'):
        out.write_bytes((r/'manager-next').read_bytes())
    elif 'subscription.test/' in url:
        assert not url.startswith('https://mirror.'), 'subscription leaked to mirror'
        out.write_bytes((r/'subscription.yaml').read_bytes())
    elif url.endswith('.gz'): shutil.copyfile(r/'core.gz',out)
    elif '.mmdb' in url:
        out.write_bytes(b'<html>error</html>' if (r/'bad-mmdb').exists() else b'x'*2048+b'\xab\xcd\xefMaxMind.com')
    elif '.dat' in url: out.write_bytes(b'x'*2048)
    else: raise AssertionError(url)
else: raise AssertionError(name)
'''


class Terminal:
    def __init__(self, app, env):
        pid, fd = pty.fork()
        if pid == 0:
            os.execvpe('bash', ['bash', str(app)], env)
        self.pid, self.fd, self.output, self.cursor = pid, fd, '', 0

    def expect(self, text, timeout=12):
        until = time.monotonic() + timeout
        while text not in self.output[self.cursor:]:
            if time.monotonic() > until:
                raise AssertionError(f'terminal timeout waiting for {text!r}\n{self.output[-2500:]}')
            ready, _, _ = select.select([self.fd], [], [], .05)
            if ready:
                try: chunk = os.read(self.fd, 65536)
                except OSError: chunk = b''
                if not chunk: raise AssertionError(f'terminal closed waiting for {text!r}\n{self.output[-2500:]}')
                self.output += chunk.decode('utf-8', errors='replace')
        self.cursor = self.output.index(text, self.cursor) + len(text)

    def send(self, value):
        os.write(self.fd, value.encode())

    def choose(self, value):
        self.send(str(value) + '\n')

    def close(self):
        try:
            # A menu may already have exited normally (0/EOF). Reap it first,
            # rather than signalling a process group that no longer exists.
            done, _ = os.waitpid(self.pid, os.WNOHANG)
            if not done:
                try: os.killpg(self.pid, signal.SIGKILL)
                except ProcessLookupError: pass
                except PermissionError:
                    try: os.kill(self.pid, signal.SIGKILL)
                    except ProcessLookupError: pass
                os.waitpid(self.pid, 0)
        except ChildProcessError:
            pass
        finally:
            os.close(self.fd)


class ManagerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='mihomo-lite-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for d in ('data','state','units','bin','installed'):
            (self.root/d).mkdir()
        self.app=self.root/'manager'
        text=SOURCE.read_text()
        replacements={
            '/etc/mihomo-lite.env':str(self.root/'settings.env'),
            '/etc/mihomo-lite':str(self.root/'data'),
            '/var/lib/mihomo-lite':str(self.root/'state'),
            '/usr/local/lib/mihomo-lite/mihomo':str(self.root/'installed/core'),
            '/usr/local/sbin/mihomo-lite':str(self.root/'installed/manager'),
            '/etc/systemd/system':str(self.root/'units'),
            '(( EUID == 0 ))':'true',
            '[[ -d /run/systemd/system ]]':'true',
        }
        for old,new in replacements.items():
            assert old in text, old
            text=text.replace(old,new)
        self.app.write_text(text)
        self.installer=self.root/'installer'
        installer=SOURCE.with_name('install.sh').read_text()
        for old,new in replacements.items():
            installer=installer.replace(old,new)
        self.installer.write_text(installer)
        legacy=SOURCE.parent/'tests/fixtures/legacy-manager-v0.2.0'
        if legacy.exists():
            text=legacy.read_text()
            for old,new in replacements.items(): text=text.replace(old,new)
            (self.root/'legacy').write_text(text)
        shim=self.root/'shim.py'
        shim.write_text('#!'+sys.executable+'\n'+SHIM)
        shim.chmod(0o755)
        for name in ('python3','uname','stat','readlink','flock','timeout','sleep','systemd-analyze','journalctl','systemctl','curl'):
            (self.root/'bin'/name).symlink_to(shim)
        core='''#!/usr/bin/env bash
if [[ ${1:-} == -v ]]; then echo 'Mihomo Meta v1.19.33 fixture'; exit; fi
if [[ -n ${MIHOMO_TEST_REAL_CORE:-} ]]; then exec "$MIHOMO_TEST_REAL_CORE" "$@"; fi
for arg; do
  if [[ -f $arg ]] && grep -q INVALID_CONFIG "$arg"; then exit 1; fi
done
exit 0
'''
        payload=gzip.compress(core.encode())
        (self.root/'core.gz').write_bytes(payload)
        self.digest=hashlib.sha256(payload).hexdigest()
        (self.root/'subscription.yaml').write_text('''# fixture subscription
mixed-port: 17890
allow-lan: false
mode: rule
proxies: []
rules:
  - MATCH,DIRECT
''')
        self.env=dict(os.environ, PATH=str(self.root/'bin')+os.pathsep+os.environ['PATH'],
                      ML_FIXTURE=str(self.root), ML_PYTHON=sys.executable)
        self.env['EDITOR']='true'
        self.env['MIHOMO_DOWNLOAD_MIRRORS']='https://mirror.test/'

    def run_cli(self, *args, input='', code=0):
        result=subprocess.run(['bash',str(self.app),*args],input=input,text=True,
                              env=self.env,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,timeout=15)
        if code is not None:
            self.assertEqual(result.returncode,code,result.stdout)
        return result

    def initialize(self):
        self.run_cli('init')
        with (self.root/'settings.env').open('a') as f:
            f.write("SUB_URL='https://subscription.test/token'\n")
            f.write('CORE_SHA256='+shlex.quote(self.digest)+'\n')
            f.write("MIRRORS=('https://mirror.test/')\n")

    def install(self):
        self.initialize()
        self.run_cli('install')

    def state(self):
        return json.loads((self.root/'system-state.json').read_text())

    def term(self):
        t=Terminal(self.app,self.env)
        self.addCleanup(t.close)
        t.expect('请选择 [0-5]：')
        return t

    def test_install_full_entrypoint(self):
        self.install()
        self.assertEqual((self.root/'data/config.yaml').read_bytes(),(self.root/'subscription.yaml').read_bytes())
        self.assertTrue(self.state()['active'] and self.state()['enabled'] and self.state()['timers'])
        self.assertEqual(len(list((self.root/'units').iterdir())),5)
        self.assertEqual((self.root/'settings.env').stat().st_mode & 0o777,0o600)
        self.assertEqual(len(list((self.root/'state').glob('txn.*'))),0)
        self.assertTrue((self.root/'installed/manager').exists())

    def test_every_menu_returns_to_main(self):
        self.install()
        t=self.term()
        for number,title in [(2,'服务控制'),(3,'订阅管理'),(4,'日志与诊断'),(5,'设置与维护')]:
            t.choose(number); t.expect('--- '+title+' ---')
            t.choose(0); t.expect('请选择 [0-5]：')
        t.choose(0)

    def test_all_service_menu_actions(self):
        self.install(); t=self.term(); t.choose(2)
        for i in range(1,6):
            t.expect('请选择（0 返回）：'); t.choose(i); t.expect('操作完成。')
        t.expect('请选择（0 返回）：'); t.choose(0); t.expect('请选择 [0-5]：')

    def test_all_diagnostics_actions(self):
        self.install(); t=self.term(); t.choose(4)
        for i in range(1,5):
            t.expect('请选择（0 返回）：'); t.choose(i); t.expect('操作完成。')
        t.expect('请选择（0 返回）：'); t.choose(0); t.expect('请选择 [0-5]：')
        calls=(self.root/'calls').read_text()
        self.assertIn('--no-pager',calls)

    def test_subscription_actions_and_hidden_cancel(self):
        self.install(); t=self.term(); t.choose(3)
        for i in (1,3):
            t.expect('请选择（0 返回）：'); t.choose(i); t.expect('操作完成。')
        for value in ('','0'):
            t.expect('请选择（0 返回）：'); t.choose(2)
            t.expect('输入隐藏；回车或 0 返回'); t.choose(value); t.expect('已取消，返回菜单。')
        t.expect('请选择（0 返回）：'); t.choose(0); t.expect('请选择 [0-5]：')

    def test_advanced_actions_and_nested_return(self):
        self.install(); t=self.term(); t.choose(5)
        t.expect('请选择（0 返回）：'); t.choose(2); t.expect('操作完成。')
        t.expect('请选择（0 返回）：'); t.choose(3); t.expect('版本号（'); t.choose('v1.19.33'); t.expect('操作完成。')
        t.expect('请选择（0 返回）：'); t.choose(4)
        for value in (1,2,3):
            t.expect('请选择（0 返回）：'); t.choose(value); t.expect('操作完成。')
        t.expect('请选择（0 返回）：'); t.choose(0); t.expect('--- 设置与维护 ---')
        t.expect('请选择（0 返回）：'); t.choose(5); t.expect('操作完成。')
        t.expect('请选择（0 返回）：'); t.choose(0); t.expect('请选择 [0-5]：')

    def test_download_settings_menu_and_cancel(self):
        self.install(); t=self.term(); t.choose(5); t.expect('请选择（0 返回）：'); t.choose(1)
        t.expect('--- 下载设置 ---'); t.expect('请选择：'); t.choose(1)
        t.expect('0 取消：'); t.choose('0'); t.expect('请选择：')
        t.choose(1); t.expect('0 取消：'); t.choose('https://new-mirror.test/ https://second.test/')
        t.expect('镜像设置已保存。'); t.expect('请选择：')
        t.choose(2); t.expect('回车清空，0 取消'); t.choose('http://127.0.0.1:7890')
        t.expect('下载代理已保存。'); t.expect('请选择：')
        t.choose(0); t.expect('--- 设置与维护 ---'); t.expect('请选择（0 返回）：')
        t.choose(0); t.expect('请选择 [0-5]：')
        self.assertIn('new-mirror.test',(self.root/'settings.env').read_text())

    def test_invalid_input_and_version_cancel(self):
        self.install(); t=self.term()
        t.choose('xxx'); t.expect('无效选项'); t.expect('请选择 [0-5]：')
        t.choose(5); t.expect('请选择（0 返回）：')
        t.choose('bad'); t.expect('无效选项'); t.expect('请选择（0 返回）：')
        for version in ('','0','nonsense'):
            t.choose(3); t.expect('版本号（'); t.choose(version)
            if version == 'nonsense': t.expect('操作失败')
            t.expect('请选择（0 返回）：')
        t.choose(0); t.expect('请选择 [0-5]：')

    def test_menu_failure_returns_and_keeps_old_config(self):
        self.install(); old=(self.root/'data/config.yaml').read_bytes()
        (self.root/'fail-download').touch()
        t=self.term(); t.choose(3); t.expect('请选择（0 返回）：'); t.choose(1)
        t.expect('操作失败'); t.expect('请选择（0 返回）：'); t.choose(0); t.expect('请选择 [0-5]：')
        self.assertEqual((self.root/'data/config.yaml').read_bytes(),old)

    def test_ctrl_c_operation_returns(self):
        self.install(); (self.root/'sleep-action').touch()
        t=self.term(); t.choose(2); t.expect('请选择（0 返回）：'); t.choose(3)
        time.sleep(.3); t.send('\x03'); t.expect('操作已中断',timeout=6)
        t.expect('请选择（0 返回）：'); t.choose(0); t.expect('请选择 [0-5]：')

    def test_ctrl_c_prompts_return(self):
        self.install(); t=self.term()
        t.send('\x03'); t.expect('请选择 [0-5]：')
        t.choose(2); t.expect('请选择（0 返回）：'); t.send('\x03')
        t.expect('请选择 [0-5]：')

    def test_interrupt_during_update_rolls_back_and_returns(self):
        self.install(); old=(self.root/'data/config.yaml').read_bytes()
        with (self.root/'subscription.yaml').open('a') as f: f.write('# changed\n')
        (self.root/'sleep-action').touch()
        t=self.term(); t.choose(3); t.expect('请选择（0 返回）：'); t.choose(1)
        until=time.monotonic()+8
        while not (self.root/'sleep-entered').exists():
            self.assertLess(time.monotonic(),until,'update did not reach restart')
            time.sleep(.03)
        t.send('\x03'); t.expect('操作已中断',timeout=8)
        t.expect('请选择（0 返回）：'); t.choose(0); t.expect('请选择 [0-5]：')
        self.assertEqual((self.root/'data/config.yaml').read_bytes(),old)
        self.assertTrue(self.state()['active'])

    def test_first_setup_cancel_returns(self):
        t=self.term(); t.choose(1); t.expect('输入隐藏；回车或 0 返回')
        t.choose(0); t.expect('已取消，返回菜单。'); t.expect('请选择 [0-5]：')
        self.assertFalse((self.root/'installed/core').exists())

    def test_setup_enters_installed_state(self):
        self.initialize(); t=self.term(); t.choose(1); t.expect('输入隐藏；回车或 0 返回')
        t.choose('https://subscription.test/new'); t.expect('服务和自动更新已启用。')
        t.expect('查看服务状态'); t.expect('请选择 [0-5]：')

    def test_secret_roundtrip_and_no_echo(self):
        self.initialize()
        secret="https://subscription.test/token?key=a'b&x=$(touch_BAD)"
        t=self.term(); t.choose(3); t.expect('请选择（0 返回）：'); t.choose(2)
        t.expect('输入隐藏；回车或 0 返回'); t.choose(secret); t.expect('操作完成。')
        self.assertNotIn(secret,t.output)
        result=subprocess.run(['bash','-c','source "$1"; printf %s "$SUB_URL"','bash',str(self.root/'settings.env')],capture_output=True,text=True)
        self.assertEqual(result.stdout,secret)

    def test_hidden_input_ctrl_c_restores_echo(self):
        self.initialize(); t=self.term(); t.choose(3); t.expect('请选择（0 返回）：'); t.choose(2)
        t.expect('输入隐藏；回车或 0 返回')
        self.assertFalse(termios.tcgetattr(t.fd)[3] & termios.ECHO)
        t.send('\x03'); t.expect('操作已中断'); t.expect('请选择（0 返回）：')
        self.assertTrue(termios.tcgetattr(t.fd)[3] & termios.ECHO)
        t.choose(0); t.expect('请选择 [0-5]：')

    def test_unchanged_subscription_no_restart(self):
        self.install(); (self.root/'calls').write_text(''); self.run_cli('update')
        calls=[json.loads(v) for v in (self.root/'calls').read_text().splitlines()]
        self.assertFalse(any('restart' in v for v in calls))

    def test_changed_subscription_restart_and_raw_preservation(self):
        self.install()
        with (self.root/'subscription.yaml').open('a') as f: f.write('# keep this comment\n')
        self.run_cli('update')
        self.assertEqual((self.root/'data/config.yaml').read_bytes(),(self.root/'subscription.yaml').read_bytes())

    def test_invalid_subscription_preserves_running_files(self):
        self.install(); old=(self.root/'data/config.yaml').read_bytes()
        (self.root/'subscription.yaml').write_text('INVALID_CONFIG: [\n')
        self.assertNotEqual(self.run_cli('update',code=None).returncode,0)
        self.assertEqual((self.root/'data/config.yaml').read_bytes(),old)
        self.assertTrue(self.state()['active'])

    def test_restart_failure_rolls_back(self):
        self.install(); old=(self.root/'data/config.yaml').read_bytes()
        with (self.root/'subscription.yaml').open('a') as f: f.write('# changed\n')
        (self.root/'fail-once').touch()
        self.assertNotEqual(self.run_cli('update',code=None).returncode,0)
        self.assertEqual((self.root/'data/config.yaml').read_bytes(),old)
        self.assertTrue(self.state()['active'])

    def test_stopped_service_stays_stopped_after_update(self):
        self.install(); self.run_cli('stop')
        with (self.root/'subscription.yaml').open('a') as f: f.write('# changed\n')
        self.run_cli('update'); self.assertFalse(self.state()['active'])

    def test_service_controls_work_with_broken_settings(self):
        self.install(); (self.root/'settings.env').write_text("SUB_URL='broken\n")
        for cmd in ('stop','start','restart','status','recent-logs','update-logs','geo-logs','timers','timer-off','timer-on','boot-off','boot-on'):
            self.run_cli(cmd)

    def test_write_lock_releases_and_prompt_does_not_lock(self):
        self.install()
        t=self.term(); t.choose(3); t.expect('请选择（0 返回）：'); t.choose(2)
        t.expect('输入隐藏；回车或 0 返回')
        self.run_cli('update')
        t.choose(0); t.expect('已取消，返回菜单。')
        with (self.root/'state/manager.lock').open('a') as lock:
            fcntl.flock(lock,fcntl.LOCK_EX)
            result=self.run_cli('update',code=None)
            self.assertNotEqual(result.returncode,0)
            self.assertIn('另一个管理任务',result.stdout)
        self.run_cli('update')

    def test_input_validation_and_cancel_no_change(self):
        self.initialize(); old=(self.root/'settings.env').read_bytes()
        self.run_cli('set-subscription',input='\n',code=10)
        self.run_cli('set-subscription',input='http://invalid.test/a\n',code=1)
        self.assertEqual((self.root/'settings.env').read_bytes(),old)

    def test_checksum_mismatch_preserves_core(self):
        self.install(); old=(self.root/'installed/core').read_bytes()
        with (self.root/'settings.env').open('a') as f: f.write("CORE_SHA256='"+'0'*64+"'\n")
        self.assertNotEqual(self.run_cli('update-core','v1.19.33',code=None).returncode,0)
        self.assertEqual((self.root/'installed/core').read_bytes(),old)

    def test_download_urls_and_subscription_not_mirrored(self):
        self.install()
        requests=[json.loads(v)['url'] for v in (self.root/'requests').read_text().splitlines()]
        self.assertTrue(any(v.startswith('https://mirror.test/https://github.com/') for v in requests))
        self.assertEqual([v for v in requests if 'subscription.test/' in v],['https://subscription.test/token'])

    def test_noninteractive_menu_exits_instead_of_hanging(self):
        result=self.run_cli('menu',code=None)
        self.assertEqual(result.returncode,1)
        self.assertIn('交互菜单需要终端',result.stdout)

    def test_first_start_failure_and_retry(self):
        self.initialize(); (self.root/'fail-once').touch()
        result=self.run_cli('install',code=None)
        self.assertNotEqual(result.returncode,0)
        self.assertFalse((self.root/'data/config.yaml').exists())
        self.assertFalse((self.root/'installed/core').exists())
        self.run_cli('install')
        self.assertTrue(self.state()['active'])

    def test_process_crash_loop_rolls_back(self):
        self.install(); old=(self.root/'data/config.yaml').read_bytes()
        with (self.root/'subscription.yaml').open('a') as f: f.write('# changed\n')
        (self.root/'crash-loop').touch()
        self.assertNotEqual(self.run_cli('update',code=None).returncode,0)
        self.assertEqual((self.root/'data/config.yaml').read_bytes(),old)

    def test_core_upgrade_bad_version_leaves_running_core(self):
        self.install(); old=(self.root/'installed/core').read_bytes()
        self.assertNotEqual(self.run_cli('update-core','v9.9.9',code=None).returncode,0)
        self.assertEqual((self.root/'installed/core').read_bytes(),old)

    def test_html_mmdb_rejected_preserves_old_files(self):
        self.install(); old=(self.root/'data/Country.mmdb').read_bytes()
        (self.root/'bad-mmdb').touch()
        self.assertNotEqual(self.run_cli('update-geo',code=None).returncode,0)
        self.assertEqual((self.root/'data/Country.mmdb').read_bytes(),old)

    def test_mirror_failure_falls_back_to_official(self):
        self.initialize(); (self.root/'fail-mirror').touch(); self.run_cli('install')
        urls=[json.loads(v)['url'] for v in (self.root/'requests').read_text().splitlines()]
        self.assertTrue(any(v.startswith('https://github.com/') for v in urls))
        self.assertTrue(any(v.startswith('https://mirror.test/') for v in urls))

    def test_proxy_failure_retries_direct(self):
        self.initialize()
        with (self.root/'settings.env').open('a') as f: f.write("DOWNLOAD_PROXY='http://127.0.0.1:7890'\n")
        (self.root/'fail-proxy').touch(); self.run_cli('install')
        proxies=[json.loads(v)['proxy'] for v in (self.root/'requests').read_text().splitlines()]
        self.assertIn('',proxies); self.assertIn('http://127.0.0.1:7890',proxies)

    def test_valid_yaml_invalid_mihomo_configuration(self):
        self.install(); old=(self.root/'data/config.yaml').read_bytes()
        (self.root/'subscription.yaml').write_text('''# INVALID_CONFIG
proxies:
  - name: bad
    type: nonexistent_proxy_protocol
rules: [MATCH,DIRECT]
''')
        self.assertNotEqual(self.run_cli('update',code=None).returncode,0)
        self.assertEqual((self.root/'data/config.yaml').read_bytes(),old)

    def test_download_settings_clear_and_invalid(self):
        self.initialize()
        self.run_cli('downloads',input='1\n\n2\n\n0\n')
        self.assertIn('MIRRORS=()', (self.root/'settings.env').read_text())
        old=(self.root/'settings.env').read_bytes()
        self.run_cli('downloads',input='1\nhttp://invalid.test/\n',code=1)
        self.assertEqual((self.root/'settings.env').read_bytes(),old)

    def test_status_inactive_is_normal_result(self):
        self.install(); self.run_cli('stop'); self.run_cli('status')

    def test_eof_download_settings_returns(self):
        self.initialize(); self.run_cli('downloads',code=10)

    def test_empty_mirror_list_uses_official(self):
        self.initialize(); self.run_cli('downloads',input='1\n\n0\n'); self.run_cli('install')
        self.assertTrue(self.state()['active'])
        urls=[json.loads(v)['url'] for v in (self.root/'requests').read_text().splitlines()]
        self.assertFalse(any(v.startswith('https://mirror.test/') for v in urls))

    def test_installed_command_executes_update(self):
        self.install()
        result=subprocess.run(['bash',str(self.root/'installed/manager'),'update'],env=self.env,capture_output=True,text=True,timeout=15)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)

    def test_service_controls_with_missing_settings(self):
        self.install(); (self.root/'settings.env').unlink()
        self.run_cli('stop'); self.run_cli('status'); self.run_cli('recent-logs')
        self.assertFalse((self.root/'settings.env').exists())

    def prepare_release(self, version='0.3.1'):
        candidate=self.app.read_text().replace('VERSION=0.3.0','VERSION='+version,1)
        (self.root/'manager-next').write_text(candidate)
        (self.root/'release.json').write_text(json.dumps({'version':version,'sha256':hashlib.sha256(candidate.encode()).hexdigest()}))

    def test_legacy_migration_keeps_core_settings_and_network_access(self):
        self.install()
        (self.root/'data/subscription.yaml').unlink(); (self.root/'data/overrides.yaml').unlink()
        import yaml
        path=self.root/'data/config.yaml'; config=yaml.safe_load(path.read_text())
        config.update({'allow-lan':True,'bind-address':'*','lan-allowed-ips':['127.0.0.1/32','172.17.0.0/16']})
        path.write_text(json.dumps(config))
        shutil_payload=(self.root/'legacy').read_bytes()
        (self.root/'installed/manager').write_bytes(shutil_payload)
        old_env=(self.root/'settings.env').read_bytes(); old_core=(self.root/'installed/core').read_bytes()
        old_config=path.read_bytes(); old_state=self.state(); (self.root/'calls').write_text('')
        self.run_cli('migrate')
        self.assertEqual((self.root/'settings.env').read_bytes(),old_env)
        self.assertEqual((self.root/'installed/core').read_bytes(),old_core)
        self.assertEqual(path.read_bytes(),old_config)
        self.assertEqual(self.state(),old_state)
        self.assertNotIn('restart',(self.root/'calls').read_text())
        overrides=yaml.safe_load((self.root/'data/overrides.yaml').read_text())
        self.assertTrue(overrides['allow-lan']); self.assertEqual(overrides['bind-address'],'*')
        self.assertTrue(list((self.root/'state/backups').glob('manager-upgrade.*/manager')))
        self.run_cli('update')
        self.assertTrue(yaml.safe_load(path.read_text())['allow-lan'])

    def test_override_survives_subscription_updates_and_raw_is_exact(self):
        self.install(); self.run_cli('set-access','docker','172.17.0.0/16')
        with (self.root/'subscription.yaml').open('a') as f: f.write('# updated upstream\n')
        self.run_cli('update')
        import yaml
        config=yaml.safe_load((self.root/'data/config.yaml').read_text())
        self.assertTrue(config['allow-lan']); self.assertIn('172.17.0.0/16',config['lan-allowed-ips'])
        self.assertEqual((self.root/'data/subscription.yaml').read_bytes(),(self.root/'subscription.yaml').read_bytes())

    def test_invalid_override_is_not_committed(self):
        self.install(); old=(self.root/'data/config.yaml').read_bytes()
        old_overrides=(self.root/'data/overrides.yaml').read_bytes()
        self.run_cli('set-access','docker','0.0.0.0/0',code=1)
        self.assertEqual((self.root/'data/config.yaml').read_bytes(),old)
        self.assertEqual((self.root/'data/overrides.yaml').read_bytes(),old_overrides)

    def test_local_access_profile_preserves_authentication(self):
        self.install()
        (self.root/'data/overrides.yaml').write_text('authentication: ["test:pass"]\n')
        self.run_cli('set-access','docker','172.17.0.0/16')
        self.run_cli('set-access','local')
        import yaml
        config=yaml.safe_load((self.root/'data/config.yaml').read_text())
        self.assertFalse(config['allow-lan']); self.assertEqual(config['bind-address'],'127.0.0.1')
        self.assertEqual(config['authentication'],['test:pass'])

    def test_override_nested_merge_and_list_replacement(self):
        self.install()
        with (self.root/'subscription.yaml').open('a') as f:
            f.write('dns:\n  enable: false\n  ipv6: false\n')
        (self.root/'data/overrides.yaml').write_text('dns:\n  ipv6: true\nrules: ["MATCH,DIRECT"]\n')
        self.run_cli('update')
        import yaml
        config=yaml.safe_load((self.root/'data/config.yaml').read_text())
        self.assertEqual(config['dns'],{'enable':False,'ipv6':True})
        self.assertEqual(config['rules'],['MATCH,DIRECT'])

    def test_self_update_success_and_backup(self):
        self.install(); self.prepare_release()
        core=(self.root/'installed/core').read_bytes(); env=(self.root/'settings.env').read_bytes()
        self.run_cli('self-update')
        self.assertIn('VERSION=0.3.1',(self.root/'installed/manager').read_text())
        self.assertEqual((self.root/'installed/core').read_bytes(),core)
        self.assertEqual((self.root/'settings.env').read_bytes(),env)

    def test_self_update_bad_hash_keeps_old_manager(self):
        self.install(); self.prepare_release()
        old=(self.root/'installed/manager').read_bytes()
        m=json.loads((self.root/'release.json').read_text()); m['sha256']='0'*64
        (self.root/'release.json').write_text(json.dumps(m))
        self.run_cli('self-update',code=1)
        self.assertEqual((self.root/'installed/manager').read_bytes(),old)

    def test_self_update_rejects_downgrade(self):
        self.install(); self.prepare_release('0.2.0')
        self.run_cli('self-update',code=1)

    def test_self_update_same_version_does_not_deploy(self):
        self.install(); self.prepare_release('0.3.0')
        self.run_cli('self-update')
        self.assertFalse(list((self.root/'state').glob('backups/manager-upgrade.*')))

    def test_oneclick_installer_upgrades_existing_without_restart(self):
        self.install(); self.prepare_release('0.3.0')
        (self.root/'installed/manager').write_bytes((self.root/'legacy').read_bytes())
        old_config=(self.root/'data/config.yaml').read_bytes()
        (self.root/'calls').write_text('')
        result=subprocess.run(['bash',str(self.installer),'--no-menu'],env=self.env,capture_output=True,text=True,timeout=15)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertIn('VERSION=0.3.0',(self.root/'installed/manager').read_text())
        self.assertEqual((self.root/'data/config.yaml').read_bytes(),old_config)
        self.assertNotIn('restart',(self.root/'calls').read_text())

    def test_oneclick_installer_fresh_initialization(self):
        self.prepare_release('0.3.0')
        result=subprocess.run(['bash',str(self.installer),'--no-menu'],env=self.env,capture_output=True,text=True,timeout=15)
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        self.assertTrue((self.root/'installed/manager').exists())
        self.assertFalse((self.root/'installed/core').exists())

    def test_oneclick_installer_bad_hash_keeps_old_manager(self):
        self.install(); self.prepare_release('0.3.0'); old=(self.root/'installed/manager').read_bytes()
        m=json.loads((self.root/'release.json').read_text()); m['sha256']='0'*64
        (self.root/'release.json').write_text(json.dumps(m))
        result=subprocess.run(['bash',str(self.installer),'--no-menu'],env=self.env,capture_output=True,text=True,timeout=15)
        self.assertNotEqual(result.returncode,0)
        self.assertEqual((self.root/'installed/manager').read_bytes(),old)

    def test_access_menu_returns(self):
        self.install(); t=self.term(); t.choose(5); t.expect('请选择（0 返回）：'); t.choose(6)
        t.expect('--- 代理访问范围 ---'); t.expect('请选择（0 返回）：'); t.choose(2)
        t.expect('Docker CIDR：'); t.choose('172.17.0.0/16'); t.expect('操作完成。')
        t.expect('请选择（0 返回）：'); t.choose(0); t.expect('--- 设置与维护 ---')
        t.expect('请选择（0 返回）：'); t.choose(0); t.expect('请选择 [0-5]：')

    def test_eof_submenu_exits(self):
        t=self.term(); t.choose(2); t.expect('请选择（0 返回）：'); t.send('\x04')
        t.expect('请选择 [0-5]：'); t.choose(0)


if __name__ == '__main__':
    print('Native parser:', REAL_CORE or 'fixture (set MIHOMO_TEST_REAL_CORE for real validation)', flush=True)
    unittest.main(verbosity=2)
