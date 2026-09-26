"""Real browser zoom, emulated touch, and deterministic failure-boundary checks.

Requires Vite on 5173 and Playwright Chromium (extensions use its real tab zoom).
No model calls or production writes. Screenshots/diagnostics stay under work/.
"""

import json
import base64
import os
import tempfile
from pathlib import Path

from playwright.sync_api import expect, sync_playwright
from e2e_mvp_browser import BASE_URL, ROOT, install_api_fixtures, sse

OUT = ROOT / "work/product-experience/edge-cases"
OUT.mkdir(parents=True, exist_ok=True)


def assert_layout(page):
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    composer = (
        page.locator(".composer").bounding_box()
        if page.locator(".composer").count()
        else None
    )
    if composer:
        assert composer["y"] >= 0
        assert composer["y"] + composer["height"] <= page.evaluate("innerHeight") + 1


def check_zoom(p):
    with tempfile.TemporaryDirectory(dir=OUT) as directory:
        root = Path(directory)
        extension = root / "extension"
        extension.mkdir()
        (extension / "manifest.json").write_text(
            json.dumps(
                {
                    "manifest_version": 3,
                    "name": "Local acceptance zoom",
                    "version": "1.0",
                    "permissions": ["tabs"],
                    "background": {"service_worker": "background.js"},
                }
            )
        )
        (extension / "background.js").write_text(
            "chrome.tabs.onUpdated.addListener((id,info,tab)=>{"
            "if(info.status==='complete' && tab.url && "
            f"tab.url.startsWith('{BASE_URL}')) chrome.tabs.setZoom(id,2);"
            "});"
        )
        context = p.chromium.launch_persistent_context(
            str(root / "profile"),
            channel="chromium",
            executable_path=os.getenv("CHROMIUM_EXECUTABLE"),
            headless=True,
            no_viewport=True,
            args=[
                "--window-size=1440,900",
                "--force-device-scale-factor=1",
                f"--disable-extensions-except={extension}",
                f"--load-extension={extension}",
            ],
        )
        try:
            page = context.pages[0]
            install_api_fixtures(page)
            for route in ["/", "/chat", "/knowledge"]:
                page.goto(BASE_URL + route)
                if route == "/chat":
                    expect(page.locator("#question")).to_be_visible()
                elif route == "/knowledge":
                    expect(page.locator(".document-row")).to_have_count(1)
                else:
                    expect(page.get_by_role("heading", level=1)).to_be_visible()
                page.wait_for_function(
                    "devicePixelRatio === 2 && innerWidth < 800 && visualViewport.scale === 1"
                )
                assert_layout(page)
                shot = context.new_cdp_session(page).send("Page.captureScreenshot", {"format":"png"})
                (OUT / ("zoom-200-" + (route.strip("/") or "home") + ".png")).write_bytes(base64.b64decode(shot["data"]))
                if route == "/knowledge":
                    page.get_by_role("button", name="添加文档", exact=True).click()
                    dialog = page.get_by_role("dialog", name="添加文档", exact=True)
                    expect(dialog).to_be_visible()
                    page.keyboard.press("Escape")
                    expect(dialog).to_have_count(0)
            return page.evaluate(
                "({dpr:devicePixelRatio,width:innerWidth,height:innerHeight,scale:visualViewport.scale})"
            )
        finally:
            context.close()


def check_touch(browser, motion):
    context = browser.new_context(
        viewport={"width": 390, "height": 844},
        has_touch=True,
        is_mobile=True,
        reduced_motion=motion,
    )
    try:
        page = context.new_page()
        install_api_fixtures(page)
        page.goto(BASE_URL + "/chat")
        page.get_by_role("button", name="打开导航菜单").tap()
        page.get_by_role("link", name="知识管理", exact=True).tap()
        for _ in range(3):
            page.get_by_role("button", name="添加文档", exact=True).tap()
            expect(
                page.get_by_role("dialog", name="添加文档", exact=True)
            ).to_be_visible()
            page.get_by_role("button", name="关闭添加文档").tap()
        page.get_by_role("link", name="返回问答").tap()
        page.locator("#question").fill("差旅审批")
        page.route(
            "**/api/chat/stream",
            lambda route: route.fulfill(
                content_type="text/event-stream",
                body=sse(
                    {
                        "type": "result",
                        "content": "差旅需要审批 [C1]。",
                        "failure_type": "none",
                        "citations": [
                            {
                                "citation_id": "C1",
                                "source_id": "finance-v2",
                                "title": "费用报销制度",
                                "quote": "需要审批",
                            }
                        ],
                    }
                )
                + sse({"type": "done"}),
            ),
        )
        page.get_by_role("button", name="发送", exact=True).tap()
        trigger = page.get_by_role("button", name="查看引用 C1")
        trigger.tap()
        source = page.get_by_role("button", name="查看文档信息与下载")
        source.tap()
        expect(page.get_by_role("dialog", name="文档详情", exact=True)).to_be_visible()
        expect(page.locator(".chat-surface")).to_have_attribute("inert", "")
        page.route(
            "**/api/knowledge/records/*/download",
            lambda route: route.fulfill(status=403, json={"detail": "Forbidden"}),
        )
        page.get_by_role("button", name="下载原文件").tap()
        expect(page.get_by_text("无权下载该来源。", exact=True)).to_be_visible()
        page.screenshot(path=str(OUT / f"touch-source-{motion}.png"))
        page.get_by_role("button", name="关闭文档详情").tap()
        expect(source).to_be_focused()
        page.get_by_role("button", name="关闭引用证据", exact=True).tap()
        expect(trigger).to_be_focused()
        assert_layout(page)
    finally:
        context.close()


def check_failures(browser, width, motion):
    context = browser.new_context(
        viewport={"width": width, "height": 844}, reduced_motion=motion
    )
    diagnostics = {"page_errors": [], "unexpected_console": [], "request_failures": []}
    try:
        page = context.new_page()
        install_api_fixtures(page)
        page.on("pageerror", lambda e: diagnostics["page_errors"].append(str(e)))
        page.on(
            "console",
            lambda m: (
                diagnostics["unexpected_console"].append(m.text)
                if m.type == "error"
                and not (
                    m.location.get("url", "").endswith("/api/chat/stream")
                    and any(f"status of {code}" in m.text for code in [401, 403, 500])
                )
                else None
            ),
        )
        page.on(
            "requestfailed",
            lambda r: diagnostics["request_failures"].append(
                {"url": r.url, "reason": r.failure}
            ),
        )
        for scenario in [401, 403, 500, "no-result", "result-only", "cancel"]:
            page.goto(BASE_URL + "/chat")
            expect(page.locator("#question")).to_be_visible()
            page.unroute("**/api/chat/stream")
            calls, held = [], []

            def respond(route):
                calls.append(route.request.post_data_json)
                if len(calls) > 1:
                    route.fulfill(
                        content_type="text/event-stream",
                        body=sse(
                            {
                                "type": "result",
                                "content": "重试已完成",
                                "citations": [],
                                "failure_type": "none",
                            }
                        )
                        + sse({"type": "done"}),
                    )
                elif isinstance(scenario, int):
                    route.fulfill(
                        status=scenario, json={"detail": "Expected fixture failure"}
                    )
                elif scenario == "cancel":
                    held.append(route)
                else:
                    body = sse({"type": "token", "content": "不可展示的候选"})
                    if scenario == "result-only":
                        body += sse(
                            {
                                "type": "result",
                                "content": "已提交的权威答案",
                                "citations": [],
                                "failure_type": "none",
                            }
                        )
                    route.fulfill(content_type="text/event-stream", body=body)

            page.route("**/api/chat/stream", respond)
            page.locator("#question").fill("测试边界")
            page.get_by_role("button", name="发送", exact=True).click()
            if scenario == "cancel":
                expect(page.get_by_role("button", name="取消请求")).to_be_visible()
                page.get_by_role("button", name="取消请求").click()
                for route in held:
                    route.abort("aborted")
            if scenario == "cancel":
                expect(page.locator(".message.assistant")).to_contain_text(
                    "本次回答已取消"
                )
                page.locator("#question").fill("取消后重新提问")
                page.get_by_role("button", name="发送", exact=True).click()
                expect(page.locator(".message.assistant").last).to_contain_text(
                    "重试已完成"
                )
                assert calls[0]["turn_id"] != calls[1]["turn_id"]
            elif scenario == "result-only":
                expect(page.locator(".message.assistant")).to_contain_text(
                    "已提交的权威答案"
                )
                expect(page.locator(".failure-notice")).to_have_count(0)
            else:
                expect(
                    page.get_by_role("button", name="重新发送", exact=True)
                ).to_be_visible()
                page.get_by_role("button", name="重新发送", exact=True).click()
                expect(page.locator(".message.assistant").last).to_contain_text(
                    "重试已完成"
                )
                assert calls[0]["turn_id"] != calls[1]["turn_id"]
            expect(page.get_by_text("不可展示的候选", exact=True)).to_have_count(0)
            expect(page.get_by_role("button", name="取消请求")).to_have_count(0)
            assert_layout(page)
        assert not diagnostics["page_errors"], diagnostics
        assert not diagnostics["unexpected_console"], diagnostics
        assert all(
            "ERR_ABORTED" in item["reason"] and item["url"].endswith("/api/chat/stream")
            for item in diagnostics["request_failures"]
        ), diagnostics
        return diagnostics
    finally:
        context.close()


if __name__ == "__main__":
    with sync_playwright() as p:
        report = {"zoom": check_zoom(p), "failures": []}
        browser = p.chromium.launch(executable_path=os.getenv("CHROMIUM_EXECUTABLE"))
        try:
            report["browser"] = browser.version
            for motion in ["reduce", "no-preference"]:
                check_touch(browser, motion)
                for width in [390, 1440]:
                    report["failures"].append(
                        {
                            "width": width,
                            "motion": motion,
                            **check_failures(browser, width, motion),
                        }
                    )
        finally:
            browser.close()
    (OUT / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(
        "Passed: real 200% tab zoom, emulated touch/nested dialogs, 24 error/stream scenarios."
    )
