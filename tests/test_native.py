#!/usr/bin/env python3
"""Real native Mihomo config + loopback HTTP proxy smoke test. No TUN/systemd."""
import http.server
import os
from pathlib import Path
import socket
import subprocess
import tempfile
import threading
import time
import unittest

CORE = os.environ.get('MIHOMO_TEST_REAL_CORE', '')


@unittest.skipUnless(CORE, 'set MIHOMO_TEST_REAL_CORE to a native Mihomo executable')
class NativeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='mihomo-native-test-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_real_version(self):
        result = subprocess.run([CORE, '-v'], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('v1.19.33', result.stdout)

    def test_invalid_yaml_rejected(self):
        config = self.root / 'config.yaml'
        config.write_text('proxies: [\n')
        result = subprocess.run([CORE, '-t', '-d', str(self.root), '-f', str(config)], capture_output=True, timeout=15)
        self.assertNotEqual(result.returncode, 0)

    def test_real_proxy_forwards_http(self):
        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                body = b'MIHOMO_LOCAL_PROXY_OK'
                self.send_response(200)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            def log_message(self, *_):
                pass
        server = http.server.HTTPServer(('127.0.0.1', 0), Handler)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            proxy_port = probe.getsockname()[1]
        config = self.root / 'config.yaml'
        config.write_text(f'''mixed-port: {proxy_port}
bind-address: 127.0.0.1
allow-lan: false
mode: rule
log-level: warning
proxies: []
rules:
  - MATCH,DIRECT
''')
        result = subprocess.run([CORE, '-t', '-d', str(self.root), '-f', str(config)], capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        with (self.root / 'core.log').open('wb') as log:
            process = subprocess.Popen([CORE, '-d', str(self.root), '-f', str(config)], stdout=log, stderr=log)
        def stop():
            if process.poll() is None:
                process.terminate()
                try: process.wait(timeout=5)
                except subprocess.TimeoutExpired: process.kill(); process.wait()
        self.addCleanup(stop)
        response = b''
        for attempt in range(100):
            try:
                with socket.create_connection(('127.0.0.1', proxy_port), timeout=2) as sock:
                    upstream = server.server_address[1]
                    sock.sendall(f'GET http://127.0.0.1:{upstream}/test HTTP/1.1\r\nHost: 127.0.0.1:{upstream}\r\nConnection: close\r\n\r\n'.encode())
                    while True:
                        block = sock.recv(65536)
                        if not block: break
                        response += block
                break
            except ConnectionRefusedError:
                if process.poll() is not None:
                    self.fail((self.root / 'core.log').read_text())
                time.sleep(.05)
        self.assertIn(b'200 OK', response)
        self.assertIn(b'MIHOMO_LOCAL_PROXY_OK', response)


if __name__ == '__main__':
    unittest.main(verbosity=2)
