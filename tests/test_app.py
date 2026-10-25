import unittest
from pathlib import Path
from unittest.mock import patch

from app import app, extract_document_info


class ExtractDocumentInfoTests(unittest.TestCase):
    def test_google_docs_url(self):
        result = extract_document_info(
            "https://docs.google.com/document/d/abc_DEF-123/edit?tab=t.0"
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["provider"], "google_docs")
        self.assertEqual(result["doc_id"], "abc_DEF-123")

    def test_localized_scribd_url(self):
        result = extract_document_info(
            "https://fr.scribd.com/document/659488932/Reading-explorer-3"
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["provider"], "scribd")
        self.assertEqual(result["doc_id"], "659488932")

    def test_legacy_scribd_doc_url(self):
        result = extract_document_info("https://www.scribd.com/doc/12345/example")

        self.assertTrue(result["ok"])
        self.assertEqual(result["provider"], "scribd")
        self.assertEqual(result["doc_id"], "12345")

    def test_studocu_url(self):
        result = extract_document_info(
            "https://www.studocu.com/in/document/anna-university/computer-science/notes/127987574"
        )

        self.assertTrue(result["ok"])
        self.assertEqual(result["provider"], "studocu")
        self.assertEqual(result["doc_id"], "127987574")

    def test_rejects_studocu_lookalike_hostname(self):
        result = extract_document_info(
            "https://studocu.com.evil.example/in/document/example/127987574"
        )

        self.assertFalse(result["ok"])

    def test_studocu_vn_url(self):
        for hostname in ("studocu.vn", "www.studocu.vn"):
            with self.subTest(hostname=hostname):
                url = f"https://{hostname}/vn/document/truong-dai-hoc/mon-hoc/tai-lieu/127987574?origin=share"
                result = extract_document_info(url)

                self.assertTrue(result["ok"])
                self.assertEqual(result["provider"], "studocu")
                self.assertEqual(result["doc_id"], "127987574")
                self.assertEqual(result["url"], url)

    def test_rejects_studocu_vn_lookalike_hostnames(self):
        for hostname in ("studocu.vn.evil.example", "fakestudocu.vn"):
            with self.subTest(hostname=hostname):
                result = extract_document_info(
                    f"https://{hostname}/vn/document/example/127987574"
                )

                self.assertFalse(result["ok"])

    def test_rejects_studocu_vn_non_document_urls(self):
        for path in ("/vn", "/vn/course/example/12345", "/vn/document/example"):
            with self.subTest(path=path):
                result = extract_document_info(f"https://www.studocu.vn{path}")

                self.assertFalse(result["ok"])

    def test_rejects_lookalike_hostname(self):
        result = extract_document_info(
            "https://scribd.com.evil.example/document/659488932/example"
        )

        self.assertFalse(result["ok"])


class IndexTests(unittest.TestCase):
    def test_index_mentions_all_sources(self):
        with app.test_client() as client:
            response = client.get("/")

        self.assertEqual(response.status_code, 200)
        html = response.get_data(as_text=True)
        self.assertIn("Google Docs", html)
        self.assertIn("Scribd", html)
        self.assertIn("Studocu", html)


class StartCaptureTests(unittest.TestCase):
    def test_studocu_vn_starts_studocu_pipeline(self):
        url = "https://www.studocu.vn/vn/document/truong-dai-hoc/mon-hoc/tai-lieu/127987574"
        output_dir = Path("outputs/test-studocu-vn")
        with (
            patch("app.make_output_dir", return_value=output_dir) as make_output_dir,
            patch("app.JOBS", {}),
            patch("app.threading.Thread") as thread,
            app.test_client() as client,
        ):
            response = client.post("/start_capture", data={"docs_url": url})

            self.assertEqual(response.status_code, 200)
            data = response.get_json()
            self.assertTrue(data["ok"])
            make_output_dir.assert_called_once_with("127987574", "studocu")
            self.assertEqual(thread.call_args.kwargs["target"].__name__, "capture_studocu")
            self.assertEqual(
                thread.call_args.kwargs["args"][:4],
                (data["job_id"], "127987574", url, output_dir),
            )
            thread.return_value.start.assert_called_once_with()
            job = client.get(f"/status/{data['job_id']}").get_json()["job"]
            self.assertEqual(job["provider"], "studocu")


if __name__ == "__main__":
    unittest.main()
