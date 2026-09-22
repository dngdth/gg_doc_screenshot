import unittest

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


if __name__ == "__main__":
    unittest.main()
