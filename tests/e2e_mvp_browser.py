"""Deterministic browser acceptance checks for the MVP frontend.

The browser talks to the real Vite application.  API responses are mocked at
the browser boundary so the checks remain repeatable and focus on controls,
navigation, streaming rendering, evidence, upload feedback, and download
behavior.  Backend parsing, persistence, ACL, and rollback remain covered by
the Python integration tests.
"""

from __future__ import annotations

import json
from pathlib import Path

from playwright.sync_api import Page, Playwright, expect, sync_playwright


ROOT = Path(__file__).resolve().parents[1]
BASE_URL = "http://127.0.0.1:5173"


def sse(payload: dict[str, object]) -> str:
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def install_api_fixtures(page: Page) -> None:
    state: dict[str, object] = {
        "chat_calls": 0,
        "uploaded": False,
        "current_session_id": "browser-session",
        "session_deleted": False,
    }
    seed_record = {
        "source_id": "finance-v2",
        "title": "费用报销制度",
        "source": "synthetic_seed",
        "source_type": "policy",
        "department": "Finance",
        "version": "v2",
        "status": "active",
        "effective_from": "2026-01-01",
        "owner": "财务部",
        "access_scope": "internal",
        "original_filename": "finance-v2.md",
        "preview": "单笔超过5000元还需财务负责人复核。",
        "content_checksum": "checksum-finance",
    }
    uploaded_record = {
        "source_id": "upload-policy-v1",
        "title": "新差旅政策",
        "source": "internal_upload",
        "source_type": "internal_upload",
        "department": "Finance",
        "version": "v1",
        "status": "active",
        "effective_from": "2026-09-01",
        "owner": "user_001",
        "access_scope": "internal",
        "original_filename": "travel-policy.md",
        "preview": "差旅申请需要直属主管审批。",
        "content_checksum": "checksum-upload",
    }

    def records() -> list[dict[str, object]]:
        return [seed_record, uploaded_record] if state["uploaded"] else [seed_record]

    def handle(route) -> None:  # type: ignore[no-untyped-def]
        request = route.request
        path = request.url.split("/api", 1)[-1].split("?", 1)[0]
        method = request.method

        if method == "GET" and path == "/health":
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(
                    {"status": "ok", "model": "qwen3.5-ocr", "retrieval_strategy": "rerank"}
                ),
            )
            return
        if method == "GET" and path == "/chat/sessions/user_001":
            sessions = []
            if int(state["chat_calls"]) > 0 and not state["session_deleted"]:
                sessions = [
                    {
                        "session_id": str(state["current_session_id"]),
                        "title": "费用报销制度",
                        "created_at": "",
                        "updated_at": "",
                        "last_message": "继续追问",
                    }
                ]
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps({"items": sessions}, ensure_ascii=False),
            )
            return
        if method == "DELETE" and path.startswith("/chat/session/user_001/"):
            state["session_deleted"] = True
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps({"message": "会话已删除。"}, ensure_ascii=False),
            )
            return
        if method == "GET" and path == "/knowledge/records":
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps({"items": records()}, ensure_ascii=False),
            )
            return
        if method == "GET" and path.endswith("/download"):
            route.fulfill(
                status=200,
                headers={"Content-Type": "text/markdown"},
                body="# 新差旅政策\n差旅申请需要直属主管审批。",
            )
            return
        if method == "POST" and path == "/chat/stream":
            payload = request.post_data_json or {}
            if isinstance(payload, dict) and payload.get("session_id"):
                state["current_session_id"] = str(payload["session_id"])
            state["chat_calls"] = int(state["chat_calls"]) + 1
            call_number = int(state["chat_calls"])
            answer = (
                "差旅报销需要直属主管审批。[C1]"
                if call_number == 1
                else "超过5000元还需要财务负责人复核。[C1]"
            )
            body = "".join(
                [
                    sse({"type": "status", "node": "route_query", "content": "正在判断路由"}),
                    sse({"type": "status", "node": "retrieve", "content": "正在检索知识库"}),
                    sse({"type": "token", "content": answer}),
                    sse(
                        {
                            "type": "result",
                            "content": answer,
                            "citations": [
                                {
                                    "citation_id": "C1",
                                    "title": "费用报销制度",
                                    "source": "synthetic_seed",
                                    "filename": "finance-v2.md",
                                    "chunk_id": "finance-v2:0",
                                    "quote": "单笔超过5000元还需财务负责人复核。",
                                    "verification_status": "grounded",
                                }
                            ],
                            "trace_id": f"browser-trace-{call_number}",
                            "turn_id": f"browser-turn-{call_number}",
                            "failure_type": "none",
                        }
                    ),
                    sse({"type": "done", "trace_id": f"browser-trace-{call_number}"}),
                ]
            )
            route.fulfill(
                status=200,
                headers={"Content-Type": "text/event-stream"},
                body=body,
            )
            return
        if method == "POST" and path == "/knowledge/upload":
            state["uploaded"] = True
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps(
                    {"message": "文件已成功入库。", "record": uploaded_record},
                    ensure_ascii=False,
                ),
            )
            return
        if method == "GET" and path.startswith("/chat/history/"):
            route.fulfill(
                status=200,
                content_type="application/json",
                body=json.dumps({"items": []}),
            )
            return

        route.fulfill(status=404, content_type="application/json", body='{"detail":"not mocked"}')

    page.route("**/api/**", handle)


def run_mvp_browser_checks(playwright: Playwright) -> None:
    chrome_candidates = [
        Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
        Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
    ]
    executable = next((path for path in chrome_candidates if path.exists()), None)
    if executable is None:
        raise RuntimeError("No local Chrome or Edge executable was found.")

    browser = playwright.chromium.launch(
        headless=True,
        executable_path=str(executable),
    )
    context = browser.new_context(accept_downloads=True, viewport={"width": 1440, "height": 1000})
    page = context.new_page()
    console_errors: list[str] = []
    page_errors: list[str] = []
    failed_requests: list[str] = []
    page.on("console", lambda message: console_errors.append(message.text) if message.type == "error" else None)
    page.on("pageerror", lambda error: page_errors.append(str(error)))
    page.on("requestfailed", lambda request: failed_requests.append(f"{request.method} {request.url}"))
    install_api_fixtures(page)

    try:
        page.goto(f"{BASE_URL}/chat", wait_until="domcontentloaded")
        expect(page.get_by_text("知识服务在线")).to_be_visible()

        page.get_by_role("tab", name="通用模式").click()
        expect(page.get_by_role("button", name="联网搜索")).to_be_visible()
        page.get_by_role("button", name="联网搜索").click()
        expect(page.get_by_role("button", name="联网搜索")).to_have_class("search-toggle active")
        page.get_by_role("tab", name="知识库").click()

        question = page.get_by_label("向企业知识库提问")
        question.fill("差旅报销需要谁审批？")
        page.get_by_role("button", name="发送").click()
        expect(page.locator("article.message.assistant").last).to_contain_text("直属主管审批")
        expect(page.get_by_role("button", name="打开或隐藏引用证据")).to_contain_text("1")

        page.get_by_role("button", name="打开或隐藏引用证据").click()
        evidence = page.get_by_role("complementary", name="引用证据")
        expect(evidence).to_be_visible()
        expect(evidence).to_contain_text("单笔超过5000元")
        page.get_by_role("button", name="关闭引用证据").click()

        question.fill("超过5000元呢？")
        page.get_by_role("button", name="发送").click()
        expect(page.locator("article.message.assistant")).to_have_count(2)
        expect(page.locator("article.message.assistant").last).to_contain_text("财务负责人复核")

        page.get_by_role("button", name="删除会话：费用报销制度").click()
        expect(page.get_by_role("dialog", name="彻底删除这个会话？")).to_be_visible()
        page.get_by_role("button", name="确认删除").click()
        expect(page.get_by_text("让制度回答，有据可查。", exact=True)).to_be_visible()

        page.get_by_role("link", name="知识库").click()
        expect(page.get_by_role("heading", name="知识库", exact=True)).to_be_visible()
        file_input = page.locator("#knowledge-file")
        file_input.set_input_files(
            {
                "name": "travel-policy.md",
                "mimeType": "text/markdown",
                "buffer": "# 新差旅政策\n差旅申请需要直属主管审批。".encode("utf-8"),
            }
        )
        expect(page.get_by_role("button", name="上传文档")).to_be_enabled()
        page.get_by_role("button", name="上传文档").click()
        expect(page.get_by_role("heading", name="文件已完成入库", exact=True)).to_be_visible()
        expect(page.get_by_text("新差旅政策", exact=True)).to_be_visible()

        page.get_by_label("搜索文档").fill("新差旅政策")
        expect(page.get_by_text("显示 1 / 2 份文档", exact=True)).to_be_visible()
        page.get_by_role("button", name="清除筛选").click()
        expect(page.get_by_text("显示 2 / 2 份文档", exact=True)).to_be_visible()

        uploaded_row = page.locator("article.document-row").filter(has_text="新差旅政策")
        uploaded_row.get_by_role("button", name="查看详情").click()
        dialog = page.get_by_role("dialog", name="文档详情")
        expect(dialog).to_be_visible()
        expect(dialog).to_contain_text("新差旅政策")
        with page.expect_download() as download_info:
            dialog.get_by_role("button", name="下载原文件").click()
        download = download_info.value
        assert download.suggested_filename == "travel-policy.md"
        dialog.get_by_role("button", name="关闭文档详情").click()

        page.screenshot(path=str(ROOT / "work" / "mvp-e2e-knowledge.png"), full_page=True)
        if console_errors or page_errors or failed_requests:
            raise AssertionError(
                "Browser diagnostics reported errors: "
                + repr(
                    {
                        "console_errors": console_errors,
                        "page_errors": page_errors,
                        "failed_requests": failed_requests,
                    }
                )
            )
    finally:
        context.close()
        browser.close()


if __name__ == "__main__":
    with sync_playwright() as playwright:
        run_mvp_browser_checks(playwright)
    print("MVP browser acceptance passed")
