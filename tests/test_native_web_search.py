"""Provider wire-contract, source provenance, cancellation and budget regressions."""
import asyncio
import json
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from langgraph.runtime import Runtime

from backend.agent import web_search
from backend.agent.nodes import tool_executor, generate, check_hallucination
from backend.observability.budget import RequestBudget, RunContext


SOURCES = [{"index": 3, "title": "Python", "url": "https://www.python.org/"}]


def event(text="", sources=None, finish="null", usage=None):
    return "data: " + json.dumps({"output": {
        "choices": [{"message": {"content": [{"text": text}]}, "finish_reason": finish}],
        "search_info": {"search_results": sources or []},
    }, "usage": usage or {}})


class SearchStreamTests(unittest.TestCase):
    def test_final_rejected_event_still_records_usage(self):
        stream = web_search.SearchStream()
        with self.assertRaises(web_search.WebSearchError):
            stream.consume(event("partial", SOURCES, "length", {"input_tokens": 700, "output_tokens": 1200}))
        self.assertEqual(stream.usage, {"input_tokens": 700, "output_tokens": 1200})

    def test_referenced_source_after_first_ten_is_preserved(self):
        sources = [{"index": index, "title": "Page", "url": f"https://example.com/{index}"} for index in range(1, 12)]
        stream = web_search.SearchStream()
        stream.consume(event("Answer [ref_11]", sources, "stop"))
        result = stream.result("question")
        self.assertEqual([item["index"] for item in result["sources"]], [11])

    def test_oversized_unterminated_line_is_rejected_before_line_decoding(self):
        stream = web_search.SearchStream()
        with self.assertRaises(web_search.WebSearchError):
            for _ in range(123):
                stream.feed(b'x' * 16384)
        self.assertLessEqual(len(stream.buffer), 2_000_000)

    def test_utf8_and_sse_lines_can_split_at_any_byte(self):
        wire = event("中文回答 [ref_3]。", SOURCES, "stop").encode('utf-8')
        stream = web_search.SearchStream()
        for byte in wire:
            stream.feed(bytes([byte]))
        stream.finish_input()
        self.assertEqual(stream.result("question")["text"], "中文回答 [C3]。")

    def test_incremental_answer_keeps_non_sequential_source_indices(self):
        stream = web_search.SearchStream()
        stream.consume(event("Python 是编程语言 [ref_", SOURCES, usage={"input_tokens": 20, "output_tokens": 2}))
        stream.consume(event("3]。", SOURCES, "stop", {"input_tokens": 20, "output_tokens": 8}))
        result = stream.result("Python 是什么？")
        self.assertEqual(result["text"], "Python 是编程语言 [C3]。")
        self.assertEqual(result["usage"], {"input_tokens": 20, "output_tokens": 8})
        citations = web_search.web_citations(result["text"], result["sources"])
        self.assertEqual(citations[0]["url"], SOURCES[0]["url"])
        self.assertEqual(citations[0]["quote"], "")  # Generated text is not a verbatim webpage quote.

    def test_no_results_is_distinct_from_broken_or_incomplete_stream(self):
        stream = web_search.SearchStream()
        with self.assertRaisesRegex(web_search.WebSearchError, "web_search_unavailable"):
            stream.result("question")
        stream.consume(event("未找到", finish="stop"))
        with self.assertRaisesRegex(web_search.WebSearchError, "web_search_no_results"):
            stream.result("question")

    def test_malformed_or_unsafe_citations_are_not_accepted(self):
        for answer in ['```json\n{"answer":"Python"}\n```', 'Python', 'Python [C0][C3]', 'Python [C9]']:
            self.assertEqual(web_search.web_citations(answer, SOURCES), [])
        for url in ["javascript:alert(1)", "https://user:password@example.com/", "https://example.com/\n"]:
            self.assertEqual(web_search.safe_web_url(url), "")
        stream = web_search.SearchStream()
        stream.consume(event("Python [ref_3]", SOURCES))
        with self.assertRaisesRegex(web_search.WebSearchError, "web_answer_invalid"):
            stream.consume(event(sources=[{**SOURCES[0], "url": "https://different.example/"}]))

    def test_http_200_error_event_and_token_truncation_fail_closed(self):
        for line in ['data: {"code":"Throttling"}', event("partial", SOURCES, "length")]:
            with self.assertRaises(web_search.WebSearchError):
                web_search.SearchStream().consume(line)


class NativeSearchTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_http_adapter_parses_sse_and_sets_native_options(self):
        def handle(request):
            payload = json.loads(request.content)
            self.assertTrue(payload["parameters"]["search_options"]["forced_search"])
            self.assertTrue(payload["parameters"]["search_options"]["enable_citation"])
            return httpx.Response(200, text=event("Python 是编程语言 [ref_3]。", SOURCES, "stop"))
        client_type = httpx.AsyncClient
        with patch.object(web_search.settings, "dashscope_api_key", "test-key"), patch.object(web_search.httpx, "AsyncClient", side_effect=lambda **kw: client_type(transport=httpx.MockTransport(handle), **kw)):
            result = await web_search.search_async("Python 是什么？")
        self.assertTrue(result["ok"])

    async def test_native_timeout_returns_safe_error(self):
        async def handle(request):
            await asyncio.sleep(0.1)
            return httpx.Response(200)
        client_type = httpx.AsyncClient
        with patch.object(web_search.settings, "dashscope_api_key", "test-key"), patch.object(web_search.settings, "web_search_timeout_seconds", 0.01), patch.object(web_search.httpx, "AsyncClient", side_effect=lambda **kw: client_type(transport=httpx.MockTransport(handle), **kw)):
            result = await web_search.search_async("Python")
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "web_search_unavailable")

    async def test_search_counts_model_tool_and_usage_without_regeneration(self):
        budget = RequestBudget(max_model_calls=2, max_tool_calls=1, max_total_seconds=5)
        runtime = Runtime(context=RunContext(budget=budget))
        state = {"mode": "general", "web_search": True, "route": "tool_call", "query": "Python 是什么？"}
        response = {"ok": True, "text": "Python 是编程语言 [C3]。", "sources": SOURCES, "error": None, "usage": {"input_tokens": 20, "output_tokens": 8}}
        with patch('backend.agent.nodes.search_async', new=AsyncMock(return_value=response)):
            state.update(await tool_executor(state, runtime))
        with patch('backend.agent.nodes._build_model') as model:
            state.update(await generate(state, runtime))
            state.update(await check_hallucination(state, runtime))
        model.assert_not_called()
        self.assertTrue(state["hallucination_pass"])
        self.assertEqual((budget.model_calls, budget.tool_calls, budget.input_tokens, budget.output_tokens), (1, 1, 20, 8))
        self.assertEqual(state["candidate_citations"][0]["citation_id"], "C3")

    async def test_model_budget_exhaustion_prevents_network_search(self):
        runtime = Runtime(context=RunContext(budget=RequestBudget(max_model_calls=0, max_tool_calls=1, max_total_seconds=5)))
        with patch('backend.agent.nodes.search_async', new=AsyncMock()) as search:
            result = await tool_executor({"mode": "general", "web_search": True, "query": "search"}, runtime)
        search.assert_not_called()
        self.assertEqual(result["failure_stage"], "runtime")
