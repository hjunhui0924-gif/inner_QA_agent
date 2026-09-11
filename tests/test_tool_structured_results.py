"""Tests for structured tool results and legacy string compatibility."""

from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from backend.agent.nodes import tool_executor
from backend.agent.memory import search_knowledge_base_data
from langchain_core.documents import Document
from backend.agent.tools import (
    render_tool_output,
    normalize_tool_result,
    search_knowledge_base,
    search_knowledge_base_result,
    search_web,
    search_web_result,
)


class StructuredToolResultTests(unittest.IsolatedAsyncioTestCase):
    async def test_time_tool_result_is_serializable_and_keeps_legacy_text(self) -> None:
        with patch(
            "backend.agent.nodes.get_current_time_result",
            return_value={
                "ok": True,
                "text": "2026-09-11 23:30:00",
                "sources": [],
                "error": None,
            },
        ):
            result = await tool_executor(
                {
                    "query": "现在几点？",
                    "mode": "general",
                    "tool_call_count": 0,
                    "model_call_count": 0,
                }
            )

        self.assertEqual(result["tool_result"]["ok"], True)
        self.assertEqual(result["tool_result"]["text"], "2026-09-11 23:30:00")
        self.assertEqual(result["tool_output"], "2026-09-11 23:30:00")
        self.assertEqual(result["tool_result"]["sources"], [])
        self.assertIsNone(result["tool_result"]["error"])
        json.dumps(result["tool_result"], ensure_ascii=False)

    async def test_knowledge_search_result_preserves_legacy_text(self) -> None:
        with patch(
            "backend.agent.tools.search_knowledge_base_data",
            return_value=(
                "1. 报销制度 [internal]：需要部门负责人审批。",
                [{"source_id": "finance", "chunk_id": "finance:0"}],
                None,
            ),
        ):
            result = search_knowledge_base_result("报销审批")

        self.assertTrue(result["ok"])
        self.assertIn("报销制度", result["text"])
        self.assertEqual(result["sources"], [{"source_id": "finance", "chunk_id": "finance:0"}])
        self.assertEqual(render_tool_output(result), result["text"])

        with patch(
            "backend.agent.tools.search_knowledge_base_text",
            return_value="旧版文本结果",
        ):
            self.assertEqual(
                search_knowledge_base.invoke({"query": "报销审批"}),
                "旧版文本结果",
            )

    async def test_knowledge_search_data_preserves_bounded_source_metadata(self) -> None:
        class Retriever:
            def retrieve(self, query: str, top_k: int):
                return type(
                    "Retrieval",
                    (),
                    {
                        "documents": [
                            Document(
                                page_content="证据内容",
                                metadata={
                                    "source_id": "source-1",
                                    "document_id": "document-1",
                                    "chunk_id": "chunk-1",
                                    "title": "标题",
                                    "source": "internal",
                                    "original_filename": "policy.md",
                                    "page": 2,
                                    "section": "第一节",
                                },
                            )
                        ]
                    },
                )()

        with patch("backend.agent.memory.get_retriever", return_value=Retriever()):
            text, sources, error = search_knowledge_base_data("证据", top_k=1)

        self.assertIn("标题", text)
        self.assertEqual(error, None)
        self.assertEqual(sources[0]["chunk_id"], "chunk-1")
        json.dumps(sources, ensure_ascii=False, allow_nan=False)

    async def test_knowledge_search_failure_uses_stable_code_without_exception_details(self) -> None:
        with patch(
            "backend.agent.tools.search_knowledge_base_data",
            return_value=("知识库暂时不可用。", [], "knowledge_base_unavailable"),
        ):
            result = search_knowledge_base_result("报销审批")

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "knowledge_base_unavailable")
        self.assertNotIn("Traceback", render_tool_output(result))
        self.assertIn("知识库暂时不可用", render_tool_output(result))

        with patch(
            "backend.agent.tools.search_knowledge_base_data",
            side_effect=RuntimeError("C:\\private\\memory.db"),
        ):
            result = search_knowledge_base_result("报销审批")
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "knowledge_base_unavailable")
        self.assertNotIn("memory.db", str(result))

    async def test_web_search_result_retains_optional_public_sources(self) -> None:
        with patch(
            "backend.agent.tools._search_web_payload",
            return_value=(
                "网页摘要",
                [{"title": "公开页面", "url": "https://example.com", "snippet": "网页摘要"}],
            ),
        ):
            result = search_web_result("企业知识助手")

        self.assertTrue(result["ok"])
        self.assertEqual(result["text"], "网页摘要")
        self.assertEqual(result["sources"][0]["url"], "https://example.com")
        json.dumps(result, ensure_ascii=False)

        with patch(
            "backend.agent.tools._search_web_payload",
            return_value=("旧版网页摘要", []),
        ):
            self.assertEqual(search_web.invoke({"query": "企业知识助手"}), "旧版网页摘要")

    async def test_web_search_does_not_create_sources_without_verifiable_urls(self) -> None:
        with patch(
            "backend.agent.tools._search_web_payload",
            return_value=("无链接摘要", [{"title": "摘要", "url": "", "snippet": "无链接摘要"}]),
        ):
            result = search_web_result("无链接")

        self.assertTrue(result["ok"])
        self.assertEqual(result["sources"], [])

    async def test_empty_web_search_is_explicit_and_does_not_fake_sources(self) -> None:
        with patch(
            "backend.agent.tools._search_web_payload",
            return_value=("公开网页没有返回可用摘要。", []),
        ):
            result = search_web_result("没有结果的问题")

        self.assertTrue(result["ok"])
        self.assertEqual(result["text"], "公开网页没有返回可用摘要。")
        self.assertEqual(result["sources"], [])

    async def test_web_search_exception_becomes_failed_structured_result(self) -> None:
        with patch(
            "backend.agent.tools._search_web_payload",
            side_effect=TimeoutError("upstream timeout"),
        ):
            result = search_web_result("实时问题")

        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "web_search_unavailable")
        self.assertTrue(render_tool_output(result).startswith("工具调用失败："))

    async def test_tool_executor_exposes_structured_failure_and_legacy_error_text(self) -> None:
        with patch(
            "backend.agent.nodes._choose_tool_result",
            return_value={
                "ok": False,
                "text": "",
                "sources": [],
                "error": "web_search_unavailable",
            },
        ):
            result = await tool_executor({"query": "实时问题"})

        self.assertEqual(result["tool_result"]["ok"], False)
        self.assertEqual(result["failure_stage"], "tool")
        self.assertEqual(result["failure_reason"], "web_search_unavailable")
        self.assertEqual(result["tool_output"], "工具调用失败：联网搜索暂时不可用，请稍后重试。")

    async def test_malformed_tool_results_fail_closed_at_runtime_boundary(self) -> None:
        malformed = normalize_tool_result(
            {"ok": "false", "text": "should not pass", "sources": [], "error": None}
        )
        self.assertFalse(malformed["ok"])
        self.assertEqual(malformed["error"], "tool_unavailable")

        incomplete = normalize_tool_result({"ok": True, "text": "only text"})
        self.assertFalse(incomplete["ok"])
        self.assertEqual(incomplete["error"], "tool_unavailable")

        with patch(
            "backend.agent.nodes._choose_tool_result",
            return_value={"ok": "false", "text": "leaked", "sources": [], "error": None},
        ):
            result = await tool_executor({"query": "异常工具"})
        self.assertFalse(result["tool_result"]["ok"])
        self.assertEqual(result["failure_stage"], "tool")
        self.assertNotIn("leaked", result["tool_output"])

        malformed_sources = normalize_tool_result(
            {
                "ok": True,
                "text": "answer",
                "sources": ["not-a-source"],
                "error": None,
            }
        )
        self.assertFalse(malformed_sources["ok"])
        self.assertEqual(malformed_sources["error"], "tool_unavailable")

    async def test_source_metadata_is_bounded_cycle_safe_and_finite(self) -> None:
        bounded = normalize_tool_result(
            {
                "ok": True,
                "text": "answer",
                "sources": [
                    {"title": "t" * 10_000, "snippet": "s" * 10_000}
                    for _ in range(20)
                ],
                "error": None,
            }
        )
        self.assertTrue(bounded["ok"])
        self.assertLessEqual(len(bounded["sources"]), 10)
        self.assertLessEqual(len(bounded["sources"][0]["title"]), 500)
        self.assertLessEqual(len(bounded["sources"][0]["snippet"]), 1_200)
        json.dumps(bounded, ensure_ascii=False, allow_nan=False)

        cyclic: dict[str, object] = {}
        cyclic["self"] = cyclic
        cyclic_result = normalize_tool_result(
            {
                "ok": True,
                "text": "answer",
                "sources": [
                    {
                        "title": "t" * 10_000,
                        "snippet": "s" * 10_000,
                        "value": float("nan"),
                        "cycle": cyclic,
                    }
                    for _ in range(20)
                ],
                "error": None,
            }
        )
        self.assertFalse(cyclic_result["ok"])
        self.assertEqual(cyclic_result["error"], "tool_unavailable")

        unknown_object = normalize_tool_result(
            {
                "ok": True,
                "text": "answer",
                "sources": [{"object": object()}],
                "error": None,
            }
        )
        self.assertFalse(unknown_object["ok"])
        self.assertEqual(unknown_object["error"], "tool_unavailable")

    async def test_knowledge_failure_reaches_executor_as_tool_failure_without_details(self) -> None:
        with patch(
            "backend.agent.tools.search_knowledge_base_data",
            side_effect=RuntimeError("D:\\private\\memory.db"),
        ):
            result = await tool_executor(
                {"query": "报销审批", "mode": "knowledge"}
            )

        self.assertFalse(result["tool_result"]["ok"])
        self.assertEqual(result["tool_result"]["error"], "knowledge_base_unavailable")
        self.assertEqual(result["failure_stage"], "tool")
        self.assertNotIn("memory.db", str(result))


if __name__ == "__main__":
    unittest.main()
