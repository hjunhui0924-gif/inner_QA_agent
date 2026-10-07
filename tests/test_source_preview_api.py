"""Stored citations, current ACL and revision-bound windows at the API seam."""
from unittest.mock import AsyncMock, patch
import unittest

from fastapi import HTTPException
from starlette.requests import Request

from backend.api.routes import knowledge_record_preview
from backend.auth.access import ServerAccessContext


class SourcePreviewApiTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.request = Request({"type": "http", "method": "GET", "path": "/", "headers": []})
        self.request.state.access_context = ServerAccessContext(
            user_id="alice", roles=frozenset({"employee"}),
            departments=frozenset({"Finance"}), scopes=frozenset({"internal"}),
        )
        self.record = {"id": "policy", "title": "制度", "content": "🙂经理审批。", "content_checksum": "current",
            "department": "Finance", "access_scope": "internal", "original_filename": "missing.docx"}
        self.record_patch = patch("backend.api.routes.get_knowledge_record", side_effect=lambda _: self.record)
        self.record_patch.start()
        self.addCleanup(self.record_patch.stop)
        self.completed = AsyncMock(return_value={"citations": [
            {"citation_id": "C1", "source_id": "policy", "quote": "经理审批", "source_content_checksum": "current"}
        ]})
        self.turn_patch = patch("backend.api.routes.load_completed_turn", self.completed)
        self.turn_patch.start()
        self.addCleanup(self.turn_patch.stop)

    async def preview(self, *, source="policy", session="session-a", turn="turn-a", citation="C1", cursor=None):
        return await knowledge_record_preview(self.request, source, session_id=session, turn_id=turn, citation_id=citation, cursor=cursor)

    async def test_target_binds_stored_turn_to_server_identity_and_reports_revision(self):
        result = await self.preview()
        self.completed.assert_awaited_once_with("alice", "session-a", "turn-a")
        self.assertEqual(result["revision_match"], "same")
        self.assertEqual(result["format"], "docx")
        self.assertFalse(result["original_available"])
        block = result["blocks"][0]
        location = result["location"]
        self.assertEqual(block["text"][location["start"]:location["end"]], "经理审批")
        self.record["content_checksum"] = "updated"
        self.assertEqual((await self.preview())["revision_match"], "changed")
        self.completed.return_value["citations"][0].pop("source_content_checksum")
        self.assertEqual((await self.preview())["revision_match"], "unknown")

    async def test_forged_source_citation_and_missing_turn_do_not_return_text(self):
        for parameters in ({"source": "other"}, {"citation": "C2"}):
            with self.assertRaises(HTTPException) as raised:
                await self.preview(**parameters)
            self.assertEqual(raised.exception.status_code, 404)
        self.completed.return_value = None
        with self.assertRaises(HTTPException) as raised:
            await self.preview(session="other-session", turn="other-turn")
        self.assertEqual(raised.exception.status_code, 404)

    async def test_revoked_access_is_checked_again_on_each_window_request(self):
        await self.preview()
        self.record["department"] = "Legal"
        self.completed.reset_mock()
        with self.assertRaises(HTTPException) as raised:
            await self.preview()
        self.assertEqual(raised.exception.status_code, 404)
        self.completed.assert_not_awaited()

    async def test_cursor_tampering_cross_source_and_version_changes_are_rejected(self):
        self.record["content"] = "\n\n".join("段落" + str(i) for i in range(30))
        response = await self.preview(session=None, turn=None, citation=None)
        cursor = response["next_cursor"]
        self.assertTrue(cursor)
        next_page = await self.preview(session=None, turn=None, citation=None, cursor=cursor)
        self.assertEqual(next_page["blocks"][0]["text"], "段落12")
        for source, value in (("policy", cursor + "bad"), ("other", cursor)):
            with self.assertRaises(HTTPException) as raised:
                await self.preview(source=source, session=None, turn=None, citation=None, cursor=value)
            self.assertEqual(raised.exception.status_code, 422)
        self.record["content_checksum"] = "updated"
        with self.assertRaises(HTTPException) as raised:
            await self.preview(session=None, turn=None, citation=None, cursor=cursor)
        self.assertEqual(raised.exception.status_code, 422)

    async def test_partial_target_is_rejected_and_legacy_records_still_preview(self):
        with self.assertRaises(HTTPException) as raised:
            await self.preview(citation=None)
        self.assertEqual(raised.exception.status_code, 422)
        self.record.pop("content_checksum")
        self.record.pop("original_filename")
        response = await self.preview(session=None, turn=None, citation=None)
        self.assertTrue(response["content_checksum"])
        self.assertEqual(response["location"]["status"], "unavailable")
        self.assertIsNone(response["blocks"][0]["page"])


if __name__ == "__main__":
    unittest.main()
