"""Guard against accepting answers tied to wrong or changed source material."""
import hashlib
import json
from pathlib import Path

import pytest

from backend.knowledge.schema import normalize_knowledge_record
from scripts.run_knowledge_quality_audit import load_fixture, precheck


RAW_RECORD = {"id": "policy-v2", "title": "报销制度", "version": "v2",
              "content": "单笔超过5000元须由财务负责人复核。"}
CASE = {"case_id": "amount", "session": "amount", "question": "6000元谁复核？",
        "allowed_sources": ["policy-v2"], "required_sources": ["policy-v2"],
        "required": [["财务负责人"]], "evidence": [
            {"source_id": "policy-v2", "quote": "超过5000元须由财务负责人复核"}]}


def write_fixture(tmp_path, **updates):
    corpus = tmp_path / "knowledge.json"
    corpus.write_text(json.dumps([RAW_RECORD], ensure_ascii=False), encoding="utf-8")
    fixture = {"corpus_path": str(corpus), "corpus_sha256": hashlib.sha256(corpus.read_bytes()).hexdigest(),
               "cases": [{**CASE, **updates}]}
    path = tmp_path / "cases.json"
    path.write_text(json.dumps(fixture, ensure_ascii=False), encoding="utf-8")
    return path, corpus


def test_rejects_corpus_drift_before_service_calls(tmp_path):
    path, corpus = write_fixture(tmp_path)
    corpus.write_text(json.dumps([{**RAW_RECORD, "content": "已变更为10000元。"}]), encoding="utf-8")
    with pytest.raises(ValueError, match="Corpus changed"):
        load_fixture(path)


@pytest.mark.parametrize("updates, reason", [
    ({"evidence": [{"source_id": "policy-v2", "quote": "超过10000元"}]}, "evidence absent"),
    ({"allowed_sources": ["unknown-source"]}, "Invalid expected sources"),
    ({"required_sources": ["other-policy"]}, "Required sources outside"),
    ({"session": "../outside"}, "session identifier"),
])
def test_rejects_inconsistent_expectations(tmp_path, updates, reason):
    path, _ = write_fixture(tmp_path, **updates)
    with pytest.raises(ValueError, match=reason):
        load_fixture(path)


def test_original_inline_audit_fixtures_still_load():
    for name in ("knowledge_quality_audit.json", "knowledge_version_followup_audit.json"):
        fixture, records = load_fixture(Path(__file__).parent / "fixtures" / name)
        assert records and fixture["cases"]


@pytest.mark.parametrize("field, value, reason", [
    ("version", "v1", "revision does not match"),
    ("source_content_checksum", "other-revision", "revision does not match"),
    ("quote", "超过10000元须由财务负责人复核。", "quote absent"),
    ("citation_id", "C2", "markers and citation records disagree"),
])
def test_cannot_pass_with_wrong_source_revision_or_quote(field, value, reason):
    record = normalize_knowledge_record(RAW_RECORD)
    citation = {"citation_id": "C1", "source_id": "policy-v2", "version": "v2",
                "source_content_checksum": record["content_checksum"], "quote": RAW_RECORD["content"]}
    result = {"content": "财务负责人复核。[C1]", "failure_type": "none",
              "citations": [{**citation, field: value}]}
    assert any(reason in failure for failure in precheck(CASE, result, [record]))


def test_multiple_document_answer_requires_all_expected_sources():
    record = normalize_knowledge_record(RAW_RECORD)
    case = {**CASE, "allowed_sources": ["policy-v2", "other-policy"],
            "required_sources": ["policy-v2", "other-policy"]}
    result = {"content": "财务负责人复核。[C1]", "failure_type": "none", "citations": [
        {"citation_id": "C1", "source_id": "policy-v2", "version": "v2",
         "source_content_checksum": record["content_checksum"], "quote": RAW_RECORD["content"]}]}
    assert "missing required citation source" in precheck(case, result, [record])


def test_correct_source_pointer_does_not_hide_missing_excerpt_fact():
    record = normalize_knowledge_record({**RAW_RECORD,
        "content": RAW_RECORD["content"] + "三单不一致时暂停付款。"})
    case = {**CASE, "required_quote_phrases": [{"source_id": "policy-v2", "quote": "暂停付款"}]}
    result = {"content": "财务负责人复核。[C1] 三单不一致时暂停付款。[C1]", "failure_type": "none",
        "citations": [{"citation_id": "C1", "source_id": "policy-v2", "version": "v2",
                       "source_content_checksum": record["content_checksum"], "quote": RAW_RECORD["content"]}]}
    assert "missing required quote phrase: 暂停付款" in precheck(case, result, [record])
