import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import Mock, patch

from PIL import Image
from pypdf import PdfReader

import app as application
from document_processing import finish_capture_as_pdf, images_to_pdf
from providers.common import CaptureRuntime


class PdfExportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)

    def png(self, name, color='white', size=(100, 150), mode='RGB'):
        path = self.directory / name
        Image.new(mode, size, color).save(path)
        return path

    def test_pages_follow_numeric_order_and_preserve_pixels_and_aspect_ratio(self):
        paths = [self.png('page_010.png', 'blue'), self.png('page_002.png', 'green'), self.png('page_001.png', 'red')]
        result = images_to_pdf(paths, self.directory / 'document.pdf')
        reader = PdfReader(result.path)
        self.assertEqual(result.pages, 3)
        self.assertEqual(len(reader.pages), 3)
        self.assertGreater(result.size_bytes, 0)
        for page, color in zip(reader.pages, [(255, 0, 0), (0, 128, 0), (0, 0, 255)]):
            self.assertAlmostEqual(float(page.mediabox.width) / float(page.mediabox.height), 100 / 150)
            image = page.images[0].image.convert('RGB')
            self.assertEqual(image.size, (100, 150))
            self.assertEqual(image.getpixel((5, 5)), color)

    def test_transparency_has_white_background(self):
        image = self.png('page_001.png', (255, 0, 0, 0), mode='RGBA')
        result = images_to_pdf([image], self.directory / 'document.pdf')
        self.assertEqual(PdfReader(result.path).pages[0].images[0].image.convert('RGB').getpixel((10, 10)), (255, 255, 255))

    def test_empty_input_and_bad_dpi_do_not_publish_pdf(self):
        destination = self.directory / 'document.pdf'
        for paths, dpi in (([], 150), ([self.png('page_001.png')], 0)):
            with self.assertRaises(ValueError):
                images_to_pdf(paths, destination, dpi=dpi)
            self.assertFalse(destination.exists())

    def test_corrupt_input_preserves_existing_pdf_and_cleans_temporary_file(self):
        first = self.png('page_001.png')
        destination = self.directory / 'document.pdf'
        images_to_pdf([first], destination)
        original = destination.read_bytes()
        bad = self.directory / 'page_002.png'
        bad.write_bytes(b'not an image')
        with self.assertRaises(OSError):
            images_to_pdf([first, bad], destination)
        self.assertEqual(destination.read_bytes(), original)
        self.assertFalse(list(self.directory.glob('.pdf-*.tmp')))

    def test_failed_pdf_generation_does_not_report_a_successful_job(self):
        runtime = CaptureRuntime({}, Mock(), Mock())
        finish_capture_as_pdf('job', '123', 'scribd', self.directory, ['missing.png'], runtime)
        self.assertEqual(runtime.update_job.call_args.kwargs['status'], 'building_pdf')
        self.assertFalse(runtime.finish_job.call_args.args[1])
        self.assertNotIn('pdf_file', runtime.finish_job.call_args.kwargs)


class PdfDownloadTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name) / 'outputs'
        self.directory.mkdir()
        self.root_patch = patch('app.OUTPUTS_DIR', self.directory)
        self.jobs_patch = patch('app.JOBS', {})
        self.root_patch.start()
        self.jobs_patch.start()
        self.addCleanup(self.root_patch.stop)
        self.addCleanup(self.jobs_patch.stop)
        self.client = application.app.test_client()

    def ready_job(self):
        job_id = application.new_job('123', 'https://www.scribd.com/document/123/example', 'scribd')
        output = self.directory / job_id
        output.mkdir()
        Image.new('RGB', (100, 150), 'white').save(output / 'page_001.png')
        runtime = CaptureRuntime({}, application.update_job, application.finish_job)
        finish_capture_as_pdf(job_id, '123', 'scribd', output, ['page_001.png'], runtime)
        return job_id, output

    def test_status_contains_download_link_without_internal_paths_or_png_list(self):
        job_id, _ = self.ready_job()
        job = self.client.get(f'/status/{job_id}').get_json()['job']
        self.assertTrue(job['success'])
        self.assertEqual(job['pdf']['pages'], 1)
        self.assertNotIn('output_dir', job)
        self.assertNotIn('files', job)
        self.assertNotIn('pdf_file', job)
        response = self.client.get(job['pdf']['url'])
        self.addCleanup(response.close)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, 'application/pdf')
        self.assertIn('attachment;', response.headers['Content-Disposition'])
        self.assertTrue(response.data.startswith(b'%PDF'))
        self.assertEqual(len(PdfReader(BytesIO(response.data)).pages), 1)
        listed = self.client.get('/pdfs').get_json()['files']
        self.assertEqual(listed[0]['job_id'], job_id)

    def test_pdf_not_downloadable_until_job_is_complete(self):
        job_id, _ = self.ready_job()
        application.JOBS[job_id]['status'] = 'building_pdf'
        self.assertEqual(self.client.get(f'/download_pdf/{job_id}').status_code, 404)
        self.assertIsNone(self.client.get(f'/status/{job_id}').get_json()['job']['pdf'])
        self.assertEqual(self.client.get('/pdfs').get_json()['files'], [])

    def test_rejects_unknown_job_missing_file_and_filename_traversal(self):
        self.assertEqual(self.client.get('/download_pdf/not-a-job').status_code, 404)
        job_id, output = self.ready_job()
        application.JOBS[job_id]['pdf_file'] = '../secret.pdf'
        self.assertEqual(self.client.get(f'/download_pdf/{job_id}').status_code, 404)
        application.JOBS[job_id]['pdf_file'] = 'missing.pdf'
        self.assertEqual(self.client.get(f'/download_pdf/{job_id}').status_code, 404)
        application.JOBS[job_id]['output_dir'] = str(output.parent.parent)
        self.assertEqual(self.client.get(f'/download_pdf/{job_id}').status_code, 404)


if __name__ == '__main__':
    unittest.main()
