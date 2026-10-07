"""M6 SSE and reading-position acceptance with controlled browser streams.

Mocks isolate transport/UI behavior, not live model or real phone quality.
Run against Vite: python tests/e2e_stream_scroll.py --browsers chrome
"""
from __future__ import annotations
import argparse
import json
from datetime import datetime
from pathlib import Path
from playwright.sync_api import expect, sync_playwright
from e2e_mvp_browser import BASE_URL, ROOT, install_api_fixtures

STREAM_INIT = r"""(() => {
  const original = window.fetch.bind(window);
  window.m6Requests = [];
  window.m6Streams = [];
  window.fetch = async (input, init) => {
    if (!String(input).endsWith('/chat/stream')) return original(input, init);
    window.m6Requests.push(JSON.parse(init.body));
    const item = {closed: false, cancelled: false, aborted: false};
    window.m6Streams.push(item);
    const body = new ReadableStream({start(controller) {
      item.wire = (text, bytewise = false) => {
        const bytes = new TextEncoder().encode(text);
        if (bytewise) for (const byte of bytes) controller.enqueue(new Uint8Array([byte]));
        else controller.enqueue(bytes);
      };
      item.push = event => item.wire('data: ' + JSON.stringify(event) + '\n\n');
      item.close = () => { controller.close(); item.closed = true; };
      item.fail = () => { controller.error(new Error('synthetic transport failure')); item.closed = true; };
      init.signal.addEventListener('abort', () => {
        item.aborted = true;
        if (!item.closed) { controller.error(new DOMException('Cancelled', 'AbortError')); item.closed = true; }
      }, {once: true});
    }, cancel() { item.cancelled = true; item.closed = true; }});
    return new Response(body, {headers: {'Content-Type': 'text/event-stream'}});
  };
})()"""

HISTORY = []
for index in range(20):
    shared = {"turn_id": f"history-{index}", "mode": "general"}
    HISTORY += [
        {**shared, "role": "user", "content": f"第 {index + 1} 个审批问题", "message_id": f"history-{index}:user"},
        {**shared, "role": "assistant", "message_id": f"history-{index}:assistant", "failure_type": "none",
         "content": "\n\n".join(f"第 {index + 1} 题第 {part + 1} 段审批说明：提交申请后由经理核对材料与审批条件。[C1]" for part in range(12)),
         "citations": [{"citation_id": "C1", "title": "合成审批制度", "quote": "经理核对材料"}]},
    ]


def setup(browser, width=390, motion="reduce"):
    context = browser.new_context(viewport={"width": width, "height": 900 if width > 760 else 844}, reduced_motion=motion)
    page = context.new_page()
    page.add_init_script(STREAM_INIT)
    install_api_fixtures(page)
    page.route("**/api/chat/sessions/**", lambda route: route.fulfill(json={"items": [
        {"session_id": "m6-history", "title": "m6-history", "last_message": ""}]}))
    page.route("**/api/chat/history/**", lambda route: route.fulfill(json={"items": HISTORY}))
    errors = {"page": [], "console": [], "requests": []}
    page.on("pageerror", lambda error: errors["page"].append(str(error)))
    page.on("console", lambda message: errors["console"].append(message.text) if message.type == "error" else None)
    page.on("requestfailed", lambda request: errors["requests"].append(request.url))
    page.goto(BASE_URL + "/chat", wait_until="domcontentloaded")
    expect(page.locator("#question")).to_be_enabled()
    return context, page, errors


def history(page):
    if page.viewport_size["width"] <= 760:
        page.get_by_role("button", name="打开导航菜单", exact=True).click()
    page.get_by_role("button", name="m6-history", exact=True).click()
    expect(page.locator(".workspace-header h1")).to_have_text("m6-history")
    expect(page.locator(".message.user")).to_have_count(20)


def send(page, question="审批流程怎么走？"):
    page.locator("#question").fill(question)
    page.get_by_role("button", name="发送", exact=True).click()
    page.wait_for_function("window.m6Streams.length > 0")


def push(page, event):
    page.evaluate("event => window.m6Streams.at(-1).push(event)", event)


def final(content="经理审批后的正式答案🙂", failure="none"):
    return {"type": "result", "content": content, "citations": [], "trace_id": "m6-trace", "failure_type": failure}


def ready(page):
    expect(page.get_by_role("button", name="停止回答", exact=True)).to_have_count(0)
    expect(page.locator(".answer-wait")).to_have_count(0)


def clean(errors):
    assert not any(errors.values()), errors
    return errors


def check_delivery(browser, out, scenario):
    context, page, errors = setup(browser)
    try:
        if scenario == "generation": history(page)
        send(page)
        assistant = page.locator(".message.assistant").last
        page.locator("#question").fill("下一条问题的草稿")
        push(page, {"type": "status", "node": "retrieve", "content": "retrieving"})
        push(page, {"type": "token", "content": "不应显示的候选内容"})
        if scenario == "framing":
            event = final()
            wire = ': heartbeat\r\n\r\ndata: {\r\ndata: ' + json.dumps(event, ensure_ascii=False)[1:] + '\r\n\r\n'
            page.evaluate("wire => window.m6Streams.at(-1).wire(wire, true)", wire)
            push(page, final("不应覆盖的第二个答案"))
            push(page, {"type": "status", "node": "error", "content": "late"})
            push(page, {"type": "token", "content": "late"})
            # Deliberately leave HTTP body open after done.
            push(page, {"type": "done", "trace_id": "late-trace"})
            expect(assistant.locator(".message-content")).to_have_text(event["content"])
            ready(page)
            assert page.evaluate("window.m6Streams[0].cancelled")
        elif scenario == "transport-before":
            page.evaluate("window.m6Streams.at(-1).fail()")
            expect(assistant.locator(".failure-error")).to_contain_text("本次回答未完成")
            expect(assistant.locator(".message-content")).to_have_count(0)
        elif scenario == "transport-after":
            push(page, final())
            expect(assistant.locator(".message-content")).to_contain_text("正式答案🙂")
            page.evaluate("window.m6Streams.at(-1).fail()")
            ready(page)
            expect(assistant.locator(".failure-notice")).to_have_count(0)
        elif scenario == "invalid-result":
            push(page, {**final(), "content": None})
            expect(assistant.locator(".failure-error")).to_contain_text("本次回答未完成")
            expect(assistant.locator(".message-content")).to_have_count(0)
            assert page.evaluate("window.m6Streams[0].cancelled")
        elif scenario == "fallback":
            push(page, final("资料不足，无法回答。", "retrieval_miss"))
            page.evaluate("window.m6Streams.at(-1).close()")
            expect(assistant.locator(".failure-fallback")).to_contain_text("没有找到足够相关的知识")
            expect(assistant.locator(".failure-error")).to_have_count(0)
        elif scenario == "cancel-retry":
            expect(assistant.locator(".answer-wait")).to_be_visible()
            page.get_by_role("button", name="停止回答", exact=True).click()
            expect(assistant).to_contain_text("本次回答已取消")
            expect(assistant.get_by_role("button", name="重新生成", exact=True)).to_be_visible()
            assert page.evaluate("window.m6Streams[0].aborted")
            assistant.get_by_role("button", name="重新生成", exact=True).click()
            page.wait_for_function("window.m6Requests.length === 2")
            first, second = page.evaluate("window.m6Requests")
            assert first["turn_id"] != second["turn_id"]
            assert first["attempt_group_id"] == second["attempt_group_id"]
            assert second["retry_of_turn_id"] == first["turn_id"]
            push(page, final("重试后的正式答案"))
            page.evaluate("window.m6Streams.at(-1).close()")
            expect(page.locator(".message.assistant").last).to_contain_text("重试后的正式答案")
            page.get_by_role("button", name="查看此前 1 次尝试").click()
            expect(page.locator(".message.assistant").first).to_contain_text("本次回答已取消")
        elif scenario == "generation":
            push(page, {"type": "preview_delta", "generation_id": "old", "content": "旧草稿"})
            push(page, {"type": "preview_delta", "generation_id": "new", "content": "新草稿"})
            push(page, {"type": "preview_delta", "generation_id": "old", "content": "迟到的旧草稿"})
            expect(assistant.locator(".answer-preview")).to_contain_text("新草稿")
            expect(assistant.locator(".answer-preview")).not_to_contain_text("旧草稿")
            push(page, final())
            page.evaluate("window.m6Streams.at(-1).close()")
            expect(assistant.locator(".answer-preview")).to_have_count(0)
            expect(assistant).to_contain_text("正式答案🙂")
        ready(page)
        expect(page.locator("#question")).to_have_value("下一条问题的草稿")
        assert len(page.evaluate("window.m6Requests")) == (2 if scenario == "cancel-retry" else 1)
        expect(page.locator(".message.assistant").last).not_to_contain_text("不应显示的候选内容")
        page.screenshot(path=str(out / f"{scenario}.png"))
        return {"scenario": scenario, "diagnostics": clean(errors)}
    finally: context.close()


def at_bottom(page):
    page.wait_for_function("() => { const el = document.querySelector('.conversation'); return el.scrollHeight - el.scrollTop - el.clientHeight < 3; }")


def check_manual_input(browser, out):
    context, page, errors = setup(browser)
    stage = 'initial-follow'
    try:
        history(page)
        page.get_by_role('button', name='回到最新回答', exact=True).click()
        send(page, '输入意图专项')
        push(page, {'type': 'preview_delta', 'generation_id': 'input', 'content': '审批草稿。\n\n' * 30})
        expect(page.locator('.answer-preview')).to_contain_text('审批草稿')
        at_bottom(page)
        conversation = page.locator('.conversation')
        conversation.focus()
        conversation.press('ArrowUp')
        stage = 'keyboard-pause'
        page.wait_for_function("() => { const el = document.querySelector('.conversation'); return el.scrollHeight - el.scrollTop - el.clientHeight > 15; }")
        conversation.evaluate("""async el => {
          let previous = el.scrollTop, stable = 0;
          for (let i = 0; i < 90; i++) {
            await new Promise(requestAnimationFrame);
            stable = Math.abs(el.scrollTop - previous) < .1 ? stable + 1 : 0;
            previous = el.scrollTop;
            if (stable >= 4) return;
          }
          throw new Error('Keyboard scroll did not settle');
        }""")
        before = conversation.evaluate('el => el.scrollTop')
        push(page, {'type': 'preview_delta', 'generation_id': 'input', 'content': '键盘阅读时追加。\n\n' * 10})
        expect(page.locator('.answer-preview')).to_contain_text('键盘阅读时')
        page.wait_for_timeout(100)
        assert abs(conversation.evaluate('el => el.scrollTop') - before) < 3, {'before':before,'after':conversation.evaluate('el => el.scrollTop')}
        stage = 'keyboard-resume'
        conversation.press('End')
        at_bottom(page)
        push(page, {'type': 'preview_delta', 'generation_id': 'input', 'content': '手动回底部后追加。\n\n' * 10})
        expect(page.locator('.answer-preview')).to_contain_text('手动回底部')
        at_bottom(page)
        # Synthetic touches verify event wiring. Native phone gestures remain a
        # separate acceptance boundary; programmatic scrolling applies the move.
        conversation.evaluate("""el => {
          for (const [type, y] of [['touchstart',100],['touchmove',135]]) {
            const event = new Event(type); Object.defineProperty(event,'touches',{value:[{clientY:y}]}); el.dispatchEvent(event);
          }
          el.scrollTop -= 35; el.dispatchEvent(new Event('scroll'));
        }""")
        stage = 'touch-pause'
        before = conversation.evaluate('el => el.scrollTop')
        push(page, {'type': 'preview_delta', 'generation_id': 'input', 'content': '触摸阅读时追加。\n\n' * 10})
        expect(page.locator('.answer-preview')).to_contain_text('触摸阅读时')
        page.wait_for_timeout(100)
        assert abs(conversation.evaluate('el => el.scrollTop') - before) < 3, {'before':before,'after':conversation.evaluate('el => el.scrollTop')}
        stage = 'touch-resume'
        conversation.evaluate("el => { el.scrollTop = el.scrollHeight; el.dispatchEvent(new Event('scroll')); }")
        push(page, {'type': 'preview_delta', 'generation_id': 'input', 'content': '触摸回底部后追加。\n\n' * 10})
        expect(page.locator('.answer-preview')).to_contain_text('触摸回底部')
        at_bottom(page)
        push(page, final())
        page.evaluate('window.m6Streams.at(-1).close()')
        ready(page)
        page.screenshot(path=str(out / 'manual-input.png'))
        return {'keyboard': True, 'synthetic_touch_events': True, 'manual_bottom_resumes': True, 'diagnostics': clean(errors)}
    except Exception as error:
        page.screenshot(path=str(out / 'failed-manual-input.png'))
        metrics = page.locator('.conversation').evaluate('el => ({top: el.scrollTop, height: el.clientHeight, total: el.scrollHeight})')
        raise RuntimeError(f'{stage}: {error}; metrics={metrics}; diagnostics={errors}') from error
    finally: context.close()


ANCHOR = """() => {
  const container = document.querySelector('.conversation'), top = container.getBoundingClientRect().top;
  const paragraphs = [...container.querySelectorAll('.message-content > p')];
  const node = paragraphs.find(node => node.getBoundingClientRect().top >= top && node.getBoundingClientRect().top < top + container.clientHeight / 2);
  if (!node) throw new Error('No visible reading paragraph');
  window.m6Anchor = {node, offset: node.getBoundingClientRect().top - top, text: node.textContent};
  return {offset: window.m6Anchor.offset, text: window.m6Anchor.text, scrollTop: container.scrollTop};
}"""


def same_anchor(page):
    page.wait_for_function("""() => {
      const anchor = window.m6Anchor, container = document.querySelector('.conversation');
      return anchor.node.isConnected && Math.abs(anchor.node.getBoundingClientRect().top - container.getBoundingClientRect().top - anchor.offset) < 3;
    }""")


def check_scroll(browser, out, width, motion):
    context, page, errors = setup(browser, width, motion)
    stage = 'history'
    try:
        history(page)
        page.get_by_role("button", name="回到最新回答", exact=True).click()
        send(page, "生成中阅读旧审批说明")
        push(page, {"type": "preview_delta", "generation_id": "scroll", "content": "草稿中的审批条件。\n\n" * 30})
        expect(page.locator(".answer-preview")).to_contain_text("审批条件")
        stage = 'initial-follow'
        at_bottom(page)
        page.locator("#question").fill("下一条问题\n" * 10)
        stage = 'composer-grow'
        at_bottom(page)
        # A small upward wheel must pause even within the old 80px threshold.
        box = page.locator(".conversation").bounding_box()
        page.mouse.move(box['x'] + box['width'] / 2, box['y'] + box['height'] / 2)
        page.mouse.wheel(0, -35)
        stage = 'wheel-intent'
        page.wait_for_function("() => { const el = document.querySelector('.conversation'); return el.scrollHeight - el.scrollTop - el.clientHeight > 15; }")
        before = page.locator(".conversation").evaluate("el => el.scrollTop")
        push(page, {"type": "preview_delta", "generation_id": "scroll", "content": "向上阅读时继续生成。\n\n" * 10})
        expect(page.locator(".answer-preview")).to_contain_text("向上阅读时")
        page.wait_for_timeout(100)
        assert abs(page.locator(".conversation").evaluate("el => el.scrollTop") - before) < 3
        page.locator(".conversation").evaluate("el => { el.scrollTop = 1200; el.dispatchEvent(new Event('scroll')); }")
        anchor = page.evaluate(ANCHOR)
        page.locator("#question").fill("短草稿")
        stage = 'composer-shrink-reading'
        same_anchor(page)
        if width >= 1280:
            page.evaluate("window.m6Anchor.node.querySelector('button.inline-citation').click()")
            expect(page.get_by_role("complementary", name="引用来源")).to_be_visible()
            stage = 'evidence-open-reading'
            same_anchor(page)
            page.get_by_role("button", name="关闭引用证据").click()
            stage = 'evidence-close-reading'
            same_anchor(page)
        push(page, final("最终审批说明。\n\n" * 60))
        page.evaluate("window.m6Streams.at(-1).close()")
        ready(page)
        stage = 'formal-result-reading'
        same_anchor(page)
        page.get_by_role("button", name="回到最新回答", exact=True).click()
        stage = 'resume-follow'
        at_bottom(page)
        # Late table/layout growth should keep following only at the bottom.
        page.locator('.message.assistant').last.evaluate("el => { const node = document.createElement('p'); node.style.height = '180px'; node.textContent = '模拟表格布局高度变化'; el.append(node); }")
        stage = 'late-layout-growth'
        at_bottom(page)
        page.screenshot(path=str(out / f"scroll-{width}-{motion}.png"))
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        return {"width": width, "motion": motion, "retained_anchor": anchor, "diagnostics": clean(errors)}
    except Exception as error:
        page.screenshot(path=str(out / f'failed-scroll-{width}-{motion}.png'))
        metrics = page.locator('.conversation').evaluate('el => ({top: el.scrollTop, height: el.clientHeight, total: el.scrollHeight})')
        raise RuntimeError(f'{stage}: {error}; metrics={metrics}; diagnostics={errors}') from error
    finally: context.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--browsers', nargs='+', default=['chrome'])
    parser.add_argument('--scenarios', nargs='+')
    args = parser.parse_args()
    output = ROOT / 'work' / 'dsh-frontend' / 'm6' / datetime.now().strftime('%Y%m%d-%H%M%S-%f')
    output.mkdir(parents=True)
    report = {"mocked_api": True, "checks": []}
    with sync_playwright() as p:
        for name in args.browsers:
            browser = p.chromium.launch(channel=name) if name in ('chrome','msedge') else getattr(p,name).launch()
            out = output / name
            out.mkdir()
            scenarios = [(scenario, lambda scenario=scenario: check_delivery(browser,out,scenario)) for scenario in (
                'framing','transport-before','transport-after','invalid-result','fallback','cancel-retry','generation')]
            scenarios += [(f'scroll-{width}-{motion}', lambda width=width,motion=motion: check_scroll(browser,out,width,motion))
                for width in (1440,390,320) for motion in ('reduce','no-preference')]
            scenarios += [('manual-input', lambda: check_manual_input(browser, out))]
            try:
                for scenario, run in scenarios:
                    if args.scenarios and not any(part in scenario for part in args.scenarios): continue
                    print('START',name,scenario,flush=True)
                    row = {'browser':name,'version':browser.version,'scenario':scenario,'status':'passed'}
                    try: row['detail'] = run()
                    except Exception as error: row.update(status='failed',error=str(error))
                    report['checks'].append(row)
                    (output / 'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
                    print(name,scenario,row['status'],flush=True)
            finally: browser.close()
    passed = sum(row['status']=='passed' for row in report['checks'])
    print(f'M6 {passed}/{len(report["checks"])} passed. Evidence: {output}',flush=True)
    return int(passed != len(report['checks']))


if __name__ == '__main__': raise SystemExit(main())
