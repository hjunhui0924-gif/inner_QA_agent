"""Real pdf.js/worker browser acceptance using temporary synthetic PDF bytes.

API fixtures isolate UI rendering; these do not measure real LLM answer quality.
Run: python tests/e2e_pdf_source_preview.py --browsers chrome msedge firefox webkit
Vite must already be listening; --base-url also accepts a production preview.
"""
from __future__ import annotations

import json
import argparse
from datetime import datetime
from pathlib import Path

from playwright.sync_api import expect, sync_playwright
from e2e_mvp_browser import BASE_URL, ROOT, install_api_fixtures
from browser_pdf_fixtures import CHINESE_QUOTE, QUOTE, make_pdf


def canvas_pixels(page):
    return page.locator(".pdf-page-scroll canvas").evaluate("""canvas => {
        const {data} = canvas.getContext('2d').getImageData(0, 0, canvas.width, canvas.height);
        let blue = 0, red = 0;
        for (let i = 0; i < data.length; i += 4) {
          const r = data[i], g = data[i + 1], b = data[i + 2], a = data[i + 3];
          if (a && b > r * 2 && b > g * 1.5) blue++;
          if (a && r > b * 2 && r > g * 1.5) red++;
        }
        return {width: canvas.width, height: canvas.height, blue, red,
          visible_width: canvas.getBoundingClientRect().width};
    }""")


def rendered(preview):
    expect(preview).to_have_attribute("aria-busy", "false", timeout=15000)
    expect(preview.locator("canvas")).to_be_visible()
    expect(preview.get_by_role("alert")).to_have_count(0)


def setup(browser, width, pdf_bytes, *, headers=None, worker_failure=False, delayed=False, status=200, quote=QUOTE):
    context = browser.new_context(viewport={"width": width, "height": 900 if width > 760 else 844})
    page = context.new_page()
    errors, logs, downloads, workers, closed_workers, pending, requests, assets = [], [], [], [], [], [], [], []
    page.on("pageerror", lambda error: errors.append(str(error)))
    page.on("console", lambda message: logs.append({"type": message.type, "text": message.text})
        if message.type in ("warning", "error") else None)
    page.on("requestfailed", lambda request: requests.append({"url": request.url, "failure": request.failure}))
    page.on("request", lambda request: assets.append(request.url) if '/pdfjs/' in request.url else None)
    def worker_started(worker):
        workers.append(worker.url)
        worker.on("close", lambda _: closed_workers.append(worker.url))
    page.on("worker", worker_started)
    page.add_init_script("""(() => {
      const original = window.fetch;
      window.pdfPreviewAborts = 0;
      window.fetch = (input, init) => {
        if (String(input).includes('/download?preview=true')) {
          init?.signal?.addEventListener('abort', () => window.pdfPreviewAborts++);
        }
        return original(input, init);
      };
    })()""")
    install_api_fixtures(page)
    record = {"source_id": "real-pdf", "title": "合成差旅制度", "source": "internal_upload",
        "original_filename": "travel-policy.pdf", "content": quote}
    page.route("**/api/knowledge/records", lambda route: route.fulfill(json={"items": [record]}))
    page.route("**/api/knowledge/records/real-pdf", lambda route: route.fulfill(json={"record": record}))
    def download(route):
        downloads.append(route.request.url)
        if delayed:
            pending.append(route)
        else:
            route.fulfill(status=status, body=pdf_bytes, content_type="application/pdf", headers=headers or {})
    page.route("**/api/knowledge/records/real-pdf/download**", download)
    if worker_failure:
        page.route("**/*pdf.worker*.mjs*", lambda route: route.continue_() if "?url" in route.request.url else route.abort())
    return context, page, {"page_errors": errors, "console": logs, "downloads": downloads,
        "workers": workers, "closed_workers": closed_workers, "pending": pending,
        "failed_requests": requests, "resource_requests": assets}


def open_detail(page):
    page.goto(BASE_URL + "/knowledge", wait_until="domcontentloaded")
    page.get_by_role("button", name="查看详情", exact=True).click()
    drawer = page.get_by_role("dialog", name="文档详情", exact=True)
    expect(drawer).to_be_visible()
    return drawer


def open_pdf(drawer):
    drawer.get_by_role("button", name="打开 PDF 原页", exact=True).click()
    return drawer.get_by_role("region", name="PDF 原页预览")


def clean_state(state, allow_console_errors=False, *, cancelled_download=False, worker_failure=False, completed_stream=False):
    assert not state["page_errors"], state["page_errors"]
    if not allow_console_errors:
        assert not [item for item in state["console"] if item["type"] == "error"], state["console"]
        assert not [item for item in state["console"] if "PDF page rendering failed" in item["text"]], state["console"]
    unexpected = [request for request in state["failed_requests"] if not (
        cancelled_download and '/download?preview=true' in request['url']
        or worker_failure and 'pdf.worker' in request['url']
        or completed_stream and request['url'].endswith('/api/chat/stream'))]
    assert not unexpected, unexpected
    return {**{key: value for key, value in state.items() if key != "pending"}, 'unexpected_requests': unexpected}


def capture_failure(page, state, output, name):
    page.screenshot(path=str(output / f"failed-{name}.png"))
    (output / f"failed-{name}.json").write_text(json.dumps({
        "url": page.url, "body": page.locator('body').inner_text(),
        **{key: value for key, value in state.items() if key != 'pending'},
    }, ensure_ascii=False, indent=2), encoding='utf-8')


def run_standard(browser, output, pdf_bytes, width, quote=QUOTE, name=None):
    context, page, state = setup(browser, width, pdf_bytes, quote=quote)
    name = name or f"pdf-{width}"
    try:
        drawer = open_detail(page)
        assert not state["downloads"], "Preview downloaded before explicit opening"
        assert not state["workers"], "PDF worker started before explicit opening"
        preview = open_pdf(drawer)
        expect(preview).to_contain_text("1 / 2", timeout=15000)
        rendered(preview)
        first = canvas_pixels(page)
        assert first["blue"] > 500 and first["red"] == 0, first
        search = preview.get_by_role("searchbox", name="当前页查找")
        search.fill(quote)
        expect(preview).to_contain_text("定位到唯一匹配")
        mark = preview.locator(".pdf-text-layer mark")
        expect(mark).to_have_text(quote)
        # Verify overlay coordinates cover actual glyph pixels, and text is selectable.
        mapped = mark.evaluate("""mark => {
          const canvas = document.querySelector('.pdf-source-preview canvas');
          const bounds = canvas.getBoundingClientRect(), target = mark.getBoundingClientRect();
          const x = Math.max(0, Math.floor(target.x - bounds.x)), y = Math.max(0, Math.floor(target.y - bounds.y));
          const {data} = canvas.getContext('2d').getImageData(x, y, Math.ceil(target.width), Math.ceil(target.height));
          let ink = 0;
          for (let i = 0; i < data.length; i += 4) if (data[i] < 100 && data[i+1] < 100 && data[i+2] < 100) ink++;
          const range = document.createRange(); range.selectNodeContents(mark);
          getSelection().removeAllRanges(); getSelection().addRange(range);
          return {ink, selected: getSelection().toString(), width: target.width, height: target.height};
        }""")
        assert mapped["ink"] > 20 and mapped["selected"] == quote, mapped
        if quote == CHINESE_QUOTE:
            assert any('/pdfjs/cmaps/' in url for url in state['resource_requests']), state['resource_requests']
        search.fill("nonexistent exact quotation")
        expect(preview).to_contain_text("未能精确标注")
        expect(mark).to_have_count(0)
        search.fill(quote)
        preview.get_by_role("button", name="下一页", exact=True).click()
        expect(preview).to_contain_text("2 / 2")
        rendered(preview)
        second = canvas_pixels(page)
        assert second["red"] > 500 and second["blue"] == 0, second
        for _ in range(4):
            preview.get_by_role("button", name="放大", exact=True).click()
        rendered(preview)
        zoomed = canvas_pixels(page)
        assert zoomed["width"] >= second["width"] * 1.99 and zoomed["visible_width"] > second["visible_width"], zoomed
        expect(preview.get_by_role("button", name="放大", exact=True)).to_be_disabled()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        preview.get_by_role("button", name="适应宽度", exact=True).click()
        rendered(preview)
        assert canvas_pixels(page)["width"] == second["width"]
        # Force rendering to start between requests so this exercises real cancellation.
        page.evaluate("""async () => {
          const buttons = [...document.querySelectorAll('.pdf-preview-toolbar button')];
          for (let i = 0; i < 24; i++) {
            buttons[i % 2 ? 1 : 0].click();
            buttons[i % 2 ? 3 : 2].click();
            await new Promise(requestAnimationFrame);
          }
        }""")
        rendered(preview)
        expect(preview).to_contain_text("2 / 2")
        assert canvas_pixels(page)["red"] > 500
        preview.get_by_role("button", name="上一页", exact=True).click()
        preview.get_by_role("button", name="适应宽度", exact=True).click()
        rendered(preview)
        search.fill(quote)
        expect(preview.locator(".pdf-text-layer mark")).to_have_text(quote)
        page.screenshot(path=str(output / f"{name}.png"))
        drawer.get_by_role("button", name="收起 PDF 原页", exact=True).click()
        expect(preview).not_to_be_visible()
        page.wait_for_function("window.pdfPreviewAborts >= 1")
        page.wait_for_timeout(100)
        assert len(state["closed_workers"]) == len(state["workers"]) >= 1, state
        preview = open_pdf(drawer)
        expect(preview).to_contain_text("1 / 2")
        rendered(preview)
        assert canvas_pixels(page)["blue"] > 500
        page.keyboard.press("Escape")
        expect(drawer).not_to_be_visible()
        expect(page.get_by_role("button", name="查看详情", exact=True)).to_be_focused()
        return {"width": width, "first": first, "second": second, "zoom_200": zoomed,
            "text_mapping": mapped, **clean_state(state)}
    except Exception:
        capture_failure(page, state, output, name)
        raise
    finally:
        context.close()


def run_variant(browser, output, name, bytes_, message, headers=None, status=200):
    context, page, state = setup(browser, 390, bytes_, headers=headers, status=status, worker_failure=name == "worker-failure")
    try:
        drawer = open_detail(page)
        preview = open_pdf(drawer)
        if name in ("scanned", "duplicate"):
            expect(preview).to_contain_text("1 /", timeout=15000)
            rendered(preview)
            assert canvas_pixels(page)["blue"] > 500
            preview.get_by_role("searchbox", name="当前页查找").fill(QUOTE)
            expect(preview).to_contain_text(message)
            expect(preview.locator(".pdf-text-layer mark")).to_have_count(0)
        else:
            expect(preview).to_contain_text(message, timeout=15000)
            expect(preview.get_by_role("alert")).to_be_visible()
            expect(preview.locator("canvas")).not_to_be_visible()
            expect(drawer.locator(".detail-preview")).to_contain_text(QUOTE)
            expect(drawer.get_by_role("button", name="下载原文件", exact=True)).to_be_enabled()
            # Retry with valid authorized bytes after the failing attempt.
            page.unroute("**/*pdf.worker*.mjs*")
            page.route("**/api/knowledge/records/real-pdf/download**", lambda route: route.fulfill(body=make_pdf(), content_type="application/pdf"))
            preview.get_by_role("button", name="重试 PDF 预览", exact=True).click()
            expect(preview).to_contain_text("1 / 2")
            rendered(preview)
            assert canvas_pixels(page)["blue"] > 500
        page.screenshot(path=str(output / f"{name}.png"))
        return {"name": name, **clean_state(state, allow_console_errors=status != 200 or name == "worker-failure",
            worker_failure=name == "worker-failure", cancelled_download=name == 'byte-limit')}
    except Exception:
        capture_failure(page, state, output, name)
        raise
    finally:
        context.close()


def run_delayed_close(browser, output):
    context, page, state = setup(browser, 390, make_pdf(), delayed=True)
    try:
        drawer = open_detail(page)
        preview = open_pdf(drawer)
        expect(preview).to_contain_text("正在加载 PDF")
        page.wait_for_function("!!document.querySelector('.pdf-source-preview')")
        page.keyboard.press("Escape")
        expect(drawer).not_to_be_visible()
        page.wait_for_function("window.pdfPreviewAborts === 1")
        assert state["pending"], "No download was started"
        for route in state["pending"]:
            route.fulfill(body=make_pdf(), content_type="application/pdf")
        page.get_by_role("button", name="查看详情", exact=True).click()
        expect(drawer.get_by_role("region", name="PDF 原页预览")).to_have_count(0)
        page.route("**/api/knowledge/records/real-pdf/download**", lambda route: route.fulfill(body=make_pdf(), content_type="application/pdf"))
        preview = open_pdf(drawer)
        expect(preview).to_contain_text("1 / 2")
        rendered(preview)
        return {"name": "close-during-download", **clean_state(state, cancelled_download=True)}
    except Exception:
        capture_failure(page, state, output, 'close-during-download')
        raise
    finally:
        context.close()


def run_citation_target(browser, output, target_page):
    context, page, state = setup(browser, 390, make_pdf())
    try:
        result = {"type": "result", "content": "Manager approval is required. [C1]", "citations": [
            {"citation_id": "C1", "source_id": "real-pdf", "title": "合成差旅制度", "quote": QUOTE, "page": target_page}
        ], "trace_id": "pdf-citation", "failure_type": "none", "turn_id": "pdf-turn"}
        body = "data: " + json.dumps(result) + "\n\ndata: {\"type\":\"done\",\"trace_id\":\"pdf-citation\"}\n\n"
        page.route("**/api/chat/stream", lambda route: route.fulfill(body=body, content_type="text/event-stream"))
        page.route("**/api/knowledge/records/real-pdf/preview**", lambda route: route.fulfill(json={
            "source_id": "real-pdf", "revision_match": "unknown", "original_available": True,
            "blocks": [{"id": "block", "text": QUOTE, "page": target_page, "section": None}],
            "location": {"status": "exact", "block_id": "block", "page": target_page, "start": 0, "end": len(QUOTE)},
        }))
        page.goto(BASE_URL + "/chat", wait_until="domcontentloaded")
        page.get_by_role("textbox", name="向企业知识库提问").fill("差旅审批要求？")
        page.get_by_role("button", name="发送", exact=True).click()
        page.locator(".citation-row button").first.click()
        page.get_by_role("button", name="查看文档信息与下载").click()
        drawer = page.get_by_role("dialog", name="文档详情", exact=True)
        expect(drawer).to_contain_text("已定位到引用原文")
        preview = open_pdf(drawer)
        expected_page = target_page if 1 <= target_page <= 2 else 1
        expect(preview).to_contain_text(f"{expected_page} / 2", timeout=15000)
        rendered(preview)
        expect(preview.locator(".pdf-text-layer mark")).to_have_text(QUOTE)
        if expected_page != target_page:
            expect(preview).to_contain_text("引用页码超出原文件范围")
        assert canvas_pixels(page)["red" if expected_page == 2 else "blue"] > 500
        return {"name": "citation-target", "target": target_page, "opened": expected_page,
            **clean_state(state, completed_stream=True)}
    except Exception:
        capture_failure(page, state, output, f'citation-page-{target_page}')
        raise
    finally:
        context.close()


def main():
    global BASE_URL
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', default=BASE_URL)
    parser.add_argument('--browsers', nargs='+', choices=['chrome', 'msedge', 'firefox', 'webkit'], default=['chrome'])
    parser.add_argument('--scenario', action='append', help='Run only these named scenarios (repeatable).')
    options = parser.parse_args()
    BASE_URL = options.base_url.rstrip('/')
    output = ROOT / "work" / "pdf-source-preview" / datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    output.mkdir(parents=True)
    bytes_ = make_pdf()
    (output / "synthetic-policy.pdf").write_bytes(bytes_)
    report = {"base_url": BASE_URL, "mocked_api": True, "actual_pdfjs_rendering": True, "checks": []}
    with sync_playwright() as playwright:
        for browser_name in options.browsers:
            browser = playwright.chromium.launch(channel=browser_name) if browser_name in ('chrome', 'msedge') else getattr(playwright, browser_name).launch()
            browser_output = output / browser_name
            browser_output.mkdir()
            scenarios = [(f"render-{width}", lambda width=width: run_standard(browser, browser_output, bytes_, width)) for width in (1440, 390, 320)]
            scenarios += [('chinese', lambda: run_standard(browser, browser_output, make_pdf('chinese'), 390,
                quote=CHINESE_QUOTE, name='chinese'))]
            variants = [
                ("scanned", make_pdf("scanned"), "未提取到可定位文本", None, 200),
                ("duplicate", make_pdf("duplicate"), "当前页存在多处匹配", None, 200),
                ("corrupt", b"invalid pdf data", "PDF 无法加载", None, 200),
                ("encrypted", make_pdf("encrypted"), "PDF 已加密", None, 200),
                ("byte-limit", bytes_, "PDF 文件超过预览上限", {"X-Pdf-Preview-Max-Bytes": "8"}, 200),
                ("page-limit", bytes_, "PDF 页数超过预览上限", {"X-Pdf-Preview-Max-Pages": "1"}, 200),
                ("worker-failure", bytes_, "PDF 无法加载", None, 200),
                ("forbidden", b"", "无权预览", None, 403),
                ("missing", b"", "来源文件不存在", None, 404),
            ]
            scenarios += [(variant[0], lambda variant=variant: run_variant(browser, browser_output, *variant)) for variant in variants]
            scenarios += [("close-during-download", lambda: run_delayed_close(browser, browser_output))]
            scenarios += [(f"citation-page-{target}", lambda target=target: run_citation_target(browser, browser_output, target)) for target in (2, 999)]
            if options.scenario:
                names = {name for name, _ in scenarios}
                if any(name not in names for name in options.scenario):
                    browser.close()
                    parser.error('Unknown scenario; available: ' + ', '.join(sorted(names)))
                scenarios = [item for item in scenarios if item[0] in options.scenario]
            try:
                for name, run in scenarios:
                    print(f"START {browser_name} {name}", flush=True)
                    row = {"browser": browser_name, "version": browser.version, "name": name, "status": "passed"}
                    try:
                        row['detail'] = run()
                    except Exception as error:
                        row.update(status='failed', error=str(error))
                    report['checks'].append(row)
                    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
                    print(browser_name, name, row['status'], flush=True)
            finally:
                browser.close()
    passed = sum(row['status'] == 'passed' for row in report['checks'])
    print(f"PDF preview {passed}/{len(report['checks'])} passed. Evidence: {output}")
    return int(passed != len(report['checks']))


if __name__ == "__main__":
    raise SystemExit(main())
