"""Reproducible local UI timing; home production preview, chat API fixtures."""

import json, platform, time
from pathlib import Path
from playwright.sync_api import sync_playwright, expect
from e2e_mvp_browser import install_api_fixtures, sse, ROOT

OUT = ROOT / "work/product-experience/performance"
OUT.mkdir(parents=True, exist_ok=True)
INIT = """window.localMetrics={lcp:0,cls:0,longTasks:[]};new PerformanceObserver(l=>{for(const e of l.getEntries())window.localMetrics.lcp=e.startTime}).observe({type:'largest-contentful-paint',buffered:true});new PerformanceObserver(l=>{for(const e of l.getEntries())if(!e.hadRecentInput)window.localMetrics.cls+=e.value}).observe({type:'layout-shift',buffered:true});new PerformanceObserver(l=>{for(const e of l.getEntries())window.localMetrics.longTasks.push({start:e.startTime,duration:e.duration})}).observe({type:'longtask',buffered:true});"""
with sync_playwright() as p:
    browser = p.chromium.launch(
        headless=True,
        executable_path=r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    )
    report = {
        "os": platform.platform(),
        "cpu": platform.processor(),
        "browser": browser.version,
        "network": "localhost, no network or CPU throttling",
        "viewport": [1440, 900],
        "home": [],
    }
    for _ in range(5):
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()
        page.add_init_script(INIT)
        cdp = context.new_cdp_session(page)
        cdp.send("Network.enable")
        cdp.send("Network.setCacheDisabled", {"cacheDisabled": True})
        page.goto("http://127.0.0.1:4173", wait_until="networkidle")
        page.wait_for_timeout(500)
        report["home"].append(page.evaluate("window.localMetrics"))
        context.close()
    context = browser.new_context(viewport={"width": 1440, "height": 900})
    page = context.new_page()
    install_api_fixtures(page)
    page.add_init_script(INIT)
    cdp = context.new_cdp_session(page)
    trace = []
    cdp.on("Tracing.dataCollected", lambda event: trace.extend(event["value"]))
    cdp.send(
        "Tracing.start",
        {
            "categories": "devtools.timeline,blink.user_timing",
            "transferMode": "ReportEvents",
        },
    )
    page.goto("http://127.0.0.1:5173/chat")
    expect(page.locator("#question")).to_be_visible()
    held = []
    page.route("**/api/chat/sessions/**", lambda route: held.append(route))
    answer = "## 固定长度答案\n\n" + "审批条件与原文依据需要逐项核对。\n\n" * 100
    page.route(
        "**/api/chat/stream",
        lambda route: route.fulfill(
            content_type="text/event-stream",
            body=sse({"type": "status", "node": "generate", "content": "整理回答"})
            + sse(
                {
                    "type": "result",
                    "content": answer,
                    "citations": [],
                    "failure_type": "none",
                    "trace_id": "timing",
                }
            )
            + sse({"type": "done"}),
        ),
    )
    for i in range(20):
        page.locator("#question").fill(f"性能样本{i+1}")
        page.get_by_role("button", name="发送", exact=True).click()
        expect(page.locator("#question")).to_be_enabled()
        page.wait_for_timeout(100)
    # Keep the earliest sessions requests pending at least 5 seconds across turns.
    page.wait_for_timeout(5000)
    for route in held:
        route.fulfill(json={"items": []})
    report["chat"] = page.evaluate("window.workspacePerformance.snapshot()")
    report["chat_long_tasks"] = page.evaluate("window.localMetrics.longTasks")
    cdp.send("Tracing.end")
    page.wait_for_timeout(500)
    (OUT / "browser-trace.json").write_text(
        json.dumps({"traceEvents": trace}), encoding="utf-8"
    )
    page.unroute("**/api/chat/sessions/**")
    for path in ["/chat", "/knowledge"]:
        page.goto("http://127.0.0.1:4173" + path)
        page.reload()
        expect(page.locator("#main-content")).to_be_visible()
    context.close()
    browser.close()
    (OUT / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(
        "Saved 5 cold home samples, 20 fixed-answer UI samples, Chromium timeline and SPA refresh checks."
    )
