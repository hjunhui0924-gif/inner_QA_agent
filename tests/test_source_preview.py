import pytest

from backend.agent.source_preview import (
    build_preview_blocks,
    build_window,
    decode_cursor,
    encode_cursor,
    locate_quote,
    page_window,
    previous_window_offset,
)


def test_locates_exact_quote_with_codepoint_offsets_and_page():
    blocks = build_preview_blocks("第一段制度。\n\n第二段包含🙂引用。", [{"text": "第一段制度。", "page": 2}, {"text": "第二段包含🙂引用。", "page": 3, "section": "审批"}])
    result = locate_quote(blocks, "包含🙂引用")
    assert result["status"] == "exact"
    assert result["page"] == 3
    assert result["section"] == "审批"
    assert result["start"] is not None and result["end"] is not None


def test_normalizes_whitespace_and_marks_duplicate_quotes_ambiguous():
    blocks = build_preview_blocks("同一句\n内容。\n\n同一句\t内容。")
    assert locate_quote(blocks, "同一句 内容")["status"] == "ambiguous"
    unique = build_preview_blocks("标题\n\n有  多行\n文本")
    result = locate_quote(unique, "有 多行 文本")
    assert result["status"] == "normalized"


def test_build_window_centers_target_and_missing_quotes_are_explicit():
    blocks = build_preview_blocks("\n\n".join(f"段落 {i}" for i in range(20)))
    window = build_window(blocks, "block-10-0")
    assert window[0]["id"] == "block-8-0"
    assert len(window) <= 12
    assert locate_quote(blocks, "不存在")["status"] == "not_found"


def test_cursor_binds_source_and_checksum_and_pages_blocks():
    blocks = build_preview_blocks("\n\n".join(f"段落 {i}" for i in range(20)))
    cursor = encode_cursor("source-1", "checksum-1", 12, key="test-key")
    assert page_window(blocks, decode_cursor(cursor, "source-1", "checksum-1", key="test-key"))[0]["id"] == "block-12-0"
    with pytest.raises(ValueError):
        decode_cursor(cursor, "source-2", "checksum-1", key="test-key")


def test_long_documents_remain_addressable_and_limits_apply_per_window():
    blocks = build_preview_blocks("", [{"text": (f"段{i}🙂" + "甲" * 3996), "page": i + 1} for i in range(20)])
    assert len(blocks) > 6
    target = locate_quote(blocks, "段19🙂")
    assert target["status"] == "exact" and target["page"] == 20
    window = build_window(blocks, target["block_id"])
    assert any(block["id"] == target["block_id"] for block in window)
    offset = 0
    visited = []
    while offset < len(blocks):
        current = page_window(blocks, offset)
        assert 0 < len(current) <= 12
        assert sum(len(block["text"]) for block in current) <= 24000
        assert all(len(block["text"]) <= 4000 for block in current)
        visited += current
        end = offset + len(current)
        if end < len(blocks):
            previous = previous_window_offset(blocks, end)
            assert previous + len(page_window(blocks, previous)) == end
        offset = end
    assert visited == blocks


def test_empty_quote_and_split_quote_do_not_invent_a_location():
    blocks = build_preview_blocks("前言🙂\n\n经理审批")
    assert locate_quote(blocks, "")["status"] == "unavailable"
    assert locate_quote(blocks, "前言🙂经理审批")["status"] == "not_found"
