"""Streamlit UI for the enterprise knowledge agent."""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Iterator
from typing import Any

import requests
import streamlit as st


DEFAULT_BACKEND_URL = os.environ.get("BACKEND_URL", "http://localhost:8000")


def _init_session() -> None:
    """Initialize Streamlit session state."""

    if "session_id" not in st.session_state:
        st.session_state.session_id = str(uuid.uuid4())
    if "user_id" not in st.session_state:
        st.session_state.user_id = "user_001"
    if "messages" not in st.session_state:
        st.session_state.messages = []
    if "backend_url" not in st.session_state:
        st.session_state.backend_url = DEFAULT_BACKEND_URL
    if "knowledge_records" not in st.session_state:
        st.session_state.knowledge_records = []
    if "session_items" not in st.session_state:
        st.session_state.session_items = []


def _iter_sse_lines(response: requests.Response) -> Iterator[dict[str, object]]:
    """Yield parsed SSE payloads."""

    for line in response.iter_lines(decode_unicode=True):
        if not line or not line.startswith("data: "):
            continue
        payload = line.removeprefix("data: ").strip()
        try:
            data = json.loads(payload)
        except json.JSONDecodeError:
            continue
        yield data


def _load_session_list() -> None:
    """Load available chat sessions for the current user."""

    url = (
        f"{st.session_state.backend_url.rstrip('/')}/chat/sessions/"
        f"{st.session_state.user_id}"
    )
    response = requests.get(url, timeout=30)
    response.raise_for_status()
    payload = response.json()
    items = payload.get("items", [])
    st.session_state.session_items = items if isinstance(items, list) else []


def _load_chat_history(session_id: str) -> None:
    """Load message history for one session."""

    url = (
        f"{st.session_state.backend_url.rstrip('/')}/chat/history/"
        f"{st.session_state.user_id}/{session_id}"
    )
    response = requests.get(url, timeout=30)
    response.raise_for_status()
    payload = response.json()
    items = payload.get("items", [])
    st.session_state.messages = items if isinstance(items, list) else []
    st.session_state.session_id = session_id


def _start_new_session() -> None:
    """Create a new local session placeholder."""

    st.session_state.session_id = str(uuid.uuid4())
    st.session_state.messages = []


def _delete_session(session_id: str) -> None:
    """Delete one session from backend and reset current view if needed."""

    url = (
        f"{st.session_state.backend_url.rstrip('/')}/chat/session/"
        f"{st.session_state.user_id}/{session_id}"
    )
    response = requests.delete(url, timeout=30)
    response.raise_for_status()
    if st.session_state.session_id == session_id:
        _start_new_session()


def _send_message(message: str) -> None:
    """Send one chat message and stream the answer."""

    payload = {
        "message": message,
        "user_id": st.session_state.user_id,
        "session_id": st.session_state.session_id,
    }
    url = f"{st.session_state.backend_url.rstrip('/')}/chat/stream"

    assistant_text = ""
    status_placeholder = st.empty()
    with st.chat_message("assistant"):
        answer_placeholder = st.empty()
        with requests.post(url, json=payload, stream=True, timeout=120) as response:
            response.raise_for_status()
            for event in _iter_sse_lines(response):
                event_type = str(event.get("type", ""))
                if event_type == "status":
                    content = str(event.get("content", ""))
                    status_placeholder.info(f"状态：{content}")
                elif event_type == "token":
                    assistant_text += str(event.get("content", ""))
                    answer_placeholder.markdown(assistant_text)
                elif event_type == "done":
                    break

    status_placeholder.empty()
    if not assistant_text:
        assistant_text = "未返回有效内容。"
        with st.chat_message("assistant"):
            st.markdown(assistant_text)
    st.session_state.messages.append({"role": "assistant", "content": assistant_text})
    _load_session_list()


def _load_knowledge_records() -> None:
    """Load the indexed knowledge records."""

    url = f"{st.session_state.backend_url.rstrip('/')}/knowledge/records"
    response = requests.get(url, timeout=30)
    response.raise_for_status()
    payload = response.json()
    items = payload.get("items", [])
    st.session_state.knowledge_records = items if isinstance(items, list) else []


def _upload_knowledge_file(uploaded_file: Any, title: str, source: str) -> None:
    """Upload one file into the backend knowledge base."""

    url = f"{st.session_state.backend_url.rstrip('/')}/knowledge/upload"
    files = {
        "file": (
            uploaded_file.name,
            uploaded_file.getvalue(),
            uploaded_file.type or "application/octet-stream",
        )
    }
    data = {"title": title.strip(), "source": source.strip() or "upload"}
    response = requests.post(url, files=files, data=data, timeout=120)
    if not response.ok:
        try:
            payload = response.json()
            detail = payload.get("detail")
            if isinstance(detail, str) and detail.strip():
                raise RuntimeError(detail)
        except ValueError:
            pass
        response.raise_for_status()
    payload = response.json()
    record = payload.get("record", {})
    if bool(record.get("deduplicated")):
        dedup_type = str(record.get("dedup_type", "none"))
        if dedup_type == "exact":
            st.warning("检测到完全重复内容，系统已跳过入库。")
        elif dedup_type == "similar":
            similarity = record.get("similarity")
            if isinstance(similarity, (int, float)):
                st.warning(
                    f"检测到高相似内容，系统已跳过入库。相似度：{similarity:.3f}"
                )
            else:
                st.warning("检测到高相似内容，系统已跳过入库。")
    else:
        st.success("文件已上传并完成入库。")


def _render_chat_history() -> None:
    """Render chat history with native Streamlit chat components."""

    for message in st.session_state.messages:
        role = str(message.get("role", "assistant"))
        content = str(message.get("content", ""))
        with st.chat_message(role):
            st.markdown(content)


def main() -> None:
    """Render the app."""

    st.set_page_config(
        page_title="企业内部知识问答 Agent",
        page_icon="📚",
        layout="wide",
        menu_items={"About": "企业内部知识问答 Agent"},
    )
    _init_session()
    try:
        _load_session_list()
    except Exception:
        pass
    try:
        _load_knowledge_records()
    except Exception:
        pass

    st.title("企业内部知识问答 Agent")
    st.caption("支持内部制度、流程、审批、合同、财务、人事等企业内部知识问答")

    with st.sidebar:
        if st.button("新建会话", use_container_width=True):
            _start_new_session()
            st.rerun()

        with st.expander("历史会话", expanded=False):
            for item in st.session_state.session_items:
                session_id = str(item.get("session_id", ""))
                title = str(item.get("title", "新会话"))
                last_message = str(item.get("last_message", ""))
                label = title if len(title) <= 18 else f"{title[:18]}..."
                select_col, delete_col = st.columns([5, 1])
                with select_col:
                    if st.button(
                        label,
                        key=f"session_{session_id}",
                        use_container_width=True,
                    ):
                        _load_chat_history(session_id)
                        st.rerun()
                with delete_col:
                    if st.button("删", key=f"delete_{session_id}"):
                        try:
                            _delete_session(session_id)
                            _load_session_list()
                            st.rerun()
                        except Exception as exc:
                            st.error(f"删除会话失败：{exc}")
                if last_message:
                    st.caption(last_message[:40])

        with st.expander("调试与会话设置", expanded=False):
            previous_user_id = st.session_state.user_id
            st.text_input("后端地址", key="backend_url")
            st.text_input("用户 ID", key="user_id")
            st.caption("当前会话 ID")
            st.code(st.session_state.session_id)
            if st.session_state.user_id != previous_user_id:
                st.session_state.session_items = []
                st.session_state.messages = []
                st.session_state.session_id = str(uuid.uuid4())

        st.divider()
        st.subheader("知识库管理")
        title = st.text_input("知识条目标题")
        source = st.text_input("来源标签", value="internal_upload")
        uploaded_file = st.file_uploader(
            "上传内部文件",
            type=["txt", "md", "csv", "json", "pdf", "docx", "py", "log"],
            help="上传后会自动解析文本并写入知识库及向量检索库。",
        )
        if st.button("上传并入库", disabled=uploaded_file is None):
            try:
                _upload_knowledge_file(uploaded_file, title, source)
                _load_knowledge_records()
            except Exception as exc:
                st.error(f"上传失败：{exc}")

        with st.expander("已入库文件", expanded=False):
            if st.session_state.knowledge_records:
                for record in st.session_state.knowledge_records[:10]:
                    filename = str(record.get("original_filename", "")).strip()
                    title_text = str(record.get("title", "未命名条目")).strip()
                    source_text = str(record.get("source", "unknown")).strip()
                    display_name = filename or title_text
                    st.markdown(f"- {display_name}")
                    st.caption(f"标题：{title_text} | 来源：{source_text}")
            else:
                st.caption("当前没有已入库文件。")

    _render_chat_history()

    prompt = st.chat_input("请输入企业内部问题")
    if prompt:
        st.session_state.messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)
        _send_message(prompt)


if __name__ == "__main__":
    main()
