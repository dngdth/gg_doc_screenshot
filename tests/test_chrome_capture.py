import json
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from time import monotonic
from unittest.mock import Mock, patch

from PIL import Image
from playwright.sync_api import sync_playwright

from app import app
from providers.chrome import BRIDGE_JOBS, ChromeJob, _expire, capture_studocu_in_chrome, find_chrome
from providers.common import CaptureRuntime
from providers.studocu import capture_studocu


ROOT = Path(__file__).resolve().parent.parent


class ChromeCaptureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.output = Path(self.temp.name)
        self.runtime = CaptureRuntime({}, Mock(), Mock())
        self.job = ChromeJob('abcdef123456', 'https://www.studocu.vn/vn/document/example/123', self.output, self.runtime)
        self.job.touch()
        self.registry = patch.dict(BRIDGE_JOBS, {self.job.job_id: self.job}, clear=True)
        self.registry.start()
        self.addCleanup(self.registry.stop)
        self.client = app.test_client()
        self.headers = {'Authorization': f'Bearer {self.job.token}'}
        self.base = f'/chrome_capture/{self.job.job_id}'

    def event(self, **data):
        return self.client.post(self.base + '/event', json=data, headers=self.headers)

    def test_studocu_entrypoint_uses_native_chrome_without_playwright(self):
        with patch('providers.studocu.capture_studocu_in_chrome') as native, patch('playwright.sync_api.sync_playwright') as playwright:
            capture_studocu('job', '123', self.job.target_url, self.output, self.runtime)
        native.assert_called_once_with('job', '123', self.job.target_url, self.output, self.runtime)
        playwright.assert_not_called()

    def test_opens_installed_chrome_without_automation_or_profile_flags(self):
        with patch('providers.chrome.find_chrome', return_value=Path('C:/Chrome/chrome.exe')), \
             patch('providers.chrome.subprocess.Popen') as launch, patch('providers.chrome.threading.Timer'):
            capture_studocu_in_chrome('abcdef000001', '123', self.job.target_url, self.output, self.runtime)
        command = launch.call_args.args[0]
        self.assertEqual(command[:2], ['C:\\Chrome\\chrome.exe', '--new-tab'])
        self.assertTrue(command[2].startswith('http://127.0.0.1:5000/chrome_capture/abcdef000001#'))
        self.assertFalse(any('debug' in arg or 'automation' in arg or 'user-data-dir' in arg for arg in command))
        self.assertFalse(launch.call_args.kwargs['shell'])

    def test_missing_chrome_reports_error_without_fallback(self):
        with patch('providers.chrome.find_chrome', side_effect=FileNotFoundError('Chrome missing')):
            capture_studocu_in_chrome('missing', '123', self.job.target_url, self.output, self.runtime)
        self.runtime.finish_job.assert_called_once()
        self.assertFalse(self.runtime.finish_job.call_args.args[1])
        self.assertNotIn('missing', BRIDGE_JOBS)

    def test_explicit_chrome_path_is_validated(self):
        with self.assertRaises(FileNotFoundError):
            find_chrome({'chrome_executable': str(self.output / 'missing.exe')})

    def test_info_requires_job_token_and_returns_target(self):
        self.assertEqual(self.client.get(self.base + '/info').status_code, 403)
        self.assertEqual(self.client.get(self.base + '/info', headers={'Authorization': 'Bearer wrong'}).status_code, 403)
        response = self.client.get(self.base + '/info', headers=self.headers)
        self.assertEqual(response.get_json()['target_url'], self.job.target_url)
        self.assertNotIn('token', response.get_json())

    def test_native_chrome_uploads_png_and_finishes(self):
        self.assertEqual(self.event(event='start', total=2).status_code, 200)
        png = BytesIO()
        Image.new('RGB', (100, 150), 'white').save(png, 'PNG')
        for number in (2, 1, 1):
            response = self.client.post(self.base + f'/page/{number}', data=png.getvalue(), content_type='image/png', headers=self.headers)
            self.assertEqual(response.status_code, 200)
        self.assertEqual(len(self.job.files), 2)
        self.assertEqual(self.event(event='finish').status_code, 200)
        result = self.runtime.finish_job.call_args
        self.assertTrue(result.args[1])
        self.assertEqual(result.kwargs['files'], ['page_001.png', 'page_002.png'])
        self.assertEqual((self.output / 'page_001.png').read_bytes(), png.getvalue())

    def test_rejects_invalid_frames_and_early_finish(self):
        self.assertEqual(self.event(event='finish').status_code, 400)
        self.assertEqual(self.event(event='start', total=True).status_code, 400)
        self.assertEqual(self.event(event='start', total=0).status_code, 400)
        self.assertEqual(self.event(event='start', total=1).status_code, 200)
        self.assertEqual(self.event(event='start', total=1).status_code, 400)
        self.assertEqual(self.client.post(self.base + '/page/1', data=b'bad png', headers=self.headers).status_code, 400)
        self.assertEqual(self.client.post(self.base + '/page/2', data=b'bad png', headers=self.headers).status_code, 400)
        self.assertFalse(list(self.output.glob('*.png')))

    def test_wait_can_resume_after_user_verifies(self):
        self.assertEqual(self.event(event='wait', message='Finish verification').status_code, 200)
        self.assertFalse(self.job.done)
        self.assertEqual(self.event(event='start', total=1).status_code, 200)

    def test_partial_capture_is_failed(self):
        self.event(event='start', total=2)
        self.event(event='finish')
        self.assertFalse(self.runtime.finish_job.call_args.args[1])

    def test_expired_job_is_finished_and_removed(self):
        self.job.deadline = monotonic() - 1
        _expire(self.job.job_id, self.job.token)
        self.assertNotIn(self.job.job_id, BRIDGE_JOBS)
        self.assertFalse(self.runtime.finish_job.call_args.args[1])

    def test_handoff_instructions_do_not_expose_token(self):
        response = self.client.get(self.base)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(self.job.token, response.get_data(as_text=True))
        self.assertEqual(response.headers['Referrer-Policy'], 'no-referrer')

    def test_extension_permissions_are_scoped(self):
        manifest = json.loads((ROOT / 'chrome_extension/manifest.json').read_text(encoding='utf-8'))
        self.assertIn('activeTab', manifest['permissions'])
        self.assertNotIn('<all_urls>', manifest['host_permissions'])
        self.assertNotIn('debugger', manifest['permissions'])
        self.assertNotIn('cookies', manifest['permissions'])
        self.assertIn('https://*.studocu.vn/*', manifest['host_permissions'])
        self.assertIn('https://*.studocu.com/*', manifest['host_permissions'])


class ChromeExtensionDOMTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch(headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()

    def test_detect_fit_and_restore_actual_dom(self):
        page = self.browser.new_page(viewport={'width': 1200, 'height': 800})
        self.addCleanup(page.close)
        page.set_content('''<style>.pf{width:800px;height:1100px;position:relative;background:white}
            .pc{position:absolute;width:800px;height:1100px}header{position:fixed;top:0;left:0;width:100%;height:100px}</style>
            <header>Header</header><div style="height:1000px"></div><div id="page-container">
            <div data-page-index="0"><div class="pf"><div class="page-content" style="height:0"><div class="pc">Page one</div></div></div></div>
            <div data-page-index="1"><div class="pf"><div class="page-content" style="filter:blur(2px)"><div class="pc">Preview</div></div></div></div>
            </div><div style="height:1000px"></div>''')
        source = (ROOT / 'chrome_extension/capture.mjs').read_text(encoding='utf-8').replace('export ', '')
        page.evaluate('(source) => window.eval(source)', source)
        self.assertEqual(page.evaluate('describeDocument()'), {'total': 2})
        for index in (0, 1):
            rect = page.evaluate('(index) => positionPage(index)', index)
            self.assertTrue(rect['hasContent'])
            self.assertGreaterEqual(rect['y'], 115)
            self.assertLessEqual(rect['y'] + rect['height'], 800)
            self.assertLess(rect['height'], 1100)
        self.assertEqual(page.locator('.page-content').nth(1).evaluate('el => getComputedStyle(el).filter'), 'blur(2px)')
        page.evaluate('restoreDocument()')
        self.assertEqual(page.locator('#page-container').evaluate('el => el.style.zoom'), '')

    def test_dom_error_is_returned_even_if_chrome_cannot_propagate_exceptions(self):
        page = self.browser.new_page()
        self.addCleanup(page.close)
        page.set_content('<p>Document still loading</p>')
        source = (ROOT / 'chrome_extension/capture.mjs').read_text(encoding='utf-8').replace('export ', '')
        page.evaluate('(source) => window.eval(source)', source)
        result = page.evaluate('describeDocument()')
        self.assertIn('Chưa tìm thấy khung giấy', result['captureError'])
        page.evaluate('(message) => showCaptureError(message)', 'A diagnostic error')
        self.assertEqual(page.locator('#__dps_capture_error').count(), 1)


if __name__ == '__main__':
    unittest.main()
