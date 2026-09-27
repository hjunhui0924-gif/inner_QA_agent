"""Product experience regressions with deterministic browser-boundary fixtures."""

import json
from pathlib import Path
from playwright.sync_api import sync_playwright, expect
from e2e_mvp_browser import install_api_fixtures, sse, ROOT, BASE_URL

OUT = ROOT / "work/product-experience/final"
OUT.mkdir(parents=True, exist_ok=True)
VIEWPORTS = [
    (1440, 900),
    (1366, 768),
    (1024, 768),
    (768, 1024),
    (390, 844),
    (375, 667),
    (667, 375),
]


def check_home(browser):
    for motion in ["reduce", "no-preference"]:
        for w, h in VIEWPORTS:
            context = browser.new_context(
                viewport={"width": w, "height": h}, reduced_motion=motion
            )
            page = context.new_page()
            requests = []
            errors = []
            page.on(
                "request",
                lambda request: (
                    requests.append(request.url) if "/api/" in request.url else None
                ),
            )
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.goto(BASE_URL, wait_until="networkidle")
            expect(page.get_by_role("heading", level=1)).to_contain_text("有据可查")
            assert requests == [], requests
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            page.get_by_role("button", name="查看示例来源").click()
            expect(page.locator("#sample-source")).to_contain_text(
                "直属主管审批后，再由财务负责人复核"
            )
            page.screenshot(
                path=str(OUT / f"home-{w}-{h}-{motion}.png"), full_page=True
            )
            assert not errors, errors
            context.close()


def check_task_state(browser):
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    page = context.new_page()
    install_api_fixtures(page)
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    calls = []
    page.on(
        "request",
        lambda request: calls.append(request.url) if "/api/" in request.url else None,
    )
    page.goto(BASE_URL + "/chat")
    question = page.locator("#question")
    expect(question).to_be_visible()
    expect(page.locator(".citation-row button")).to_have_count(0)
    assert not any("/knowledge/" in url for url in calls)
    question.fill("保留草稿")
    page.get_by_role("link", name="知识管理", exact=True).click()
    expect(page.locator(".document-row")).to_have_count(1)
    page.get_by_role("link", name="返回问答").click()
    expect(question).to_have_value("保留草稿")
    # Composition does not submit; Ctrl+Enter after composition does.
    question.dispatch_event("compositionstart")
    question.press("Control+Enter")
    expect(page.locator(".message.user")).to_have_count(0)
    question.dispatch_event("compositionend", {"data": "稿"})
    question.press("Control+Enter")
    expect(page.locator(".message.assistant .message-content")).to_contain_text(
        "直属主管"
    )
    first = page.locator(".message.assistant").first
    trigger = first.get_by_role("button", name="查看参考来源 1")
    trigger.click()
    expect(page.get_by_role("complementary", name="引用来源")).to_be_visible()
    old_quote = page.locator(".evidence-card blockquote").inner_text()
    pending = []
    page.route("**/api/chat/stream", lambda route: pending.append(route))
    question.fill("第二问")
    page.get_by_role("button", name="发送", exact=True).click()
    expect(page.get_by_role("button", name="取消请求")).to_be_visible()
    expect(page.locator(".evidence-card blockquote")).to_have_text(old_quote)
    # Move away during an active stream; it must finish in workspace state.
    page.get_by_role("link", name="知识管理", exact=True).click()
    pending.pop().fulfill(
        status=200,
        content_type="text/event-stream",
        body=sse(
            {
                "type": "result",
                "content": "新的回答 [C1]",
                "citations": [
                    {"citation_id": "C1", "title": "第二份原文", "quote": "第二份原文"}
                ],
                "failure_type": "none",
                "trace_id": "new",
            }
        )
        + sse({"type": "done"}),
    )
    page.get_by_role("link", name="返回问答").click()
    expect(page.locator(".message.assistant").last).to_contain_text("新的回答")
    expect(page.locator(".evidence-panel.open")).to_have_count(0)
    # Repeated C1 identities resolve independently and restore exact trigger focus.
    trigger.click()
    expect(page.locator(".evidence-card blockquote")).to_have_text(old_quote)
    page.get_by_role("button", name="关闭引用证据").click()
    expect(trigger).to_be_focused()
    page.locator(".message.assistant").last.get_by_role(
        "button", name="查看参考来源 1"
    ).click()
    expect(page.locator(".evidence-card blockquote")).to_have_text("第二份原文")
    page.keyboard.press("Escape")
    # Slow sessions must not prevent another turn.
    held = []
    page.route("**/api/chat/sessions/**", lambda route: held.append(route))
    question.fill("第三问")
    page.get_by_role("button", name="发送", exact=True).click()
    expect(page.get_by_role("button", name="取消请求")).to_be_visible()
    pending.pop().fulfill(
        status=200,
        content_type="text/event-stream",
        body=sse(
            {
                "type": "result",
                "content": "第三条完整答案",
                "citations": [],
                "failure_type": "none",
                "trace_id": "third",
            }
        )
        + sse({"type": "done"}),
    )
    expect(question).to_be_enabled()
    question.fill("可以再问")
    expect(page.get_by_role("button", name="发送", exact=True)).to_be_enabled()
    assert held
    for route in held:
        route.fulfill(json={"items": []})
    page.unroute("**/api/chat/sessions/**")
    page.wait_for_timeout(100)
    (OUT / "browser-timings.json").write_text(
        json.dumps(page.evaluate("window.workspacePerformance.snapshot()"), indent=2),
        encoding="utf-8",
    )
    page.screenshot(path=str(OUT / "chat-multiturn.png"))
    # Preserve an explicit reading position and draft through route navigation.
    page.locator(".conversation").evaluate(
        "el=>{el.scrollTop=0;el.dispatchEvent(new Event('scroll'))}"
    )
    page.get_by_role("link", name="知识管理", exact=True).click()
    page.get_by_role("link", name="返回问答").click()
    expect(question).to_have_value("可以再问")
    assert page.locator(".conversation").evaluate("el=>el.scrollTop") == 0
    assert not errors, errors
    context.close()


def check_upload_and_large_list(browser):
    context = browser.new_context(viewport={"width": 390, "height": 844})
    page = context.new_page()
    install_api_fixtures(page)
    records = [
        {
            "source_id": f"doc-{i}",
            "title": f"制度文档 {i:03d} 长标题验证",
            "source": "policy",
            "department": "Finance",
            "status": "active",
            "version": "v1",
        }
        for i in range(100)
    ]
    page.route(
        "**/api/knowledge/records", lambda route: route.fulfill(json={"items": records})
    )
    page.goto(BASE_URL + "/knowledge")
    expect(page.locator(".document-row")).to_have_count(10)
    expect(page.get_by_label("每页文档数量")).to_have_count(0)
    expect(page.locator(".library-result-count")).to_contain_text("共 100 份")
    for _ in range(9):
        page.get_by_role("button", name="下一页", exact=True).click()
    expect(page.locator(".document-row").first).to_contain_text("090")
    expect(page.get_by_role("button", name="下一页", exact=True)).to_be_disabled()
    page.get_by_label("搜索文档", exact=True).fill("099")
    expect(page.locator(".document-row")).to_have_count(1)
    expect(page.get_by_role("button", name="上一页", exact=True)).to_be_disabled()
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    page.screenshot(path=str(OUT / "knowledge-mobile.png"))
    pending = []
    page.route("**/api/knowledge/upload", lambda route: pending.append(route))
    page.get_by_role("button", name="添加文档", exact=True).click()
    page.locator("#knowledge-file").set_input_files(
        {"name": "retained.md", "mimeType": "text/markdown", "buffer": b"policy"}
    )
    page.get_by_role("button", name="上传文档", exact=True).click()
    expect(page.get_by_role("button", name="正在上传并处理文档")).to_be_disabled()
    page.get_by_role("button", name="关闭添加文档").click()
    page.get_by_role("link", name="返回问答").click()
    page.get_by_role("button", name="打开导航菜单").click()
    page.get_by_role("link", name="知识管理", exact=True).click()
    page.get_by_role("button", name="添加文档", exact=True).click()
    expect(page.get_by_role("button", name="正在上传并处理文档")).to_be_disabled()
    pending.pop().fulfill(
        json={
            "message": "检测到完全重复内容，已跳过入库。",
            "record": {
                "title": records[0]["title"],
                "source": "policy",
                "original_filename": "retained.md",
                "deduplicated": True,
            },
        }
    )
    expect(
        page.get_by_role("heading", name="检测到重复文件，未重复入库")
    ).to_be_visible()
    page.screenshot(path=str(OUT / "upload-mobile.png"))
    page.get_by_role("button", name="关闭添加文档").click()
    expect(page.locator(".document-row")).to_have_count(10)
    context.close()


def check_history_scroll(browser):
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    page = context.new_page()
    install_api_fixtures(page)
    page.route(
        "**/api/chat/sessions/**",
        lambda route: route.fulfill(
            json={
                "items": [
                    {"session_id": sid, "title": sid, "last_message": ""}
                    for sid in ["long-session", "short-session"]
                ]
            }
        ),
    )

    def history(route):
        long = route.request.url.endswith("long-session")
        route.fulfill(
            json={
                "items": [
                    {"role": "user", "content": "问题", "message_id": "user"},
                    {
                        "role": "assistant",
                        "content": ("长答案段落\n\n" * 100 if long else "短回答"),
                        "message_id": "answer",
                        "citations": [],
                    },
                ]
            }
        )

    page.route("**/api/chat/history/**", history)
    page.goto(BASE_URL + "/chat")
    page.get_by_role("button", name="long-session", exact=True).click()
    expect(page.locator(".message.assistant")).to_be_visible()
    page.locator(".conversation").evaluate(
        "el=>{el.scrollTop=600;el.dispatchEvent(new Event('scroll'))}"
    )
    page.locator("#question").fill("长会话草稿")
    page.get_by_role("button", name="short-session", exact=True).click()
    expect(page.locator(".message.assistant")).to_contain_text("短回答")
    page.get_by_role("button", name="long-session", exact=True).click()
    expect(page.locator("#question")).to_have_value("长会话草稿")
    page.wait_for_timeout(100)
    assert abs(page.locator(".conversation").evaluate("el=>el.scrollTop") - 600) < 2
    context.close()


if __name__ == "__main__":
    with sync_playwright() as p:
        executable = next(
            path
            for path in [
                Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
                Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
            ]
            if path.exists()
        )
        browser = p.chromium.launch(headless=True, executable_path=str(executable))
        try:
            check_home(browser)
            check_task_state(browser)
            check_upload_and_large_list(browser)
            check_history_scroll(browser)
        finally:
            browser.close()
    print(
        "Product experience checks passed: 14 home layouts, request isolation, IME, evidence identity/focus, draft/scroll, active stream navigation, slow sessions, upload navigation and 100-document filter."
    )
