"""Offline tests for official corpus extraction safeguards."""

from __future__ import annotations

import unittest

from scripts.download_eval_corpus import _extract_text, _validate_source


class CorpusDownloaderTests(unittest.TestCase):
    def test_redirect_to_untrusted_host_is_rejected(self) -> None:
        item = {"allowed_host": "www.gov.cn"}

        with self.assertRaisesRegex(ValueError, "left the allowed host"):
            _validate_source(item, "https://example.com/copied-policy")

    def test_article_extraction_preserves_long_official_body(self) -> None:
        title = "测试条例"
        body = "第一条 这是正式正文。" * 150
        html = f"<html><body><article><h1>{title}</h1><p>{body}</p></article></body></html>"

        extracted = _extract_text(html, title)

        self.assertIn(title, extracted)
        self.assertIn("第一条", extracted)
        self.assertGreater(len(extracted), 1200)


if __name__ == "__main__":
    unittest.main()

