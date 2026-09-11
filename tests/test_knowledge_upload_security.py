"""Security contracts for knowledge upload error responses."""

from __future__ import annotations

from io import BytesIO
from unittest.mock import AsyncMock, patch

import unittest
from fastapi import HTTPException, UploadFile

from backend.api.routes import upload_knowledge_file


class KnowledgeUploadSecurityTests(unittest.IsolatedAsyncioTestCase):
    async def test_unexpected_ingestion_errors_do_not_reach_the_client(self) -> None:
        upload = UploadFile(filename="policy.txt", file=BytesIO(b"policy"))

        with patch(
            "backend.api.routes.asyncio.to_thread",
            new=AsyncMock(side_effect=RuntimeError("secret path and stack details")),
        ):
            with self.assertRaises(HTTPException) as raised:
                await upload_knowledge_file(file=upload)

        self.assertEqual(raised.exception.status_code, 500)
        self.assertEqual(raised.exception.detail, "知识库入库失败，请稍后重试。")
        self.assertNotIn("secret path", str(raised.exception.detail))


if __name__ == "__main__":
    unittest.main()
