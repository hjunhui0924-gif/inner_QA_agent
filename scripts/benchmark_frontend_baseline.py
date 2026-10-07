"""Collect deterministic browser performance samples for the chat workspace.

The API boundary is synthetic so the report measures Vue rendering, Markdown,
scrolling and evidence-panel work without model or network variance. Run with
the Vite dev server on port 5173:

    python scripts/benchmark_frontend_baseline.py
"""

from __future__ import annotations

import json
import platform
import sys
import time
from pathlib import Path
from typing import Any

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
BASE_URL = "http://127.0.0.1:5173"
OUTPUT = ROOT / "work" / "product-experience" / "performance" / "baseline-20261007"
SAMPLES = ((2, 4), (20, 40), (100, 200), (300, 600), (1000, 1000))

METRICS_INIT = r"""
(() => {
  window.baselineMetrics = { longTasks: [] };
  new PerformanceObserver(list => {
    for (const entry of list.getEntries()) {
      window.baselineMetrics.longTasks.push({ start: entry.startTime, duration: entry.duration });
    }
  }).observe({ type: 'longtask', buffered: true });
})()
"""

STREAM_INIT = r"""
(() => {
  window.baselineStreams = [];
  const originalFetch = window.fetch.bind(window);
  window.fetch = async (input, init = {}) => {
    if (!String(input).endsWith('/api/chat/stream')) return originalFetch(input, init);
    const stream = { closed: false, aborted: false };
    window.baselineStreams.push(stream);
    const body = new ReadableStream({
      start(controller) {
        stream.push = event => controller.enqueue(new TextEncoder().encode(
          'data: ' + JSON.stringify(event) + '\n\n'
        ));
        stream.close = () => { if (!stream.closed) { stream.closed = true; controller.close(); } };
        init.signal?.addEventListener('abort', () => {
          stream.aborted = true;
          stream.close();
          controller.error(new DOMException('Cancelled', 'AbortError'));
        }, { once: true });
      },
      cancel() { stream.closed = true; },
    });
    return new Response(body, { headers: { 'Content-Type': 'text/event-stream' } });
  };
})()
"""


def make_history(rounds: int, messages: int | None = None) -> list[dict[str, Any]]:
    target = messages or rounds * 2
    result: list[dict[str, Any]] = []
    for index in range(target):
        role = "user" if index % 2 == 0 else "assistant"
        turn = index // 2 + 1
        item: dict[str, Any] = {
            "role": role,
            "content": (
                f"第 {turn} 个性能问题：请说明审批流程和需要保留的材料。"
                if role == "user"
                else "审批流程需要直属主管审批，材料应包含申请单、发票和审批记录。 [C1]"
            ),
            "message_id": f"baseline-{index}:message",
            "turn_id": f"baseline-{turn}:turn",
            "mode": "general",
        }
        if role == "assistant":
            item["citations"] = [{
                "citation_id": "C1",
                "title": "合成审批制度",
                "quote": "审批流程需要直属主管审批。",
            }]
        result.append(item)
    return result


def install_api_fixtures(page, histories: dict[str, list[dict[str, Any]]]) -> None:  # type: ignore[no-untyped-def]
    sessions = [
        {
            "session_id": session_id,
            "title": session_id,
            "created_at": "",
            "updated_at": "",
            "last_message": "",
        }
        for session_id in histories
    ]

    def handle(route) -> None:  # type: ignore[no-untyped-def]
        request = route.request
        path = request.url.split("/api", 1)[-1].split("?", 1)[0]
        if request.method == "GET" and path == "/health":
            route.fulfill(json={"status": "ok"})
        elif request.method == "GET" and path == "/chat/sessions/user_001":
            route.fulfill(json={"items": sessions})
        elif request.method == "GET" and path.startswith("/chat/history/"):
            session_id = path.rsplit("/", 1)[-1]
            route.fulfill(json={"items": histories.get(session_id, [])})
        elif request.method == "GET" and path.startswith("/chat/context/"):
            route.fulfill(json={
                "context_usage": {
                    "scope": "conversation_history",
                    "estimation_method": "synthetic",
                    "estimated": True,
                    "status": "available",
                    "used_tokens": 120,
                    "summary_tokens": 0,
                    "budget_tokens": 400,
                    "summary_trigger_tokens": 300,
                    "sampled_at": "2026-10-07T00:00:00Z",
                    "checkpoint_id": None,
                }
            })
        else:
            route.fulfill(status=404, json={"detail": "not mocked"})

    page.route("**/api/**", handle)


def push(page, event: dict[str, Any]) -> None:
    page.evaluate("event => window.baselineStreams.at(-1).push(event)", event)


def stream_sample(page) -> None:  # type: ignore[no-untyped-def]
    page.locator("#question").fill("流式性能基线问题")
    page.get_by_role("button", name="发送", exact=True).click()
    page.wait_for_function("window.baselineStreams.length > 0")
    push(page, {"type": "status", "node": "generate", "content": "生成中"})
    for index in range(120):
        push(page, {
            "type": "preview_delta",
            "generation_id": "baseline-generation",
            "content": f"第 {index + 1} 段流式草稿。\n\n",
        })
        page.wait_for_timeout(5)
    push(page, {
        "type": "result",
        "content": "基线正式答案：审批材料需要申请单、发票和审批记录。",
        "citations": [],
        "failure_type": "none",
        "trace_id": "baseline-trace",
    })
    push(page, {"type": "done", "trace_id": "baseline-trace"})
    expect(page.locator(".message.assistant").last).to_contain_text("基线正式答案")
    page.wait_for_timeout(120)


def scroll_sample(page) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    return page.locator(".conversation").evaluate(
        """async el => {
          const started = performance.now();
          let frames = 0;
          const distance = Math.max(0, el.scrollHeight - el.clientHeight);
          for (let i = 0; i <= 120; i++) {
            el.scrollTop = distance * (i / 120);
            el.dispatchEvent(new Event('scroll'));
            await new Promise(requestAnimationFrame);
            frames++;
          }
          return {
            startedAt: started,
            durationMs: performance.now() - started,
            endedAt: performance.now(),
            frames,
            scrollHeight: el.scrollHeight,
            clientHeight: el.clientHeight,
          };
        }"""
    )


def collect_sample(browser, session_id: str, messages: list[dict[str, Any]]) -> dict[str, Any]:
    context = browser.new_context(viewport={"width": 1440, "height": 900}, reduced_motion="reduce")
    page = context.new_page()
    console_errors: list[str] = []
    page_errors: list[str] = []
    failed_requests: list[str] = []
    page.on("console", lambda message: console_errors.append(message.text) if message.type == "error" else None)
    page.on("pageerror", lambda error: page_errors.append(str(error)))
    page.on("requestfailed", lambda request: failed_requests.append(f"{request.method} {request.url}"))
    page.add_init_script(METRICS_INIT)
    page.add_init_script(STREAM_INIT)
    install_api_fixtures(page, {session_id: messages})
    try:
        navigation_started = time.perf_counter()
        page.goto(f"{BASE_URL}/chat", wait_until="domcontentloaded")
        expect(page.locator("#question")).to_be_visible()
        page.get_by_role("button", name=session_id, exact=True).click()
        expect(page.locator(".message")).to_have_count(len(messages), timeout=30000)
        history_ms = (time.perf_counter() - navigation_started) * 1000

        before_stream = page.evaluate("window.workspacePerformance.snapshot()")
        scroll = scroll_sample(page)
        scroll_metrics = page.evaluate("window.baselineMetrics")
        scroll_long_tasks = [
            task for task in scroll_metrics.get("longTasks", [])
            if task["start"] < scroll["endedAt"]
            and task["start"] + task["duration"] > scroll["startedAt"]
        ]

        evidence_started = time.perf_counter()
        page.locator(".citation-row button").first.click()
        expect(page.locator(".evidence-panel.open")).to_be_visible()
        evidence_ms = (time.perf_counter() - evidence_started) * 1000
        scroll_before_close = page.locator(".conversation").evaluate("el => el.scrollTop")
        page.get_by_role("button", name="关闭引用证据", exact=True).click()
        page.wait_for_timeout(50)
        scroll_after_close = page.locator(".conversation").evaluate("el => el.scrollTop")

        stream_sample(page)
        snapshot = page.evaluate("window.workspacePerformance.snapshot()")
        local = page.evaluate("window.baselineMetrics")
        navigation = page.evaluate(
            """() => {
              const entry = performance.getEntriesByType('navigation')[0];
              return entry ? { domContentLoadedMs: entry.domContentLoadedEventEnd, loadMs: entry.loadEventEnd } : {};
            }"""
        )
        turns = snapshot.get("turns", [])
        turn = turns[-1] if turns else {}
        fps = scroll["frames"] / (scroll["durationMs"] / 1000) if scroll["durationMs"] else 0
        return {
            "sessionId": session_id,
            "rounds": len(messages) // 2,
            "messageCount": len(messages),
            "historyLoadMs": round(history_ms, 2),
            "navigation": navigation,
            "scroll": {**scroll, "fps": round(fps, 2)},
            "scrollLongTasks": scroll_long_tasks,
            "evidenceOpenMs": round(evidence_ms, 2),
            "scrollTopBeforeEvidenceClose": round(scroll_before_close, 2),
            "scrollTopAfterEvidenceClose": round(scroll_after_close, 2),
            "turn": turn,
            "diagnostics": snapshot.get("diagnostics", {}),
            "browserLongTasks": local.get("longTasks", []),
            "messageDomCount": page.locator(".message").count(),
            "browserErrors": {
                "console": console_errors,
                "page": page_errors,
                "failedRequests": failed_requests,
            },
        }
    finally:
        context.close()


def main() -> int:
    histories = {
        f"baseline-{rounds}" if rounds < 1000 else "baseline-1000-messages": make_history(rounds, messages)
        for rounds, messages in SAMPLES
    }
    OUTPUT.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {
        "generatedAt": "2026-10-07",
        "browser": {},
        "environment": {
            "os": platform.platform(),
            "cpu": platform.processor(),
            "network": "localhost synthetic API, no throttling",
            "viewport": [1440, 900],
            "reducedMotion": True,
            "streamPreviewDeltas": 120,
        },
        "samples": [],
        "thresholds": {
            "scrollFpsBelow": 50,
            "longTaskMsAbove": 50,
            "messageCountForVirtualization": 1000,
        },
    }
    with sync_playwright() as playwright:
        executable = Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")
        browser = playwright.chromium.launch(headless=True, executable_path=str(executable))
        report["browser"] = {"name": "Chrome", "version": browser.version}
        try:
            for session_id, messages in histories.items():
                print(f"START {session_id}", flush=True)
                row = collect_sample(browser, session_id, messages)
                report["samples"].append(row)
                (OUTPUT / "report.json").write_text(
                    json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                print(
                    f"DONE {session_id}: {row['messageCount']} messages, "
                    f"{row['scroll']['fps']}fps, {len(row['browserLongTasks'])} long tasks",
                    flush=True,
                )
        finally:
            browser.close()
    print(f"Saved {OUTPUT / 'report.json'}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
