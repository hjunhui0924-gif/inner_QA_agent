"""Live Vue -> Vite proxy -> FastAPI -> hosted models acceptance.

Run explicitly: python tests/e2e_live_backend.py --live
Uses isolated SQLite, Chroma, uploads and a small synthetic knowledge base.
No browser routes or backend/model functions are mocked. Requires unused ports
8000/5173 and configured model credentials; consumes hosted model quota.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import httpx
from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
WEB = "http://127.0.0.1:5173"
POLICY = """# 接口联调差旅报销制度

## 审批规则
单笔差旅报销金额不超过5000元的，由直属主管审批。
单笔差旅报销金额超过5000元的，由直属主管审批后，再由财务负责人复核。

## 所需材料
差旅报销需提交发票、行程单和审批记录。

## 提交时限
出差结束后10个工作日内提交报销申请。
"""


def wait_ready(url: str, process: subprocess.Popen, timeout: float = 60) -> None:
    deadline = time.monotonic() + timeout
    with httpx.Client(timeout=1) as client:
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError(f"Service exited before readiness; inspect its log: {url}")
            try:
                if client.get(url).status_code == 200:
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.25)
    raise TimeoutError(f"Service did not become ready: {url}")


def run_live() -> int:
    for port in (8000, 5173):
        with socket.socket() as connection:
            if connection.connect_ex(("127.0.0.1", port)) == 0:
                raise RuntimeError(f"Port {port} is in use; stop that service before this isolated test.")
    run_dir = ROOT / "work" / "backend-verification" / datetime.now().strftime("live-%Y%m%d-%H%M%S")
    run_dir.mkdir(parents=True)
    runtime = run_dir / "runtime"
    runtime.mkdir()
    (runtime / "knowledge.json").write_text("[]", encoding="utf-8")
    environment = os.environ.copy()
    environment.update({
        "KNOWLEDGE_BASE_PATH": str(runtime / "knowledge.json"),
        "SQLITE_DB_PATH": str(runtime / "memory.db"),
        "CHROMA_PERSIST_DIR": str(runtime / "chroma"),
        "UPLOAD_DIR": str(runtime / "uploads"),
        "TRACE_LOG_PATH": str(runtime / "trace.jsonl"),
        "TRACE_ENABLED": "true", "TRACE_INCLUDE_CONTENT": "false",
        "AUTH_MODE": "development", "AUTH_DEV_USER_ID": "user_001",
        "AUTH_DEV_ROLES": "employee,knowledge_reader,knowledge_admin",
        "AUTH_DEV_SCOPES": "internal", "AUTH_DEV_DEPARTMENTS": "Finance",
        "FRONTEND_ORIGINS": WEB, "VITE_API_BASE_URL": "/api",
    })
    processes: list[subprocess.Popen] = []
    logs = []
    report: dict = {"run_dir": str(run_dir), "mocked": False, "checks": [], "requests": [], "console_errors": [], "page_errors": [], "failed_requests": []}
    source_id = ""
    rag_session = ""
    sessions_created: set[str] = set()

    def check(name, function):
        print(f"START {name}", flush=True)
        started = time.monotonic()
        result = {"name": name}
        try:
            detail = function()
            result.update(status="passed", detail=detail)
        except Exception as error:
            result.update(status="failed", error=f"{type(error).__name__}: {str(error)[:1600]}")
        result["elapsed_seconds"] = round(time.monotonic() - started, 2)
        report["checks"].append(result)
        (run_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False), flush=True)
        return result["status"] == "passed"

    try:
        commands = [
            ("backend", [sys.executable, "-m", "uvicorn", "backend.main:app", "--host", "127.0.0.1", "--port", "8000"], ROOT),
            ("frontend", [shutil.which("node") or "node", str(ROOT / "frontend/node_modules/vite/bin/vite.js"), "--host", "127.0.0.1", "--port", "5173", "--strictPort"], ROOT / "frontend"),
        ]
        for name, command, directory in commands:
            log = (run_dir / f"{name}.log").open("w", encoding="utf-8")
            logs.append(log)
            process = subprocess.Popen(command, cwd=directory, env=environment, stdout=log, stderr=subprocess.STDOUT, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            processes.append(process)
        wait_ready("http://127.0.0.1:8000/health", processes[0])
        wait_ready(WEB, processes[1])
        print(f"Live services ready. Evidence: {run_dir}", flush=True)

        with sync_playwright() as playwright:
            executable = next(path for path in [Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"), Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe")] if path.exists())
            browser = playwright.chromium.launch(headless=True, executable_path=str(executable))
            context = browser.new_context(viewport={"width": 1440, "height": 900}, accept_downloads=True, reduced_motion="reduce")
            page = context.new_page()
            page.set_default_timeout(15000)
            page.on("console", lambda message: report["console_errors"].append(message.text) if message.type == "error" else None)
            page.on("pageerror", lambda error: report["page_errors"].append(str(error)))
            page.on("requestfailed", lambda request: report["failed_requests"].append({"url": request.url, "reason": request.failure}))
            page.on("response", lambda response: report["requests"].append({"method": response.request.method, "url": response.url, "status": response.status}) if "/api/" in response.url else None)
            api = context.request

            def send(question: str, expected: list[str] | None = None) -> dict:
                nonlocal rag_session
                page.locator("#question").fill(question)
                started = time.monotonic()
                with page.expect_response(lambda response: response.url.endswith("/api/chat/stream") and response.request.method == "POST", timeout=90000) as response_info:
                    page.get_by_role("button", name="发送", exact=True).click()
                response = response_info.value
                assert response.status == 200, response.status
                payload = response.request.post_data_json
                sessions_created.add(payload["session_id"])
                if payload["mode"] == "knowledge":
                    rag_session = payload["session_id"]
                events = [json.loads(line[6:]) for line in response.text().splitlines() if line.startswith("data: ")]
                results = [event for event in events if event.get("type") == "result"]
                assert len(results) == 1, f"Expected one authoritative result; event types: {[e.get('type') for e in events]}"
                result = results[0]
                detail = {
                    "session_id": payload["session_id"], "mode": payload["mode"], "web_search": payload["web_search"],
                    "answer": result["content"], "citation_count": len(result.get("citations", [])),
                    "failure_type": result.get("failure_type"), "failure_stage": result.get("failure_stage"),
                    "nodes": list(dict.fromkeys(event.get("node", "") for event in events if event.get("type") == "status")),
                    "latency_seconds": round(time.monotonic() - started, 2),
                    "budget": result.get("budget_snapshot"),
                }
                report.setdefault("answers", []).append(detail)
                assert any(event.get("type") == "done" for event in events), "missing done"
                expect(page.get_by_role("button", name="发送", exact=True)).to_be_visible()
                expect(page.locator("article.message.assistant").last.locator(".message-content")).not_to_be_empty()
                if expected:
                    assert result.get("failure_type") == "none", f"Answer fell back: {detail}"
                    assert all(text in result["content"] for text in expected), f"Expected {expected}; got {result['content']}"
                    for text in expected:
                        expect(page.locator("article.message.assistant").last).to_contain_text(text)
                return detail

            def initial():
                page.goto(f"{WEB}/chat", wait_until="domcontentloaded")
                expect(page.get_by_text("知识服务在线", exact=True)).to_be_visible()
                assert api.get(f"{WEB}/api/health").json()["status"] == "ok"
                assert api.get(f"{WEB}/api/knowledge/records").json()["items"] == []
                return "真实 Vite /api 代理、健康状态、会话列表、空知识库正常"

            check("页面初始化与真实代理", initial)

            def upload():
                nonlocal source_id
                page.get_by_role("link", name="知识库", exact=True).click()
                page.get_by_role("button", name="添加文档", exact=True).click()
                page.locator("#knowledge-file").set_input_files({"name": "live-travel-policy.md", "mimeType": "text/markdown", "buffer": POLICY.encode("utf-8")})
                page.locator(".upload-options summary").click()
                page.get_by_label("知识标题", exact=True).fill("接口联调差旅报销制度")
                page.locator('select[name="document-department"]').select_option("Finance")
                page.get_by_label("负责人", exact=True).fill("联调测试")
                with page.expect_response(lambda response: response.url.endswith("/api/knowledge/upload"), timeout=90000) as response_info:
                    page.get_by_role("button", name="上传文档", exact=True).click()
                response = response_info.value
                assert response.status == 200, response.text()
                payload = response.json()
                source_id = payload["record"].get("source_id") or payload["record"].get("id")
                expect(page.get_by_role("heading", name="文件已完成入库", exact=True)).to_be_visible()
                page.get_by_role("button", name="关闭添加文档", exact=True).click()
                expect(page.get_by_text("接口联调差旅报销制度", exact=True)).to_be_visible()
                record = api.get(f"{WEB}/api/knowledge/records").json()["items"][0]
                source_id = record["source_id"]
                assert record["department"] == "Finance"
                return {"source_id": source_id, "department": record["department"], "title": record["title"]}

            uploaded = check("前端上传、在线向量入库与列表刷新", upload)

            def document_operations():
                page.get_by_label("搜索文档", exact=True).fill("接口联调")
                expect(page.locator(".document-row")).to_have_count(1)
                page.get_by_role("button", name="查看详情", exact=True).click()
                dialog = page.get_by_role("dialog", name="文档详情", exact=True)
                expect(dialog).to_contain_text("接口联调差旅报销制度")
                expect(dialog.locator(".detail-preview")).to_contain_text("财务负责人")
                detail = api.get(f"{WEB}/api/knowledge/records/{source_id}")
                assert detail.status == 200
                with page.expect_download() as download_info:
                    dialog.get_by_role("button", name="下载原文件", exact=True).click()
                download = download_info.value
                path = download.path()
                assert path and Path(path).read_bytes() == POLICY.encode("utf-8")
                dialog.get_by_role("button", name="关闭文档详情", exact=True).click()
                page.screenshot(path=str(run_dir / "knowledge.png"))
                return {"downloaded_filename": download.suggested_filename, "content_matches_upload": True}

            if uploaded:
                check("搜索、详情接口与原文件下载", document_operations)

                def duplicate():
                    page.get_by_role("button", name="添加文档", exact=True).click()
                    page.locator("#knowledge-file").set_input_files({"name": "live-travel-policy.md", "mimeType": "text/markdown", "buffer": POLICY.encode("utf-8")})
                    with page.expect_response(lambda response: response.url.endswith("/api/knowledge/upload")) as response_info:
                        page.get_by_role("button", name="上传文档", exact=True).click()
                    payload = response_info.value.json()
                    assert payload["record"]["deduplicated"] is True
                    expect(page.get_by_role("heading", name="检测到重复文件，未重复入库", exact=True)).to_be_visible()
                    page.get_by_role("button", name="关闭添加文档", exact=True).click()
                    assert len(api.get(f"{WEB}/api/knowledge/records").json()["items"]) == 1
                    return "重复文件识别正常，仅保留一条知识记录"

                check("重复上传去重反馈", duplicate)

                def rag():
                    nonlocal rag_session
                    page.get_by_role("link", name="对话", exact=True).click()
                    detail = send("根据接口联调差旅报销制度，单笔差旅报销金额超过5000元需要谁审批？", ["直属主管", "财务负责人"])
                    rag_session = detail["session_id"]
                    assert detail["citation_count"] > 0, "No citations for knowledge answer"
                    page.get_by_role("button", name="打开或隐藏引用证据").click()
                    expect(page.get_by_role("complementary", name="引用证据")).to_contain_text("财务负责人")
                    page.screenshot(path=str(run_dir / "rag-answer.png"))
                    page.get_by_role("button", name="关闭引用证据", exact=True).click()
                    return detail

                rag_ok = check("真实 RAG 流式回答与前端引用", rag)
                if rag_session:
                    check("多轮追问", lambda: send("需要提交哪些材料？", ["发票", "行程单", "审批记录"]))

                    def history():
                        messages = api.get(f"{WEB}/api/chat/history/user_001/{rag_session}").json()["items"]
                        assert len(messages) >= 4
                        citations_persisted = any(item.get("citations") for item in messages if item["role"] == "assistant")
                        if rag_ok:
                            assert citations_persisted
                        sessions = api.get(f"{WEB}/api/chat/sessions/user_001").json()["items"]
                        title = next(item["title"] for item in sessions if item["session_id"] == rag_session)
                        page.reload(wait_until="domcontentloaded")
                        page.locator(".session-row").filter(has_text=title).click()
                        expect(page.locator("article.message.assistant")).to_have_count(2)
                        if citations_persisted:
                            expect(page.get_by_role("button", name="打开或隐藏引用证据")).to_be_visible()
                        else:
                            expect(page.locator("article.message.assistant").last).to_contain_text(messages[-1]["content"][:15])
                        return {"persisted_messages": len(messages), "generated_title": title, "history_citations": citations_persisted}

                    check("标题、会话持久化与刷新恢复", history)

                    def no_answer():
                        detail = send("根据这份差旅制度，餐补每天可以报销多少元？")
                        assert detail["citation_count"] == 0
                        assert detail["failure_type"] == "retrieval_miss", detail
                        assert any(word in detail["answer"] for word in ["未包含", "不足", "没有"]), detail
                        return detail

                    check("知识不足时拒答且不伪造引用", no_answer)

            def general():
                page.goto(f"{WEB}/chat", wait_until="domcontentloaded")
                page.get_by_role("button", name="开启新对话", exact=True).click()
                page.get_by_role("button", name="通用模式", exact=True).click()
                return send("请用一句话解释什么是项目里程碑。", ["里程碑"])

            check("通用模式真实模型回答", general)

            def web_search():
                page.get_by_role("button", name="开启新对话", exact=True).click()
                page.get_by_role("button", name="通用模式", exact=True).click()
                page.get_by_role("button", name="联网搜索", exact=True).click()
                detail = send("联网搜索 Python programming language，并说明它是什么。")
                assert detail["web_search"] is True
                assert "tool_executor" in detail["nodes"], "Search tool was not executed"
                assert detail["failure_type"] == "none", f"Web search degraded: {detail}"
                assert "Python" in detail["answer"] and "编程" in detail["answer"], f"Search did not answer the question: {detail}"
                assert detail["citation_count"] > 0
                page.get_by_role("button", name="打开或隐藏引用证据").click()
                panel = page.get_by_role("complementary", name="引用证据")
                links = panel.get_by_role("link", name="打开网页来源")
                expect(links).to_have_count(detail["citation_count"])
                for link in links.all():
                    assert link.get_attribute("href").startswith(("https://", "http://"))
                    assert link.get_attribute("rel") == "noopener noreferrer"
                expect(panel).to_contain_text("搜索服务提供的来源")
                expect(panel.get_by_role("button", name="复制原文")).to_have_count(0)
                page.screenshot(path=str(run_dir / "web-answer.png"))
                history = api.get(f"{WEB}/api/chat/history/user_001/{detail['session_id']}").json()["items"]
                assert all(item.get("url") for item in history[-1]["citations"])
                page.reload(wait_until="domcontentloaded")
                sessions = api.get(f"{WEB}/api/chat/sessions/user_001").json()["items"]
                title = next(item["title"] for item in sessions if item["session_id"] == detail["session_id"])
                page.locator(".session-row").filter(has_text=title).click()
                page.get_by_role("button", name="打开或隐藏引用证据").click()
                expect(page.get_by_role("complementary", name="引用证据").get_by_role("link", name="打开网页来源")).to_have_count(detail["citation_count"])
                page.get_by_role("button", name="关闭引用证据", exact=True).click()
                return detail

            check("联网搜索开关与真实工具调用", web_search)

            def search_provider():
                # Check the actual provider response as well as the frontend tool trigger.
                sys.path.insert(0, str(ROOT))
                from backend.agent.tools import search_web_result
                result = search_web_result("联网搜索 Python programming language，并说明它是什么。")
                report["search_provider"] = result
                assert result["ok"] and result["sources"], f"Search returned no verifiable sources: {result}"
                return result

            check("在线搜索结果与来源可用性", search_provider)

            def delete_sessions():
                if rag_session:
                    page.reload(wait_until="domcontentloaded")
                    sessions = api.get(f"{WEB}/api/chat/sessions/user_001").json()["items"]
                    title = next(item["title"] for item in sessions if item["session_id"] == rag_session)
                    page.get_by_role("button", name=f"删除会话：{title}", exact=True).click()
                    page.get_by_role("button", name="确认删除", exact=True).click()
                    expect(page.get_by_role("dialog", name="彻底删除这个会话？")).to_have_count(0)
                    assert api.get(f"{WEB}/api/chat/history/user_001/{rag_session}").json()["items"] == []
                for session_id in sessions_created:
                    response = api.delete(f"{WEB}/api/chat/session/user_001/{session_id}")
                    assert response.status == 200
                assert api.get(f"{WEB}/api/chat/sessions/user_001").json()["items"] == []
                return "界面删除、历史清空及幂等删除正常"

            check("会话删除与清理", delete_sessions)

            def negative_contracts():
                wrong_user = api.get(f"{WEB}/api/chat/sessions/another-user")
                empty_message = api.post(f"{WEB}/api/chat/stream", data={"message": ""})
                unknown_source = api.get(f"{WEB}/api/knowledge/records/missing-source/download")
                assert wrong_user.status == 403, wrong_user.status
                assert empty_message.status == 422, empty_message.status
                assert unknown_source.status == 404, unknown_source.status
                return {"other_user": 403, "invalid_question": 422, "missing_download": 404}

            check("异常请求与权限拒绝", negative_contracts)
            check("浏览器控制台与网络错误", lambda: (
                "无异常" if not any(report[key] for key in ["console_errors", "page_errors", "failed_requests"])
                else (_ for _ in ()).throw(AssertionError({key: report[key] for key in ["console_errors", "page_errors", "failed_requests"]}))
            ))
            context.close()
            browser.close()
    finally:
        for process in reversed(processes):
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
        for log in logs:
            log.close()
        (run_dir / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Report: {run_dir / 'report.json'}", flush=True)
    return 1 if any(check["status"] == "failed" for check in report["checks"]) else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Explicitly enable hosted model calls")
    arguments = parser.parse_args()
    if not arguments.live:
        parser.error("--live is required because this test consumes hosted model quota")
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(run_live())
