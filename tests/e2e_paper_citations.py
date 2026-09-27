"""Citation presentation and source identity at the browser boundary (mock API)."""

from pathlib import Path
from playwright.sync_api import expect, sync_playwright
from e2e_mvp_browser import BASE_URL, ROOT, install_api_fixtures, sse


def check(browser, width, height):
    context = browser.new_context(viewport={"width": width, "height": height})
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on("console", lambda message: errors.append(message.text) if message.type == "error" else None)
    install_api_fixtures(page)
    page.route("**/api/chat/stream", lambda route: route.fulfill(
        content_type="text/event-stream",
        body=sse({
            "type": "result",
            "content": "差旅报销需要保留发票和行程单。[C3]\n\n超过限额需由财务负责人复核。[C8] 再次提交时也需保留凭证。[C3]",
            "citations": [
                {"citation_id": "C3", "title": "差旅报销管理办法", "page": 3, "quote": "报销需保留发票和行程单。"},
                {"citation_id": "C8", "title": "费用审批制度", "page": 5, "quote": "超过限额需由财务负责人复核。"},
            ],
            "failure_type": "none", "trace_id": "paper-citations",
        }) + sse({"type": "done"}),
    ))
    page.goto(BASE_URL + "/chat")
    page.locator("#question").fill("差旅报销需要什么凭证？")
    page.get_by_role("button", name="发送", exact=True).click()
    answer = page.locator(".message.assistant").last
    markers = answer.locator("sup button")
    expect(markers).to_have_text(["[1]", "[2]", "[1]"])
    expect(answer.locator(".reference-list button")).to_have_text([
        "[1]差旅报销管理办法 · 第 3 页", "[2]费用审批制度 · 第 5 页",
    ])
    assert "<sup>" not in answer.inner_text()
    assert "C3" not in answer.inner_text()
    assert markers.first.evaluate("el => parseFloat(getComputedStyle(el.parentElement).top)") < 0
    out = ROOT / "work/paper-citations"
    out.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(out / f"answer-{width}.png"), full_page=True)

    markers.nth(1).click()
    expect(page.locator('.evidence-card[data-citation-id="C8"]')).to_be_focused()
    expect(page.locator('.evidence-card[data-citation-id="C8"] .evidence-number')).to_have_text("[2]")
    expect(page.locator('.evidence-card[data-citation-id="C8"] blockquote')).to_have_text("超过限额需由财务负责人复核。")
    assert 'C3' not in page.locator('.evidence-panel').inner_text()
    assert 'C8' not in page.locator('.evidence-panel').inner_text()
    page.keyboard.press("Escape")
    expect(markers.nth(1)).to_be_focused()
    # Real pointer input removes focus outline; keyboard navigation retains it.
    page.mouse.click(5, height - 5)
    markers.first.focus()
    page.keyboard.press("Tab")
    expect(markers.nth(1)).to_be_focused()
    assert markers.nth(1).evaluate("el => getComputedStyle(el).outlineStyle") != "none"
    page.keyboard.press("Enter")
    expect(page.locator('.evidence-card[data-citation-id="C8"]')).to_be_focused()
    page.keyboard.press("Escape")
    answer.locator(".reference-list button").first.click()
    expect(page.locator('.evidence-card[data-citation-id="C3"]')).to_be_focused()
    page.wait_for_function("""() => {
        const panel = document.querySelector('.evidence-panel.open');
        if (!panel) return false;
        const rect = panel.getBoundingClientRect();
        return rect.left >= 0 && rect.right <= innerWidth + 1;
    }""")
    page.screenshot(path=str(out / f"source-{width}.png"), full_page=True)
    page.get_by_role("button", name="关闭引用证据").click()
    trigger = answer.locator(".reference-list button").first
    expect(trigger).to_be_focused()
    assert trigger.evaluate("el => getComputedStyle(el).outlineStyle") == "none"
    assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    assert not errors, errors
    context.close()


if __name__ == "__main__":
    with sync_playwright() as p:
        executable = next(path for path in [
            Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
            Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
        ] if path.exists())
        browser = p.chromium.launch(headless=True, executable_path=str(executable))
        try:
            check(browser, 1440, 900)
            check(browser, 390, 844)
        finally:
            browser.close()
    print("PASS: desktop/mobile superscripts, sparse/repeated IDs, references, source selection, keyboard and pointer focus; no console errors.")
