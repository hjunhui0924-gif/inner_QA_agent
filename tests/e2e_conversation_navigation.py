"""M5 browser acceptance with synthetic history and controllable SSE chunks.

Run against Vite: python tests/e2e_conversation_navigation.py
No live model calls or everyday conversation data are used.
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

from e2e_mvp_browser import BASE_URL, ROOT, install_api_fixtures


LONG_QUESTION = "请说明跨部门差旅报销的审批条件、所需材料和提交时限。" * 8 + "最后一个子问题🙂"


def history_pair(turn: str, question: str, *, group: str | None = None, failed: bool = False):
    shared = {"turn_id": turn, "mode": "general", "attempt_group_id": group}
    return [
        {**shared, "role": "user", "content": question, "message_id": f"{turn}:user"},
        {**shared, "role": "assistant", "content": "可核对的制度说明。\n\n" * 24,
         "message_id": f"{turn}:assistant", "failure_type": "generation_error" if failed else "none",
         "citations": [] if failed else [{"citation_id": "C1", "title": "合成差旅制度", "quote": "审批说明"}]},
    ]


HISTORIES = {f"nav-{count}": [item for index in range(count)
    for item in history_pair(f"{count}-{index}", LONG_QUESTION if index == 0 else f"第 {index + 1} 个工作问题")]
    for count in (2, 20, 100)}
HISTORIES["nav-retry"] = (
    history_pair("original", "审批问题", group="original", failed=True)
    + history_pair("unrelated", "审批问题")
    + history_pair("retry-1", "审批问题", group="original", failed=True)
    + history_pair("retry-2", "审批问题", group="original", failed=True)
)
HISTORIES["nav-empty"] = []

STREAM_INIT = """(() => {
  const originalFetch = window.fetch.bind(window);
  window.__navigationRequests = [];
  window.fetch = async (input, init) => {
    if (!String(input).endsWith('/chat/stream')) return originalFetch(input, init);
    window.__navigationRequests.push(JSON.parse(init.body));
    return new Response(new ReadableStream({ start(controller) {
      window.__navigationStream = {
        push(event) { controller.enqueue(new TextEncoder().encode('data: ' + JSON.stringify(event) + '\\n\\n')); },
        close() { controller.close(); }
      };
    }}), { headers: { 'Content-Type': 'text/event-stream' } });
  };
  window.__navigationScrolls = [];
  const scrollTo = Element.prototype.scrollTo;
  Element.prototype.scrollTo = function(options, ...rest) {
    if (this.classList.contains('conversation')) window.__navigationScrolls.push(options);
    return scrollTo.call(this, options, ...rest);
  };
})();
"""


def setup(context):
    page = context.new_page()
    page.add_init_script(STREAM_INIT)
    install_api_fixtures(page)
    page.route("**/api/chat/sessions/**", lambda route: route.fulfill(json={"items": [
        {"session_id": sid, "title": sid, "last_message": ""} for sid in HISTORIES]}))
    page.route("**/api/chat/history/**", lambda route: route.fulfill(
        json={"items": HISTORIES[route.request.url.rsplit("/", 1)[-1]]}))
    errors = {"page": [], "console": [], "requests": []}
    page.on("pageerror", lambda error: errors["page"].append(str(error)))
    page.on("console", lambda message: errors["console"].append(message.text) if message.type == "error" else None)
    page.on("requestfailed", lambda request: errors["requests"].append(request.url))
    page.goto(BASE_URL + "/chat", wait_until="domcontentloaded")
    return page, errors


def open_session(page, sid):
    if page.viewport_size["width"] <= 760:
        page.get_by_role("button", name="打开导航菜单", exact=True).click()
    page.get_by_role("button", name=sid, exact=True).click()
    expect(page.locator(".workspace-header h1")).to_have_text(sid)
    expect(page.locator("#question")).to_be_enabled()


def aligned(page, message_id):
    page.wait_for_function("""id => {
        const container = document.querySelector('.conversation');
        const box = container.getBoundingClientRect();
        const target = [...document.querySelectorAll('.message.user')].find(el => el.dataset.messageId === id);
        if (!target) return false;
        const top = target.getBoundingClientRect().top - box.top;
        const atStart = container.scrollTop < 1 && top >= 0 && top <= 24;
        const atEnd = container.scrollHeight - container.scrollTop - container.clientHeight < 1 && top >= 24 && top < box.height;
        return Math.abs(top - 24) < 3 || atStart || atEnd;
    }""", arg=message_id)


def choose(page, label):
    page.get_by_role("button", name="问题目录", exact=True).click()
    page.get_by_role("navigation", name="问题列表", exact=True).get_by_role("button", name=label, exact=True).click()


def check_history(browser, output, width, height):
    context = browser.new_context(viewport={"width": width, "height": height}, reduced_motion="reduce")
    try:
        page, errors = setup(context)
        timings = []
        for count in (2, 20, 100):
            started = time.perf_counter()
            open_session(page, f"nav-{count}")
            expect(page.locator(".message.user")).to_have_count(count)
            page.get_by_role("button", name="问题目录", exact=True).click()
            expect(page.locator(".conversation-navigator-item")).to_have_count(count)
            timings.append({"rounds": count, "load_and_directory_ms": round((time.perf_counter() - started) * 1000, 2)})
            first = page.locator(".conversation-navigator-item").first
            expect(first).to_have_attribute("title", LONG_QUESTION)
            first.focus()
            expect(page.locator(".conversation-navigator-question-preview")).to_have_text(LONG_QUESTION)
            page.keyboard.press("End")
            expect(page.locator(".conversation-navigator-item").last).to_be_focused()
            page.keyboard.press("Home")
            expect(first).to_be_focused()
            page.keyboard.press("ArrowDown")
            expect(page.locator(".conversation-navigator-item").nth(1)).to_be_focused()
            page.keyboard.press("ArrowUp")
            expect(first).to_be_focused()
            page.keyboard.press("Escape")
            expect(page.get_by_role("button", name="问题目录", exact=True)).to_be_focused()
            expect(page.get_by_role("navigation", name="问题列表")).not_to_be_visible()
            choose(page, f"第 {count} 个工作问题")
            aligned(page, f"{count}-{count - 1}:user")
            expect(page.locator(f'[data-message-id="{count}-{count - 1}:user"]')).to_be_focused()
            choose(page, LONG_QUESTION)
            aligned(page, f"{count}-0:user")
            page.get_by_role("button", name="问题目录", exact=True).click()
            expect(page.locator('.conversation-navigator-item[aria-current="location"]')).to_have_attribute("title", LONG_QUESTION)
            page.locator(".workspace-header h1").click()
            expect(page.get_by_role("navigation", name="问题列表")).not_to_be_visible()
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.get_by_role("button", name="问题目录", exact=True).click()
        page.screenshot(path=str(output / f"directory-{width}.png"))
        open_session(page, "nav-empty")
        expect(page.get_by_role("button", name="问题目录", exact=True)).not_to_be_visible()
        assert not any(errors.values()), errors
        return {"viewport": [width, height], "timings": timings, "diagnostics": errors}
    finally:
        context.close()


def push(page, event):
    page.evaluate("event => window.__navigationStream.push(event)", event)


def check_stream(browser, output, motion):
    context = browser.new_context(viewport={"width": 1440, "height": 900}, reduced_motion=motion)
    try:
        page, errors = setup(context)
        open_session(page, "nav-20")
        page.get_by_role("button", name="回到最新回答", exact=True).click()
        page.locator("#question").fill("生成中的新问题")
        page.get_by_role("button", name="发送", exact=True).click()
        page.wait_for_function("window.__navigationRequests.length === 1")
        push(page, {"type": "preview_delta", "content": "第一段草稿\n\n" * 20, "generation_id": "nav-generation"})
        expect(page.locator(".answer-preview")).to_contain_text("第一段草稿")
        choose(page, "第 10 个工作问题")
        aligned(page, "20-9:user")
        before = page.locator(".conversation").evaluate("el => el.scrollTop")
        for index in range(3):
            push(page, {"type": "preview_delta", "content": f"后续草稿 {index}\n\n" * 10, "generation_id": "nav-generation"})
            expect(page.locator(".answer-preview")).to_contain_text(f"后续草稿 {index}")
            assert abs(page.locator(".conversation").evaluate("el => el.scrollTop") - before) < 3
        page.get_by_role("button", name="回到最新回答", exact=True).click()
        push(page, {"type": "preview_delta", "content": "返回最新后继续生成\n\n" * 10, "generation_id": "nav-generation"})
        expect(page.locator(".answer-preview")).to_contain_text("返回最新后继续生成")
        page.wait_for_function("""() => {
            const el = document.querySelector('.conversation');
            return el.scrollHeight - el.scrollTop - el.clientHeight < 3;
        }""")
        choose(page, "第 10 个工作问题")
        aligned(page, "20-9:user")
        before = page.locator(".conversation").evaluate("el => el.scrollTop")
        push(page, {"type": "result", "content": "最终完整回答\n\n" * 50, "citations": [], "failure_type": "none", "trace_id": "nav-result"})
        page.evaluate("window.__navigationStream.close()")
        expect(page.locator(".message.assistant").last).to_contain_text("最终完整回答")
        assert abs(page.locator(".conversation").evaluate("el => el.scrollTop") - before) < 3
        page.get_by_role("button", name="回到最新回答", exact=True).click()
        assert page.locator(".conversation").evaluate("el => el.scrollHeight - el.scrollTop - el.clientHeight") < 3
        page.locator(".citation-row button").first.click()
        choose(page, "第 10 个工作问题")
        aligned(page, "20-9:user")
        expect(page.get_by_role("complementary", name="引用来源")).to_be_visible()
        assert page.evaluate("window.__navigationScrolls.at(-1).behavior") == ("auto" if motion == "reduce" else "smooth")
        page.get_by_role("button", name="问题目录", exact=True).click()
        page.keyboard.press("Escape")
        expect(page.get_by_role("navigation", name="问题列表")).not_to_be_visible()
        expect(page.get_by_role("complementary", name="引用来源")).to_be_visible()
        expect(page.get_by_role("button", name="问题目录", exact=True)).to_be_focused()
        page.screenshot(path=str(output / f"stream-{motion}.png"))
        assert not any(errors.values()), errors
        return {"motion": motion, "retained_scroll_top": before, "diagnostics": errors}
    finally:
        context.close()


def check_retries(browser, output):
    context = browser.new_context(viewport={"width": 1440, "height": 900}, reduced_motion="reduce")
    try:
        page, errors = setup(context)
        open_session(page, "nav-retry")
        expect(page.locator(".message.user:visible")).to_have_count(2)
        page.get_by_role("button", name="问题目录", exact=True).click()
        expect(page.locator(".conversation-navigator-item")).to_have_count(2)
        grouped = page.locator(".conversation-navigator-item").filter(has_text="3 次尝试")
        identity = grouped.get_attribute("data-navigation-id")
        assert identity
        grouped.click()
        aligned(page, "retry-2:user")
        page.get_by_role("button", name="查看此前 2 次尝试").click()
        expect(page.locator(".message.user:visible")).to_have_count(4)
        page.locator(".conversation").evaluate("el => { el.scrollTop = 0; el.dispatchEvent(new Event('scroll')); }")
        page.get_by_role("button", name="问题目录", exact=True).click()
        expect(page.locator('.conversation-navigator-item[aria-current="location"]')).to_have_attribute("data-navigation-id", identity)
        page.keyboard.press("Escape")
        page.get_by_role("button", name="收起此前 2 次尝试").click()
        expect(page.locator(".message.user:visible")).to_have_count(2)
        page.get_by_role("button", name="问题目录", exact=True).click()
        page.locator(".conversation-navigator-item").last.click()
        aligned(page, "unrelated:user")
        page.get_by_role("button", name="问题目录", exact=True).click()
        page.locator(".conversation-navigator-item").filter(has_text="3 次尝试").click()
        page.locator('[data-message-id="retry-2:assistant"]').get_by_role("button", name="重新发送", exact=True).click()
        page.wait_for_function("window.__navigationRequests.length === 1")
        request = page.evaluate("window.__navigationRequests[0]")
        assert request["attempt_group_id"] == "original"
        assert request["retry_of_turn_id"] == "retry-2"
        page.get_by_role("button", name="问题目录", exact=True).click()
        expect(page.locator(".conversation-navigator-item")).to_have_count(2)
        grouped = page.locator(".conversation-navigator-item").filter(has_text="4 次尝试")
        expect(grouped).to_have_attribute("data-navigation-id", identity)
        grouped.click()
        expect(page.locator(f'[data-message-id="{request["turn_id"]}:user"]')).to_be_focused()
        push(page, {"type": "result", "content": "重试成功", "citations": [], "failure_type": "none", "trace_id": "retry-result"})
        page.evaluate("window.__navigationStream.close()")
        expect(page.locator(".message.assistant").last).to_contain_text("重试成功")
        page.get_by_role("button", name="查看此前 3 次尝试").click()
        expect(page.locator(".message.user:visible")).to_have_count(5)
        page.get_by_role("button", name="问题目录", exact=True).click()
        open_session(page, "nav-2")
        expect(page.get_by_role("navigation", name="问题列表")).not_to_be_visible()
        open_session(page, "nav-retry")
        expect(page.locator(".message.user:visible")).to_have_count(2)
        assert not any(errors.values()), errors
        page.screenshot(path=str(output / "retry-session-switch.png"))
        return {"attempts": 4, "separate_identical_question": True, "diagnostics": errors}
    finally:
        context.close()


def main():
    output = ROOT / "work" / "m5-navigation" / datetime.now().strftime("%Y%m%d-%H%M%S")
    output.mkdir(parents=True)
    report = {"mocked_api": True, "checks": []}
    with sync_playwright() as playwright:
        executable = next(path for path in [Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
            Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")] if path.exists())
        report["browser_executable"] = str(executable)
        browser = playwright.chromium.launch(headless=True, executable_path=str(executable))
        checks = [(f"history-{width}", lambda width=width, height=height: check_history(browser, output, width, height))
            for width, height in [(1440, 900), (390, 844), (320, 640)]]
        checks += [(f"stream-{motion}", lambda motion=motion: check_stream(browser, output, motion))
            for motion in ("reduce", "no-preference")]
        checks += [("retry-groups", lambda: check_retries(browser, output))]
        try:
            for name, run in checks:
                print(f"START {name}", flush=True)
                try:
                    report["checks"].append({"name": name, "status": "passed", "detail": run()})
                except Exception as error:
                    report["checks"].append({"name": name, "status": "failed", "error": str(error)})
                    raise
                finally:
                    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        finally:
            browser.close()
    print(f"M5 browser acceptance passed: {len(checks)} scenarios. Evidence: {output}")


if __name__ == "__main__":
    main()
