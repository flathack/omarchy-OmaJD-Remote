"""Real Chromium/Firefox Click'n'Load extension smoke tests.

Set RUN_BROWSER_E2E=1 and run under an X server (for example xvfb-run).
"""

from __future__ import annotations

import base64
import importlib.util
import os
import ssl
import subprocess
import sys
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlencode

from Crypto.Cipher import AES

try:
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options as ChromeOptions
    from selenium.webdriver.firefox.options import Options as FirefoxOptions
    from selenium.webdriver.support.ui import WebDriverWait
except ImportError:  # pragma: no cover - optional local integration dependency
    webdriver = None


PROJECT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("jdctl_browser_e2e", PROJECT / "jdctl.py")
jdctl = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = jdctl
SPEC.loader.exec_module(jdctl)


PAGE = b"""<!doctype html><meta charset=utf-8><title>OmaJDownLoad fixture</title>
<script>window.parserProbeResult = 'pending'</script>
<script src="http://127.0.0.1:9666/jdcheck.js"
        onload="window.parserProbeResult = window.jdownloader === true ? 'loaded' : 'wrong'"
        onerror="window.parserProbeResult = 'error'"></script>
<iframe name=response hidden></iframe><main id=status>ready</main>"""


class PageHandler(BaseHTTPRequestHandler):
    def log_message(self, _format, *_args):
        return

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(PAGE)))
        self.end_headers()
        self.wfile.write(PAGE)


def wait_for(predicate, timeout=10):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError("timed out waiting for browser fixture state")


@unittest.skipUnless(os.environ.get("RUN_BROWSER_E2E") == "1" and webdriver is not None, "real browsers not requested")
class BrowserExtensionEndToEndTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = TemporaryDirectory()
        cls.root = Path(cls.temporary.name)
        cls.old_paths = jdctl.CONFIG_DIR, jdctl.CONFIG_FILE, jdctl.INBOX_FILE
        jdctl.CONFIG_DIR = cls.root / "config"
        jdctl.CONFIG_FILE = jdctl.CONFIG_DIR / "config.json"
        jdctl.INBOX_FILE = jdctl.CONFIG_DIR / "inbox.json"
        cls.bridge = jdctl.Bridge(start_cnl=True, cnl_port=9666)
        if not cls.bridge.cnl_server.listening:
            raise RuntimeError(f"Click'n'Load test port 9666 is unavailable: {cls.bridge.cnl_server.error}")

        key = cls.root / "key.pem"
        cert = cls.root / "cert.pem"
        subprocess.run([
            "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
            "-keyout", str(key), "-out", str(cert), "-days", "1",
            "-subj", "/CN=localhost",
        ], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        cls.web = ThreadingHTTPServer(("127.0.0.1", 0), PageHandler)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert, key)
        cls.web.socket = context.wrap_socket(cls.web.socket, server_side=True)
        import threading
        cls.web_thread = threading.Thread(target=cls.web.serve_forever, daemon=True)
        cls.web_thread.start()
        cls.page_url = f"https://localhost:{cls.web.server_address[1]}/"

    @classmethod
    def tearDownClass(cls):
        cls.web.shutdown()
        cls.web.server_close()
        cls.bridge.cnl_server.stop()
        jdctl.CONFIG_DIR, jdctl.CONFIG_FILE, jdctl.INBOX_FILE = cls.old_paths
        cls.temporary.cleanup()

    def browsers(self):
        chrome = ChromeOptions()
        chrome.add_argument("--no-sandbox")
        chrome.add_argument("--disable-dev-shm-usage")
        chrome.add_argument("--ignore-certificate-errors")
        chrome.add_argument(f"--load-extension={PROJECT / 'browser-extension'}")
        yield "chromium", webdriver.Chrome(options=chrome)

        firefox = FirefoxOptions()
        firefox.accept_insecure_certs = True
        firefox.add_argument("-headless")
        driver = webdriver.Firefox(options=firefox)
        package = PROJECT / "dist" / "omajdownload-clicknload-firefox.zip"
        driver.install_addon(str(package), temporary=True)
        yield "firefox", driver

    def test_https_form_fetch_xhr_script_probe_and_cnl2(self):
        for browser_name, driver in self.browsers():
            with self.subTest(browser=browser_name):
                try:
                    self.bridge.inbox = []
                    jdctl.write_inbox([])
                    driver.get(self.page_url)
                    WebDriverWait(driver, 10).until(lambda current: current.execute_script("return window.jdownloader === true"))
                    WebDriverWait(driver, 10).until(
                        lambda current: current.execute_script("return window.parserProbeResult") == "loaded"
                    )

                    fetch_result = driver.execute_async_script("""
                        const done = arguments[arguments.length - 1];
                        fetch('http://127.0.0.1:9666/flash/add', {
                          method: 'POST', body: new URLSearchParams({urls: 'https://files.example/fetch'})
                        }).then(r => r.text()).then(done).catch(e => done('ERROR:' + e));
                    """)
                    self.assertIn("success", fetch_result)

                    xhr_status = driver.execute_async_script("""
                        const done = arguments[arguments.length - 1];
                        const xhr = new XMLHttpRequest();
                        xhr.open('POST', 'http://127.0.0.1:9666/flash/add');
                        xhr.onload = () => done(xhr.status);
                        xhr.onerror = () => done(-1);
                        xhr.send(new URLSearchParams({urls: 'https://files.example/xhr'}));
                    """)
                    self.assertEqual(xhr_status, 200)

                    form_result = driver.execute_async_script("""
                        const done = arguments[arguments.length - 1];
                        const form = document.createElement('form');
                        form.method = 'POST'; form.action = 'http://127.0.0.1:9666/flash/add'; form.target = 'response';
                        const urls = document.createElement('input'); urls.name = 'urls'; urls.value = 'https://files.example/form';
                        const button = document.createElement('button'); button.name = 'source'; button.value = 'https://button.example/cnl';
                        form.append(urls, button); document.body.append(form);
                        form.addEventListener('omajdownload:success', () => done('success'), {once: true});
                        button.click();
                    """)
                    self.assertEqual(form_result, "success")

                    request_submit_result = driver.execute_async_script("""
                        const done = arguments[arguments.length - 1];
                        const form = document.createElement('form');
                        form.method = 'POST'; form.action = 'http://127.0.0.1:9666/flash/add';
                        const urls = document.createElement('input'); urls.name = 'urls'; urls.value = 'https://files.example/request-submit';
                        const first = document.createElement('button'); first.name = 'source'; first.value = 'https://button.example/first';
                        const selected = document.createElement('button'); selected.name = 'source'; selected.value = 'https://button.example/selected';
                        const disabled = document.createElement('button'); disabled.name = 'source'; disabled.value = 'https://button.example/disabled'; disabled.disabled = true;
                        const unnamed = document.createElement('button'); unnamed.value = 'https://button.example/unnamed';
                        form.append(urls, first, selected, disabled, unnamed); document.body.append(form);
                        form.addEventListener('omajdownload:success', () => done('success'), {once: true});
                        form.requestSubmit(selected);
                    """)
                    self.assertEqual(request_submit_result, "success")

                    direct_submit_result = driver.execute_async_script("""
                        const done = arguments[arguments.length - 1];
                        const form = document.createElement('form');
                        form.method = 'POST'; form.action = 'http://127.0.0.1:9666/flash/add';
                        const urls = document.createElement('input'); urls.name = 'urls'; urls.value = 'https://files.example/direct-submit';
                        const button = document.createElement('button'); button.name = 'source'; button.value = 'https://button.example/not-submitted';
                        form.append(urls, button); document.body.append(form);
                        form.addEventListener('omajdownload:success', () => done('success'), {once: true});
                        form.submit();
                    """)
                    self.assertEqual(direct_submit_result, "success")

                    script_result = driver.execute_async_script("""
                        const done = arguments[arguments.length - 1];
                        window.jdownloader = false;
                        const script = document.createElement('script');
                        script.onload = () => done(window.jdownloader);
                        script.onerror = () => done(false);
                        script.src = 'http://127.0.0.1:9666/jdcheck.js';
                        document.head.append(script);
                    """)
                    self.assertTrue(script_result)

                    key_hex = "00112233445566778899aabbccddeeff"
                    key = bytes.fromhex(key_hex)
                    clear = b"https://files.example/encrypted"
                    padding = AES.block_size - len(clear) % AES.block_size
                    encrypted = AES.new(key, AES.MODE_CBC, iv=key).encrypt(clear + bytes([padding]) * padding)
                    cnl2_body = urlencode({
                        "jk": f"function f() {{ return '{key_hex}'; }}",
                        "crypted": base64.b64encode(encrypted).decode(),
                    })
                    cnl2_result = driver.execute_async_script("""
                        const done = arguments[arguments.length - 1];
                        fetch('http://127.0.0.1:9666/flash/addcrypted2', {method: 'POST', body: arguments[0]})
                          .then(r => r.text()).then(done).catch(e => done('ERROR:' + e));
                    """, cnl2_body)
                    self.assertIn("success", cnl2_result)

                    wait_for(lambda: len(self.bridge.inbox) == 6)
                    dynamic_result = driver.execute_async_script("""
                        const done = arguments[arguments.length - 1];
                        const body = new URLSearchParams({jk: 'function f(){ return buildKey(); }', crypted: 'AAAA'});
                        fetch('http://127.0.0.1:9666/flash/addcrypted2', {method: 'POST', body})
                          .then(() => done('unexpected-success')).catch(e => done(e.name));
                    """)
                    self.assertNotEqual(dynamic_result, "unexpected-success")
                    oversized_result = driver.execute_async_script("""
                        const done = arguments[arguments.length - 1];
                        fetch('http://127.0.0.1:9666/flash/add', {method: 'POST', body: 'urls=' + 'x'.repeat(1024 * 1024 + 1)})
                          .then(() => done('unexpected-success')).catch(e => done(e.name));
                    """)
                    self.assertNotEqual(oversized_result, "unexpected-success")
                    abort_result = driver.execute_async_script("""
                        const done = arguments[arguments.length - 1];
                        const controller = new AbortController();
                        controller.abort();
                        fetch('http://127.0.0.1:9666/flash/add', {method: 'POST', body: 'urls=https://files.example/cancelled', signal: controller.signal})
                          .then(() => done('unexpected-success')).catch(e => done(e.name));
                    """)
                    self.assertEqual(abort_result, "AbortError")
                    time.sleep(0.2)
                    self.assertEqual(len(self.bridge.inbox), 6)
                    flattened = [link for item in self.bridge.inbox for link in item.get("links", [])]
                    self.assertEqual(set(flattened), {
                        "https://files.example/fetch", "https://files.example/xhr",
                        "https://files.example/form", "https://files.example/request-submit",
                        "https://files.example/direct-submit", "https://files.example/encrypted",
                    })
                    form_item = next(item for item in self.bridge.inbox if item["links"] == ["https://files.example/form"])
                    self.assertEqual(form_item.get("claimed_source"), "https://button.example/cnl")
                    request_submit_item = next(
                        item for item in self.bridge.inbox if item["links"] == ["https://files.example/request-submit"]
                    )
                    self.assertEqual(request_submit_item.get("claimed_source"), "https://button.example/selected")
                    direct_submit_item = next(
                        item for item in self.bridge.inbox if item["links"] == ["https://files.example/direct-submit"]
                    )
                    self.assertEqual(direct_submit_item.get("claimed_source"), "")

                    self.bridge.cnl_server.stop()
                    listener_result = driver.execute_async_script("""
                        const done = arguments[arguments.length - 1];
                        fetch('http://127.0.0.1:9666/flash/add', {method: 'POST', body: 'urls=https://files.example/offline'})
                          .then(() => done('unexpected-success')).catch(e => done(e.name));
                    """)
                    self.assertNotEqual(listener_result, "unexpected-success")
                    failed_script_result = driver.execute_async_script("""
                        const done = arguments[arguments.length - 1];
                        const script = document.createElement('script');
                        script.onload = () => done('unexpected-success');
                        script.onerror = () => done('error');
                        script.src = 'http://127.0.0.1:9666/jdcheck.js';
                        document.head.append(script);
                    """)
                    self.assertEqual(failed_script_result, "error")
                finally:
                    if not self.bridge.cnl_server.listening:
                        self.bridge.cnl_server.start()
                    driver.quit()


if __name__ == "__main__":
    unittest.main()
