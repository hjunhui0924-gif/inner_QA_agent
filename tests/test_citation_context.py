"""Real long-policy excerpts must retain the conditions attached to amounts."""

import json
import re
from pathlib import Path

from backend.agent.citations import build_citations, validate_citation_structure
from backend.agent.memory import _chunk_documents_from_record
from backend.knowledge.chunking import split_text


def compact(text):
    return re.sub(r"\s+", "", text)


def travel_documents():
    records = json.loads((Path(__file__).parent / "fixtures/public_business_corpus.json")
                         .read_text(encoding="utf-8"))
    record = next(item for item in records if item["id"] == "swupl-travel-2024")
    return _chunk_documents_from_record(
        title=record["title"], content=record["content"], source=record["source"],
        document_id=record["id"], record_metadata=record,
    )


def test_every_city_external_allowance_excerpt_keeps_its_region_condition():
    documents = [doc for doc in travel_documents()
                 if "每人每天80元包干使用" in compact(doc.page_content)]
    assert documents
    answer = "出差人员到市外的出差地交通费，每人每天80元包干使用 [C1]。"
    for document in documents:
        citations = build_citations([document], answer, query="到重庆市外出差，交通费每天多少？")
        quote = compact(citations[0]["quote"])
        assert "出差人员到市外的出差地交通费" in quote
        assert "每人每天80元包干使用" in quote
        assert len(citations[0]["quote"]) <= 600
        assert validate_citation_structure(answer, [document], citations) == (True, "")


def test_cross_page_county_allowances_keep_their_shared_condition():
    documents = [doc for doc in travel_documents()
                 if "每人每天40元" in compact(doc.page_content)
                 and "每人每天60元" in compact(doc.page_content)]
    assert documents
    answer = ("到市内其他区县出差，区县城区所在地每人每天40元；"
              "延伸到乡镇及村社每人每天60元，不得重复报销 [C1]。")
    for document in documents:
        citations = build_citations([document], answer,
                                   query="市内其他区县城区、乡镇及村社的出差地交通费是多少？")
        quote = compact(citations[0]["quote"])
        assert "到市内其他区县出差" in quote
        assert "每人每天40元" in quote
        assert "延伸到乡镇及村社出差每人每天60元" in quote
        assert "不得重复报销" in quote
        assert validate_citation_structure(answer, [document], citations) == (True, "")


def test_pdf_soft_wrap_does_not_become_an_overlap_sentence_boundary():
    preceding = "资料核验完成后再报销。" * 9
    rule = "第十二条出差人员到市外的出差地交通费，按照重庆市财政\n局发布的标准，每人每天80元包干使用；"
    source = preceding + rule + "其他交通费依制度办理。" * 12
    chunks = split_text(source, chunk_size=180, overlap=35)
    amount_chunks = [chunk for chunk in chunks if "每人每天80元包干使用" in compact(chunk)]
    assert amount_chunks
    assert all("出差人员到市外" in compact(chunk) for chunk in amount_chunks)
    assert all(len(chunk) <= 180 for chunk in chunks)


def test_long_unbroken_text_and_large_overlap_still_make_progress():
    source = "".join(chr(0x4e00 + index) for index in range(300))
    for overlap in (0, 10, 79):
        chunks = split_text(source, chunk_size=80, overlap=overlap)
        assert all(0 < len(chunk) <= 80 for chunk in chunks)
        assert all(character in "".join(chunks) for character in source)
        assert len(chunks) <= len(source)
