"""Search/provider separation, provenance, failure and cancellation contracts."""
import asyncio
import json
import unittest
from unittest.mock import patch

import httpx

from backend.agent import web_search
from backend.config import settings


class TavilySearchTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        for name, value in {"web_search_provider": "tavily", "tavily_api_key": "search-key",
                            "model_provider": "deepseek", "deepseek_api_key": "model-key",
                            "deepseek_base_url": "https://api.deepseek.com", "model_name": "deepseek-v4-pro"}.items():
            p = patch.object(settings, name, value)
            p.start()
            self.addCleanup(p.stop)
        self.requests = []
        self.search_response = {"results": [
            {"title": "unsafe", "url": "javascript:alert(1)", "content": "bad"},
            {"title": "Python", "url": "https://www.python.org/", "content": "Python is a programming language."},
            {"title": "duplicate", "url": "https://www.python.org/", "content": "duplicate"}]}
        self.answer = {"choices": [{"finish_reason": "stop", "message": {"content": "Python 是编程语言。[C1]"}}],
                       "usage": {"prompt_tokens": 100, "completion_tokens": 15}}
        self.status = 200

    def handle(self, request):
        self.requests.append(request)
        is_search = request.url.host == "api.tavily.com"
        self.assertEqual(request.headers['Authorization'], 'Bearer search-key' if is_search else 'Bearer model-key')
        return httpx.Response(self.status if is_search else 200, json=self.search_response if is_search else self.answer)

    async def run_search(self):
        client_type = httpx.AsyncClient
        with patch.object(httpx, "AsyncClient", side_effect=lambda **kw: client_type(transport=httpx.MockTransport(self.handle), **kw)):
            return await web_search.search_async("Python 是什么？")

    async def test_success_preserves_sources_and_one_model_call(self):
        result = await self.run_search()
        self.assertTrue(result['ok'])
        self.assertEqual(len(self.requests), 2)
        self.assertEqual(result['usage'], {'input_tokens': 100, 'output_tokens': 15})
        self.assertEqual(result['sources'], [{'index': 1, 'title': 'Python', 'url': 'https://www.python.org/'}])
        search = json.loads(self.requests[0].content)
        self.assertEqual(search['search_depth'], 'basic')
        self.assertFalse(search['include_answer'])
        model = json.loads(self.requests[1].content)
        self.assertEqual(model['thinking'], {'type': 'disabled'})
        self.assertEqual(model['model'], 'deepseek-v4-pro')
        self.assertIn('Python is a programming language.', model['messages'][1]['content'])
        self.assertEqual(web_search.web_citations(result['text'], result['sources'])[0]['citation_id'], 'C1')

    async def test_empty_search_skips_model(self):
        self.search_response = {'results': []}
        result = await self.run_search()
        self.assertEqual(result['error'], 'web_search_no_results')
        self.assertEqual(len(self.requests), 1)

    async def test_invalid_citation_and_truncation_fail_closed_keep_usage(self):
        for content, finish in [('Invented [C9]', 'stop'), ('No citation', 'stop'), ('Partial [C1]', 'length')]:
            self.answer['choices'][0] = {'finish_reason': finish, 'message': {'content': content}}
            result = await self.run_search()
            self.assertEqual(result['error'], 'web_answer_invalid')
            self.assertEqual(result['text'], '')
            self.assertEqual(result['usage']['output_tokens'], 15)

    async def test_missing_key_and_http_errors_are_safe(self):
        with patch.object(settings, 'tavily_api_key', ''):
            self.assertEqual((await self.run_search())['error'], 'web_search_unavailable')
        self.assertEqual(self.requests, [])
        for status in [401, 429, 500]:
            self.status = status
            result = await self.run_search()
            self.assertEqual(result['error'], 'web_search_unavailable')
            self.assertEqual(result['sources'], [])

    async def test_total_timeout_and_cancellation(self):
        async def slow(request):
            await asyncio.sleep(0.1)
            return self.handle(request)
        client_type = httpx.AsyncClient
        with patch.object(httpx, 'AsyncClient', side_effect=lambda **kw: client_type(transport=httpx.MockTransport(slow), **kw)):
            with patch.object(settings, 'web_search_timeout_seconds', 0.01):
                self.assertEqual((await web_search.search_async('Python'))['error'], 'web_search_unavailable')
            task = asyncio.create_task(web_search.search_async('Python'))
            await asyncio.sleep(0.01)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task

    async def test_sync_entry_works_even_with_running_event_loop(self):
        client_type = httpx.Client
        with patch.object(httpx, 'Client', side_effect=lambda **kw: client_type(transport=httpx.MockTransport(self.handle), **kw)):
            result = web_search.search_sync('Python')
        self.assertTrue(result['ok'])
        self.assertEqual(len(self.requests), 2)
