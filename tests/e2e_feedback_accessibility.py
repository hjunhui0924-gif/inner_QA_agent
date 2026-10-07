"""M4 feedback keyboard, focus, background isolation and reading-position checks.

Real Vue UI and browser keyboard events; synthetic history and mocked feedback API.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime

from playwright.sync_api import expect, sync_playwright

from e2e_mvp_browser import BASE_URL, ROOT, install_api_fixtures
from e2e_conversation_navigation import history_pair


SCENARIOS = ["keyboard-1440", "keyboard-390", "keyboard-320", "pending",
             "submit", "scrim", "evidence", "reading"]
HISTORY = [item for index in range(8)
           for item in history_pair(f"feedback-{index}", f"第 {index + 1} 个审批问题")]


def check(browser, output, scenario, base_url):
    width = int(scenario.split("-")[1]) if scenario.startswith("keyboard-") else (1440 if scenario == "evidence" else 390)
    context = browser.new_context(viewport={"width": width, "height": 640 if width == 320 else 900}, reduced_motion="reduce")
    page = context.new_page()
    install_api_fixtures(page)
    page.route("**/api/chat/sessions/**", lambda route: route.fulfill(json={"items": [
        {"session_id": "feedback-history", "title": "反馈验收会话", "last_message": ""}]}))
    page.route("**/api/chat/history/**", lambda route: route.fulfill(json={"items": HISTORY}))
    diagnostics = {"page": [], "console": [], "requests": [], "expected_console": []}
    page.on("pageerror", lambda error: diagnostics["page"].append(str(error)))

    def console(message):
        if message.type != "error": return
        expected = scenario == "submit" and "/api/chat/feedback" in message.location.get("url", "") and "502" in message.text
        diagnostics["expected_console" if expected else "console"].append(message.text)

    page.on("console", console)
    page.on("requestfailed", lambda request: diagnostics["requests"].append({"url": request.url, "failure": request.failure}))
    calls, pending = [], []

    def feedback(route):
        calls.append(route.request.post_data_json)
        if scenario == "pending": pending.append(route)
        elif scenario == "submit" and len(calls) == 1:
            route.fulfill(status=502, json={"detail": "Synthetic feedback failure"})
        else: route.fulfill(json={"feedback_id": calls[-1]["feedback_id"], "status": "recorded"})

    page.route("**/api/chat/feedback", feedback)
    try:
        page.goto(base_url + "/chat", wait_until="domcontentloaded")
        expect(page.locator("#question")).to_be_enabled()
        if width <= 760: page.get_by_role("button", name="打开导航菜单", exact=True).click()
        page.get_by_role("button", name="反馈验收会话", exact=True).click()
        expect(page.locator(".message.assistant")).to_have_count(8)
        answer = page.locator(".message.assistant").nth(2 if scenario == "reading" else 7)
        trigger = answer.get_by_role("button", name="反馈回答", exact=True)
        conversation = page.locator(".conversation")
        if scenario == "evidence":
            answer.get_by_role("button", name="合成差旅制度").click()
            expect(page.locator(".evidence-panel")).to_have_class("evidence-panel open")
        if scenario == "reading":
            conversation.hover()
            page.mouse.wheel(0, -2200)
        trigger.scroll_into_view_if_needed()
        trigger.focus()
        before = conversation.evaluate("el => el.scrollTop")
        trigger.press("Enter")
        dialog = page.get_by_role("dialog", name="反馈回答", exact=True)
        selected = dialog.get_by_role("radio", name="没有解决问题", exact=True)
        note = dialog.get_by_placeholder("补充说明（可选）")
        cancel = dialog.get_by_role("button", name="取消", exact=True)
        submit = dialog.get_by_role("button", name="提交反馈", exact=True)
        expect(selected).to_be_focused(timeout=2000)
        expect(selected).to_be_checked()
        assert page.locator(".chat-surface").evaluate("el => el.inert")
        assert page.locator(".mobile-bar").evaluate("el => el.inert")
        assert page.evaluate("document.body.style.overflow") == "hidden"
        page.locator("#question").evaluate("el => el.focus()")
        expect(selected).to_be_focused()

        if scenario.startswith("keyboard-"):
            expect(dialog.get_by_role("group", name="反馈类型", exact=True)).to_be_visible()
            expect(dialog.get_by_role("textbox", name="补充说明（可选）", exact=True)).to_be_visible()
            selected.press("ArrowRight")
            selected = dialog.get_by_role("radio", name="其他", exact=True)
            expect(selected).to_be_focused()
            expect(selected).to_be_checked()
            for control in (note, cancel, submit, selected):
                page.keyboard.press("Tab")
                expect(control).to_be_focused()
            for control in (submit, cancel, note, selected):
                page.keyboard.press("Shift+Tab")
                expect(control).to_be_focused()
            # Composition cancellation should stay with the IME, not close the form.
            selected.dispatch_event("keydown", {"key": "Escape", "isComposing": True})
            expect(dialog).to_be_visible()
            assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
            box = dialog.bounding_box()
            assert box and box["y"] >= 0 and box["y"] + box["height"] <= page.viewport_size["height"]
            page.screenshot(path=str(output / f"{scenario}-open.png"))
            page.keyboard.press("Escape")
        elif scenario == "pending":
            note.fill("提交期间仍可关闭")
            submit.click()
            expect(dialog.get_by_role("button", name="提交中…", exact=True)).to_be_disabled()
            cancel.focus()
            for control in (selected, note, cancel):
                page.keyboard.press("Tab")
                expect(control).to_be_focused()
            selected.focus()
            page.keyboard.press("Shift+Tab")
            expect(cancel).to_be_focused()
            page.keyboard.press("Escape")
            expect(dialog).to_have_count(0)
            expect(trigger).to_be_focused()
            assert pending
            pending[0].fulfill(json={"feedback_id": calls[0]["feedback_id"], "status": "recorded"})
        elif scenario == "submit":
            note.fill("请补充审批条件")
            submit.click()
            expect(dialog.get_by_role("alert")).to_contain_text("反馈提交失败")
            expect(note).to_have_value("请补充审批条件")
            cancel.focus()
            page.keyboard.press("Tab")
            expect(submit).to_be_focused()
            page.keyboard.press("Tab")
            expect(selected).to_be_focused()
            note.fill("请补充审批条件，并提供制度版本")
            submit.focus()
            page.keyboard.press("Enter")
        elif scenario == "scrim":
            page.locator(".feedback-dialog-scrim").click(position={"x": 5, "y": 5})
        elif scenario == "evidence":
            assert page.locator(".evidence-panel").evaluate("el => el.inert")
            page.locator(".evidence-close").evaluate("el => el.focus()")
            expect(selected).to_be_focused()
            page.keyboard.press("Escape")
            expect(dialog).to_have_count(0)
            expect(trigger).to_be_focused()
            expect(page.locator(".evidence-panel")).to_have_class("evidence-panel open")
            assert not page.locator(".evidence-panel").evaluate("el => el.inert")
            page.keyboard.press("Escape")
            expect(page.locator(".evidence-panel")).not_to_have_class("evidence-panel open")
            expect(answer.get_by_role("button", name="合成差旅制度")).to_be_focused()
        else:
            cancel.click()

        expect(dialog).to_have_count(0)
        if scenario != "evidence": expect(trigger).to_be_focused()
        assert not page.locator(".chat-surface").evaluate("el => el.inert")
        assert page.evaluate("document.body.style.overflow") != "hidden"
        after = conversation.evaluate("el => el.scrollTop")
        if scenario == "reading":
            assert abs(after - before) < 3, {"before": before, "after": after}
            assert conversation.evaluate("el => el.scrollHeight - el.scrollTop - el.clientHeight") > 1000
        if scenario == "submit":
            assert len(calls) == 2 and calls[0]["feedback_id"] != calls[1]["feedback_id"], calls
        assert not diagnostics["page"] and not diagnostics["console"] and not diagnostics["requests"], diagnostics
        page.screenshot(path=str(output / f"{scenario}-closed.png"))
        return {"viewport": page.viewport_size, "scroll_before": before, "scroll_after": after,
                "feedback_calls": calls, "diagnostics": diagnostics}
    except Exception:
        page.screenshot(path=str(output / f"failed-{scenario}.png"))
        (output / f"failed-{scenario}.json").write_text(json.dumps({"calls": calls, "diagnostics": diagnostics},
            ensure_ascii=False, indent=2), encoding="utf-8")
        raise
    finally:
        context.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=BASE_URL)
    parser.add_argument("--browsers", nargs="+", default=["chrome"], choices=["chrome", "msedge", "firefox", "webkit"])
    parser.add_argument("--scenario", action="append", choices=SCENARIOS)
    options = parser.parse_args()
    output = ROOT / "work" / "feedback-accessibility" / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    output.mkdir(parents=True)
    report = {"mocked_api": True, "base_url": options.base_url, "checks": []}
    with sync_playwright() as p:
        for name in options.browsers:
            browser = p.chromium.launch(channel=name) if name in ("chrome", "msedge") else getattr(p, name).launch()
            browser_output = output / name
            browser_output.mkdir()
            try:
                for scenario in options.scenario or SCENARIOS:
                    row = {"browser": name, "version": browser.version, "scenario": scenario, "status": "passed"}
                    try: row["detail"] = check(browser, browser_output, scenario, options.base_url)
                    except Exception as error: row.update(status="failed", error=str(error))
                    report["checks"].append(row)
                    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
                    print(name, scenario, row["status"], flush=True)
            finally:
                browser.close()
    passed = sum(row["status"] == "passed" for row in report["checks"])
    print(f'Feedback accessibility {passed}/{len(report["checks"])} passed. Evidence: {output}', flush=True)
    return int(passed != len(report["checks"]))


if __name__ == "__main__": raise SystemExit(main())
