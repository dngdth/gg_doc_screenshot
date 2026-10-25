import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image
from playwright.sync_api import sync_playwright
from pypdf import PdfReader
from werkzeug.serving import make_server

import app as application
from document_processing import finish_capture_as_pdf


class PdfWebTests(unittest.TestCase):
    def test_capture_pdf_card_download_and_reload_on_desktop_and_mobile(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            def fixture(job_id, doc_id, target_url, output_dir, runtime):
                provider = 'google_docs' if 'docs.google.com' in target_url else 'scribd'
                files = []
                for number, color in enumerate(('white', 'lightblue'), start=1):
                    filename = f'page_{number:03d}.png'
                    Image.new('RGB', (400, 600), color).save(output_dir / filename)
                    files.append(filename)
                finish_capture_as_pdf(job_id, doc_id, provider, output_dir, files, runtime)
            server = make_server('127.0.0.1', 0, application.app, threaded=True)
            self.addCleanup(server.server_close)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            self.addCleanup(server.shutdown)
            with patch('app.OUTPUTS_DIR', directory), patch('app.JOBS', {}), \
                 patch.dict(application.PROVIDER_PIPELINES, {'google_docs': fixture, 'scribd': fixture}), sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                try:
                    page = browser.new_page(viewport={'width': 1280, 'height': 1000}, accept_downloads=True)
                    page.goto(f'http://127.0.0.1:{server.server_port}/')
                    for url, expected in (
                        ('https://docs.google.com/document/d/test_doc/edit', 1),
                        ('https://www.scribd.com/document/123/example', 2),
                    ):
                        page.locator('#docs_url').fill(url)
                        page.locator('#btn').click()
                        page.wait_for_function('(count) => document.querySelectorAll(".pdf-download").length === count', arg=expected)
                        self.assertTrue(page.locator('#btn').is_enabled())
                    panel = page.locator('#pdf-panel').bounding_box()
                    button = page.locator('#btn').bounding_box()
                    self.assertGreater(panel['y'], button['y'] + button['height'])
                    self.assertEqual(page.locator('#result-output-dir').count(), 0)
                    with page.expect_download() as download_event:
                        page.locator('.pdf-download').first.click()
                    download = download_event.value
                    self.assertEqual(download.suggested_filename, 'scribd_123.pdf')
                    self.assertEqual(len(PdfReader(download.path()).pages), 2)
                    page.reload()
                    page.wait_for_function('document.querySelectorAll(".pdf-download").length === 2')
                    previews = Path(__file__).resolve().parent.parent / 'tmp/pdfs'
                    previews.mkdir(parents=True, exist_ok=True)
                    page.screenshot(path=str(previews / 'pdf_download_desktop.png'), full_page=True)
                    page.set_viewport_size({'width': 390, 'height': 844})
                    self.assertTrue(page.evaluate('document.documentElement.scrollWidth <= innerWidth'))
                    page.screenshot(path=str(previews / 'pdf_download_mobile.png'), full_page=True)
                finally:
                    browser.close()


if __name__ == '__main__':
    unittest.main()
