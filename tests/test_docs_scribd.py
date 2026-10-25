import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

from PIL import Image
from playwright.sync_api import Error as PlaywrightError, sync_playwright

from providers.common import CaptureRuntime, capture_virtual_page, detect_page_selector, discover_virtual_pages, find_scrollable_container
from providers.google_docs import GOOGLE_DOCS_PAGE_SELECTORS, _access_blocked
from providers.scribd import capture_scribd


class DocsVirtualPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch(headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()

    def setUp(self):
        self.page = self.browser.new_page(viewport={'width': 1200, 'height': 900})
        self.addCleanup(self.page.close)
        self.runtime = CaptureRuntime({'max_scrolls': 40, 'scroll_pause': 0.02}, Mock(), Mock())

    def load_virtual_docs(self):
        self.page.set_content('''<style>
            .kix-appview-editor{height:700px;width:650px;overflow:auto}
            #space{position:relative;height:4340px}
            .kix-page-paginated{position:absolute;width:500px;height:600px;background:white}
            </style><div class="kix-appview-editor"><div id="space"></div></div>
            <script>
            const root=document.querySelector('.kix-appview-editor');
            function render(){
              const current=Math.min(6,Math.floor(root.scrollTop/620));
              const pages=[];
              for(let i=Math.max(0,current-1);i<=Math.min(6,current+1);i++){
                pages.push('<div class="kix-page-paginated" style="top:'+i*620+'px">Page '+(i+1)+'</div>');
              }
              document.querySelector('#space').innerHTML=pages.join('');
            }
            root.addEventListener('scroll',render);render();
            </script>''')

    def test_discovers_and_captures_all_seven_pages_with_at_most_three_dom_nodes(self):
        self.load_virtual_docs()
        selector, _ = detect_page_selector(self.page, GOOGLE_DOCS_PAGE_SELECTORS)
        root = find_scrollable_container(self.page, ('.kix-appview-editor',))
        pages = discover_virtual_pages(self.page, selector, root, 'test', self.runtime)
        self.assertEqual(len(pages), 7)
        self.assertEqual([round(p['top']) for p in pages], list(range(0, 4340, 620)))
        with tempfile.TemporaryDirectory() as temporary:
            for number, geometry in enumerate(pages):
                output = Path(temporary) / f'page_{number + 1}.png'
                capture_virtual_page(self.page, selector, root, geometry, output)
                self.assertEqual(self.page.locator('[data-capture-page-target="1"]').inner_text(), f'Page {number + 1}')
                self.assertLessEqual(self.page.locator(selector).count(), 3)
                with Image.open(output) as image:
                    self.assertEqual(image.size, (500, 600))

    def test_scroll_limit_is_not_silently_reported_as_complete_document(self):
        self.load_virtual_docs()
        runtime = CaptureRuntime({'max_scrolls': 1, 'scroll_pause': 0.02}, Mock(), Mock())
        root = find_scrollable_container(self.page, ('.kix-appview-editor',))
        with self.assertRaisesRegex(RuntimeError, 'giới hạn|chưa.*cuối'):
            discover_virtual_pages(self.page, '.kix-page-paginated', root, 'test', runtime)

    def test_docs_access_denial_is_explained(self):
        self.page.set_content('<h1>You need access</h1><p>Request access</p>')
        self.assertIn('Không truy cập được Google Docs', _access_blocked(self.page))


class ScribdPipelineTests(unittest.TestCase):
    def run_fixture(self, content_ready=True, status=200, navigation_error=None):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        output = Path(temporary.name)
        page, browser, playwright = MagicMock(), MagicMock(), MagicMock()
        page.url = 'https://www.scribd.com/document/123/fixture'
        page.goto.return_value.status = status
        if navigation_error:
            page.goto.side_effect = navigation_error
        browser.new_context.return_value.new_page.return_value = page
        playwright.chromium.launch.return_value = browser
        manager = MagicMock()
        manager.__enter__.return_value = playwright
        runtime = CaptureRuntime({'headless': True, 'viewport_width': 1200, 'viewport_height': 900, 'timeout': 500, 'nav_timeout': 500}, Mock(), Mock())
        def capture(*args, **kwargs):
            if kwargs.get('before_screenshot'):
                kwargs['before_screenshot']()
            Image.new('RGB', (400, 600), 'white').save(args[4])
        with patch('providers.scribd.sync_playwright', return_value=manager), \
             patch('providers.scribd._accept_cookies'), \
             patch('providers.scribd._open_fullscreen'), \
             patch('providers.scribd.maximize_and_zoom_out'), \
             patch('providers.scribd.find_scrollable_container', return_value='body'), \
             patch('providers.scribd.discover_virtual_pages', return_value=[{'top': 0, 'height': 600}]) as discover, \
             patch('providers.scribd.capture_virtual_page', side_effect=capture), \
             patch('providers.scribd._wait_for_page_content', return_value=content_ready), \
             patch('providers.scribd.save_debug_artifacts'):
            capture_scribd('test', '123', page.url, output, runtime)
        return runtime, output, discover

    def test_loaded_scribd_page_is_saved(self):
        runtime, output, _ = self.run_fixture()
        self.assertTrue(runtime.finish_job.call_args.args[1])
        self.assertTrue((output / 'page_001.png').exists())

    def test_unloaded_page_is_not_counted_as_success(self):
        runtime, output, _ = self.run_fixture(content_ready=False)
        self.assertFalse(runtime.finish_job.call_args.args[1])
        self.assertEqual(runtime.finish_job.call_args.kwargs['pages_captured'], 0)
        self.assertFalse((output / 'page_001.png').exists())

    def test_http_rejection_is_reported_before_searching_for_pages(self):
        runtime, _, discover = self.run_fixture(status=406)
        self.assertFalse(runtime.finish_job.call_args.args[1])
        self.assertIn('406', runtime.finish_job.call_args.kwargs['error'])
        discover.assert_not_called()

    def test_headless_navigation_rejection_is_explained_without_opening_a_window(self):
        runtime, _, discover = self.run_fixture(navigation_error=PlaywrightError('net::ERR_HTTP_RESPONSE_CODE_FAILURE'))
        self.assertFalse(runtime.finish_job.call_args.args[1])
        self.assertIn('chạy ngầm', runtime.finish_job.call_args.kwargs['error'])
        discover.assert_not_called()


if __name__ == '__main__':
    unittest.main()
