"""Viewport and interaction regressions for the knowledge workspace.

Uses the real Vue application with deterministic browser API fixtures. No model
calls, production uploads, or changes to the local knowledge store are made.
Run after starting Vite: python tests/e2e_workspace_design.py
"""

from __future__ import annotations

from pathlib import Path

from playwright.sync_api import Page, expect, sync_playwright

from e2e_mvp_browser import BASE_URL, ROOT, install_api_fixtures, sse


def assert_in_viewport(page: Page, selector: str) -> None:
    bounds = page.locator(selector).bounding_box()
    assert bounds is not None, f"{selector} is not rendered"
    viewport = page.viewport_size
    assert viewport is not None
    assert bounds["y"] >= 0 and bounds["y"] + bounds["height"] <= viewport["height"] + 1, (
        selector, bounds, viewport
    )
    assert bounds["x"] >= 0 and bounds["x"] + bounds["width"] <= viewport["width"] + 1


def assert_no_page_overflow(page: Page) -> None:
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), "horizontal overflow"


def check_viewports(browser) -> None:  # type: ignore[no-untyped-def]
    for width, height in [(1440, 900), (1366, 768), (1024, 768), (768, 1024), (390, 844), (375, 667)]:
        context = browser.new_context(viewport={"width": width, "height": height}, reduced_motion="reduce")
        page = context.new_page()
        failures: list[str] = []
        page.on("pageerror", lambda error: failures.append(str(error)))
        install_api_fixtures(page)
        try:
            page.goto(f"{BASE_URL}/chat", wait_until="domcontentloaded")
            expect(page.get_by_label("向企业知识库提问")).to_be_visible()
            expect(page.locator(".welcome-mark")).to_be_visible()
            assert_in_viewport(page, ".composer")
            assert page.locator(".conversation").evaluate("el => el.scrollTop") == 0
            assert_in_viewport(page, ".chat-empty h2")
            assert_no_page_overflow(page)
            assert page.evaluate("document.documentElement.scrollHeight <= innerHeight + 1")
            page.screenshot(path=str(ROOT / "work" / f"workspace-chat-{width}.png"))

            page.get_by_role("button", name="通用模式", exact=True).click()
            expect(page.get_by_role("button", name="联网搜索", exact=True)).to_be_visible()
            expect(page.get_by_text("写得更清晰", exact=True)).to_be_attached()
            expect(page.get_by_text("弄清审批流程", exact=True)).to_have_count(0)
            assert_in_viewport(page, ".composer")

            page.goto(f"{BASE_URL}/knowledge", wait_until="domcontentloaded")
            expect(page.locator(".document-row")).to_have_count(1)
            assert_in_viewport(page, ".document-filters")
            assert_in_viewport(page, ".document-row")
            assert_no_page_overflow(page)
            page.screenshot(path=str(ROOT / "work" / f"workspace-knowledge-{width}.png"))

            trigger = page.get_by_role("button", name="添加文档", exact=True)
            trigger.click()
            dialog = page.get_by_role("dialog", name="添加文档", exact=True)
            expect(dialog).to_be_visible()
            close = dialog.get_by_role("button", name="关闭添加文档")
            expect(close).to_be_focused()
            assert_in_viewport(page, ".workspace-drawer")
            # A collapsed optional form must not put hidden fields in the Tab loop.
            page.keyboard.press("Shift+Tab")
            expect(dialog.locator("summary")).to_be_focused()
            page.keyboard.press("Tab")
            expect(close).to_be_focused()
            dialog.locator("summary").click()
            expect(dialog.get_by_label("所属部门")).to_be_visible()
            expect(page.locator(".knowledge-header")).to_have_attribute("inert", "")
            page.keyboard.press("Escape")
            expect(dialog).to_have_count(0)
            expect(trigger).to_be_focused()
            assert not failures, failures
        finally:
            context.close()


def check_answer_interactions(browser) -> None:  # type: ignore[no-untyped-def]
    context = browser.new_context(viewport={"width": 1366, "height": 768}, reduced_motion="reduce")
    page = context.new_page()
    install_api_fixtures(page)
    diagnostics: list[str] = []
    page.on("pageerror", lambda error: diagnostics.append(str(error)))
    page.on("console", lambda message: diagnostics.append(message.text) if message.type == "error" else None)
    page.on("requestfailed", lambda request: diagnostics.append(request.url))
    page.add_init_script("window.__copiedAnswer = ''; Object.defineProperty(navigator, 'clipboard', { configurable: true, value: { writeText: async (text) => { window.__copiedAnswer = text; } } });")
    pending = []
    page.route("**/api/chat/stream", lambda route: pending.append(route))
    long_answer = "## 审批流程\n\n" + "\n\n".join(
        f"{index + 1}. 提交差旅申请，准备费用凭证，由直属主管审批。[C1]" for index in range(24)
    )

    def deliver(answer: str) -> None:
        route = pending.pop(0)
        route.fulfill(status=200, content_type="text/event-stream", body="".join([
            sse({"type": "status", "node": "commit_answer", "content": "完成", "node_status": "complete"}),
            sse({"type": "result", "content": answer, "citations": [
                {"citation_id": "C1", "title": "费用报销制度", "quote": "由直属主管审批。", "verification_status": "provenance_only"}
            ], "trace_id": "design-test", "failure_type": "none"}),
            sse({"type": "done", "trace_id": "design-test"}),
        ]))

    try:
        page.goto(f"{BASE_URL}/chat", wait_until="domcontentloaded")
        question = page.get_by_label("向企业知识库提问")
        question.fill("差旅报销需要谁审批？")
        page.get_by_role("button", name="发送", exact=True).click()
        expect(page.get_by_role("button", name="取消请求")).to_be_visible()
        assert pending
        deliver(long_answer)
        expect(page.get_by_role("button", name="复制回答", exact=True)).to_be_visible()
        page.get_by_role("button", name="复制回答", exact=True).click()
        expect(page.get_by_role("button", name="已复制回答")).to_be_visible()
        assert page.evaluate("window.__copiedAnswer") == long_answer
        assert_in_viewport(page, ".composer")
        assert_no_page_overflow(page)
        assert page.locator(".agent-progress").bounding_box()["height"] <= 50

        # Route navigation preserves the conversation and returns to its latest answer.
        page.get_by_role("link", name="知识库", exact=True).click()
        expect(page.locator(".library-panel")).to_be_visible()
        page.get_by_role("link", name="对话", exact=True).click()
        expect(page.locator("article.message.assistant")).to_have_count(1)
        assert page.locator(".conversation").evaluate("el => el.scrollHeight - el.scrollTop - el.clientHeight") < 10

        # The user scrolls up while the next answer is still pending.
        question.fill("超过5000元呢？")
        page.get_by_role("button", name="发送", exact=True).click()
        expect(page.get_by_role("button", name="取消请求")).to_be_visible()
        page.locator(".conversation").evaluate("el => { el.scrollTop = 0; el.dispatchEvent(new Event('scroll')); }")
        expect(page.get_by_role("button", name="回到最新回答")).to_be_visible()
        deliver("超过5000元还需要财务负责人复核。[C1]")
        expect(page.locator("article.message.assistant").last).to_contain_text("财务负责人")
        assert page.locator(".conversation").evaluate("el => el.scrollTop") < 10
        page.get_by_role("button", name="回到最新回答").click()
        expect(page.get_by_role("button", name="回到最新回答")).to_have_count(0)

        page.locator("article.message.assistant").last.get_by_role("button", name="查看引用 C1").click()
        evidence = page.get_by_role("complementary", name="引用证据")
        expect(evidence).to_be_visible()
        expect(evidence).to_contain_text("已核对引用出处")
        assert_in_viewport(page, ".composer")
        page.screenshot(path=str(ROOT / "work" / "workspace-answer-desktop.png"))
        page.set_viewport_size({"width": 390, "height": 844})
        evidence = page.get_by_role("dialog", name="引用证据")
        expect(evidence).to_be_visible()
        page.keyboard.press("Escape")
        expect(page.get_by_role("dialog", name="引用证据")).to_have_count(0)
        assert_in_viewport(page, ".composer")
        assert_no_page_overflow(page)
        for width, height in [(568, 320), (667, 375), (1200, 400)]:
            page.set_viewport_size({"width": width, "height": height})
            page.get_by_role("button", name="查看处理过程").click()
            expect(page.get_by_role("list", name="Agent 处理步骤")).to_be_visible()
            assert_in_viewport(page, ".composer")
            assert_in_viewport(page, ".step-list")
            assert page.locator(".conversation").bounding_box()["height"] >= 40
            page.get_by_role("button", name="收起处理过程").click()
        assert not diagnostics, diagnostics
    finally:
        context.close()


if __name__ == "__main__":
    (ROOT / "work").mkdir(exist_ok=True)
    with sync_playwright() as playwright:
        executable = next(path for path in [
            Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
            Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
        ] if path.exists())
        browser = playwright.chromium.launch(headless=True, executable_path=str(executable))
        try:
            check_viewports(browser)
            check_answer_interactions(browser)
        finally:
            browser.close()
    print("Workspace design checks passed: 6 viewports + 3 low-height layouts, upload focus, copy, scroll, evidence")
