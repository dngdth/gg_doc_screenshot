"""Exercise native extension APIs with a local fixture, not the user's Chrome profile."""
import json
import shutil
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from PIL import Image
from playwright.sync_api import sync_playwright
from werkzeug.serving import make_server

from app import app
from providers.chrome import BRIDGE_JOBS, ChromeJob
from providers.common import CaptureRuntime


class ChromeExtensionEndToEndTests(unittest.TestCase):
    def test_handoff_native_screenshot_crop_upload_and_finish(self):
        root = Path(__file__).resolve().parent.parent
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            extension = folder / 'extension'
            shutil.copytree(root / 'chrome_extension', extension)
            fixture = b'''<style>.pf{width:600px;height:800px;background:white;position:relative}
                .pc{position:absolute;width:600px;height:800px}</style>
                <div style="height:1000px"></div><div id="page-container">
                <div data-page-index="0"><div class="pf"><div class="page-content" style="height:0"><div class="pc">Page one</div></div></div></div>
                <div data-page-index="1"><div class="pf"><div class="page-content" style="height:0"><div class="pc">Page two</div></div></div></div>
                </div><div style="height:1000px"></div>'''
            def local_app(environ, start_response):
                if environ['PATH_INFO'] == '/fixture':
                    start_response('200 OK', [('Content-Type', 'text/html')])
                    return [fixture]
                return app(environ, start_response)
            server = make_server('127.0.0.1', 0, local_app, threaded=True)
            self.addCleanup(server.server_close)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            self.addCleanup(server.shutdown)
            base = f'http://127.0.0.1:{server.server_port}'
            manifest_file = extension / 'manifest.json'
            manifest = json.loads(manifest_file.read_text(encoding='utf-8'))
            # Test-only host access substitutes for a human clicking the action.
            # The shipped manifest keeps activeTab and localhost access only.
            manifest['host_permissions'] = ['<all_urls>']
            manifest['content_scripts'][0]['matches'] = [base + '/chrome_capture/*']
            manifest_file.write_text(json.dumps(manifest), encoding='utf-8')
            file = extension / 'background.js'
            source = file.read_text(encoding='utf-8').replace('http://127.0.0.1:5000', base)
            source = source.replace('function isStudocu(url) {', "function isStudocu(url) { if (url === BASE + '/fixture') return true;")
            file.write_text(source + '''
                globalThis.testCapture = async () => {
                    const entries = await chrome.storage.session.get(null);
                    const key = Object.keys(entries).find(key => key.startsWith('job:'));
                    let tab = await chrome.tabs.get(Number(key.slice(4)));
                    for (let i = 0; i < 100 && tab.status !== 'complete'; i++) {
                        await new Promise(r => setTimeout(r, 50));
                        tab = await chrome.tabs.get(tab.id);
                    }
                    await capture(tab, entries[key]);
                };
            ''', encoding='utf-8')
            runtime = CaptureRuntime({}, Mock(), Mock())
            job = ChromeJob('abcdef123456', base + '/fixture', folder, runtime)
            job.touch()
            with patch.dict(BRIDGE_JOBS, {job.job_id: job}, clear=True), sync_playwright() as playwright:
                context = playwright.chromium.launch_persistent_context(
                    str(folder / 'test-profile'), executable_path=playwright.chromium.executable_path,
                    headless=True, no_viewport=True,
                    args=['--window-size=1200,1000', f'--disable-extensions-except={extension}', f'--load-extension={extension}'],
                )
                try:
                    handoff = context.pages[0]
                    handoff.goto(f'{base}/chrome_capture/{job.job_id}#{job.token}')
                    handoff.locator('#extension-status').filter(has_text='Đã mở tab tài liệu').wait_for(timeout=15000)
                    worker = context.service_workers[0]
                    worker.evaluate('() => testCapture()')
                    self.assertTrue(job.done, runtime.update_job.call_args_list)
                    self.assertTrue(runtime.finish_job.call_args.args[1], runtime.finish_job.call_args)
                    self.assertEqual(sorted(job.files), [1, 2])
                    for filename in job.files.values():
                        with Image.open(folder / filename) as png:
                            self.assertGreater(png.width, 100)
                            self.assertAlmostEqual(png.width / png.height, 600 / 800, places=2)
                finally:
                    context.close()


if __name__ == '__main__':
    unittest.main()
