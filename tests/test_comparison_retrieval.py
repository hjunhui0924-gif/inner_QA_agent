"""Comparisons must retrieve the requested clauses within the named owners."""
from langchain_core.documents import Document

from backend.retrieval.engine import RetrievalConfig, RetrievalEngine, RetrievalFilter
from backend.retrieval.reranker import RerankScore


QUERY = '只比较甲大学2024年与乙大学2024年两份文件：邀请专家来校访问，交通费要求一样吗？'
TASK = '邀请专家来校访问，交通费要求一样吗？'


def document(source, owner, content):
    return Document(page_content=content, metadata={
        'source_id': source, 'document_id': source, 'chunk_id': source,
        'title': owner + '差旅管理文件', 'owner': owner,
        'version': 'file-2024',
    })


DOCS = [
    document('a-cover', '甲大学', '甲大学2024年差旅管理文件通知。'),
    document('a-clause', '甲大学', '邀请专家来校访问，按规定报销交通费。'),
    document('b-cover', '乙大学', '乙大学2024年差旅管理文件通知。'),
    document('b-clause', '乙大学', '邀请专家来校访问，一般不报销交通费，确需报销应审批。'),
    document('other-clause', '丙大学', '邀请专家来校访问，无条件报销所有交通费。'),
]


class DenseStore:
    def __init__(self, documents):
        self.documents = documents

    def similarity_search(self, query, k=4, **kwargs):
        self.query = query
        return self.documents[:k]


class RecordedRanking:
    """Replay the observed preference for covers when scope fills the query."""
    def rerank(self, query, documents, top_n):
        self.query = query
        self.sources = [d.metadata['source_id'] for d in documents]
        preference = ['a-clause', 'other-clause', 'b-clause', 'a-cover', 'b-cover'] if query == TASK else [
            'a-cover', 'a-clause', 'b-cover', 'other-clause', 'b-clause']
        return sorted([
            RerankScore(i, 1.0 - preference.index(d.metadata['source_id']) / 10)
            for i, d in enumerate(documents)
        ], key=lambda score: -score.score)[:top_n]


def engine(documents=None):
    documents = DOCS if documents is None else documents
    store = DenseStore(documents)
    reranker = RecordedRanking()
    return RetrievalEngine(store, documents, config=RetrievalConfig(
        dense_candidate_k=5, lexical_candidate_k=5, rerank_candidate_k=5,
    ), reranker=reranker), store, reranker


def test_named_comparison_retrieves_both_clauses_without_other_institution():
    retriever, store, reranker = engine()
    result = retriever.retrieve(QUERY, top_k=2)
    assert {d.metadata['source_id'] for d in result.documents} == {'a-clause', 'b-clause'}
    assert store.query == QUERY  # Candidate search retains names and years.
    assert reranker.query == TASK
    assert 'other-clause' not in reranker.sources


def test_partial_owner_match_keeps_the_original_question_and_pool():
    retriever, _, reranker = engine()
    query = QUERY.replace('乙大学', '不存在的大学')
    retriever.retrieve(query, top_k=2)
    assert reranker.query == query
    assert 'other-clause' in reranker.sources


def test_comparison_scope_cannot_restore_a_source_excluded_by_access_filter():
    retriever, _, reranker = engine()
    result = retriever.retrieve(QUERY, top_k=2, filters=RetrievalFilter(
        allowed_document_ids={'a-cover', 'a-clause'},
    ))
    assert {d.metadata['source_id'] for d in result.documents} <= {'a-cover', 'a-clause'}
    assert set(reranker.sources) <= {'a-cover', 'a-clause'}
    assert reranker.query == QUERY


def test_focusing_business_question_preserves_each_named_file_year():
    older = document('other-clause', '甲大学', '邀请专家来校访问，无条件报销所有交通费。')
    older.metadata['version'] = 'file-2022'
    retriever, _, reranker = engine([*DOCS[:4], older])
    result = retriever.retrieve(QUERY, top_k=2)
    assert {d.metadata['source_id'] for d in result.documents} == {'a-clause', 'b-clause'}
    assert 'other-clause' not in reranker.sources


def test_version_expressions_adjacent_to_chinese_names_keep_original_query():
    retriever, _, reranker = engine()
    query = QUERY.replace('2024年', 'v2版')
    retriever.retrieve(query, top_k=2)
    assert reranker.query == query
