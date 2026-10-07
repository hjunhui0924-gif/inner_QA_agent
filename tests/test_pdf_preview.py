"""Preview limits share the source download's ACL and path boundaries."""
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fastapi import HTTPException
from starlette.requests import Request

from backend.api.routes import _knowledge_original_path, download_knowledge_record
from backend.auth.access import ServerAccessContext


class PdfPreviewTests(unittest.IsolatedAsyncioTestCase):
    def request(self):
        request = Request({"type": "http", "method": "GET", "path": "/", "headers": []})
        request.state.access_context = ServerAccessContext(
            user_id="alice", roles=frozenset({"employee"}),
            departments=frozenset({"Finance"}), scopes=frozenset({"internal"}),
        )
        return request

    def record(self, filename, department="Finance"):
        return {"id": "policy", "original_filename": filename, "department": department, "access_scope": "internal"}

    async def test_limits_only_preview_and_returns_configuration_headers(self):
        with TemporaryDirectory() as directory:
            target = Path(directory) / "policy.pdf"
            target.write_bytes(b"pdf bytes")
            with patch("backend.api.routes.settings.upload_dir", directory), patch(
                "backend.api.routes.get_knowledge_record", return_value=self.record(target.name)
            ), patch("backend.api.routes.settings.pdf_preview_max_bytes", 8), patch(
                "backend.api.routes.settings.pdf_preview_max_pages", 10
            ):
                with self.assertRaises(HTTPException) as raised:
                    await download_knowledge_record(self.request(), "policy", preview=True)
                self.assertEqual(raised.exception.status_code, 413)
                response = await download_knowledge_record(self.request(), "policy")
                self.assertEqual(Path(response.path), target.resolve())
                target.write_bytes(b"pdf")
                response = await download_knowledge_record(self.request(), "policy", preview=True)
                self.assertEqual(response.headers["X-Pdf-Preview-Max-Bytes"], "8")
                self.assertEqual(response.headers["X-Pdf-Preview-Max-Pages"], "10")

    async def test_acl_still_applies_before_preview_or_download(self):
        with TemporaryDirectory() as directory:
            (Path(directory) / "policy.pdf").write_bytes(b"pdf")
            with patch("backend.api.routes.settings.upload_dir", directory), patch(
                "backend.api.routes.get_knowledge_record", return_value=self.record("policy.pdf", "Legal")
            ):
                for preview in (False, True):
                    with self.assertRaises(HTTPException) as raised:
                        await download_knowledge_record(self.request(), "policy", preview=preview)
                    self.assertIn(raised.exception.status_code, (403, 404))

    async def test_missing_files_and_unsupported_formats(self):
        with TemporaryDirectory() as directory:
            with patch("backend.api.routes.settings.upload_dir", directory), patch(
                "backend.api.routes.get_knowledge_record", return_value=self.record("policy.md")
            ):
                with self.assertRaises(HTTPException) as raised:
                    await download_knowledge_record(self.request(), "policy", preview=True)
                self.assertEqual(raised.exception.status_code, 404)
                (Path(directory) / "policy.md").write_text("text", encoding="utf-8")
                with self.assertRaises(HTTPException) as raised:
                    await download_knowledge_record(self.request(), "policy", preview=True)
                self.assertEqual(raised.exception.status_code, 415)

    def test_original_availability_requires_a_file_within_upload_root(self):
        with TemporaryDirectory() as directory, TemporaryDirectory() as outside:
            with patch("backend.api.routes.settings.upload_dir", directory):
                self.assertIsNone(_knowledge_original_path(self.record("missing.pdf")))
                self.assertIsNone(_knowledge_original_path(self.record("..")))
                self.assertIsNone(_knowledge_original_path(self.record("")))
                (Path(directory) / "policy.pdf").write_bytes(b"pdf")
                self.assertEqual(_knowledge_original_path(self.record("../policy.pdf")), Path(directory) / "policy.pdf")
                external = Path(outside) / "external.pdf"
                external.write_bytes(b"outside")
                try:
                    (Path(directory) / "link.pdf").symlink_to(external)
                except OSError:
                    # Windows may not grant symlink creation to a normal account.
                    return
                self.assertIsNone(_knowledge_original_path(self.record("link.pdf")))


if __name__ == "__main__":
    unittest.main()
