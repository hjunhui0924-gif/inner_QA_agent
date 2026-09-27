"""Model source identity must survive order changes and reject stale revisions."""
import unittest
from unittest.mock import patch

from langchain_core.documents import Document
from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage

from backend.agent.citations import (evidence_id, resolve_evidence_markers, build_citations,
    historical_citation_text, display_to_evidence, validate_citation_structure)
from backend.agent.nodes import generate, check_hallucination
from backend.config import settings


def doc(text, source='travel', version='v2'):
    return Document(page_content=text, metadata={'source_id':source,'document_id':source,
        'chunk_id':source+':0','version':version,'title':source})


class EvidenceIdentityTests(unittest.TestCase):
    def test_order_does_not_change_identity_but_revision_does(self):
        travel = doc('差旅需提交发票、行程单和审批记录。')
        other = doc('合同需验收材料。', 'contract')
        token = '['+evidence_id(travel)+']'
        self.assertEqual(resolve_evidence_markers('提交发票 '+token, [travel,other]), '提交发票 [C1]')
        self.assertEqual(resolve_evidence_markers('提交发票 '+token, [other,travel]), '提交发票 [C2]')
        for changed in [doc(travel.page_content, version='v3'), doc('差旅凭证规则已更新。'), doc(travel.page_content,'another')]:
            self.assertNotEqual(evidence_id(travel), evidence_id(changed))
            self.assertEqual(resolve_evidence_markers(token,[changed]), '[C0]')

    def test_history_uses_snapshot_not_display_id_and_does_not_guess_old_records(self):
        travel=doc('差旅需提交发票。');other=doc('合同需验收材料。','contract')
        snapshots=build_citations([travel], '提交发票 [C1]')
        text=historical_citation_text('提交发票 [C1]',snapshots,[other,travel])
        self.assertIn('['+evidence_id(travel)+']',text)
        self.assertNotIn('[C1]',text)
        self.assertNotIn('[S_',historical_citation_text('旧答 [C1]',[{'citation_id':'C1'}],[other,travel]))
        self.assertNotIn('[S_',historical_citation_text('旧答 [C1]',snapshots,[other]))
        self.assertNotIn('[S_',historical_citation_text('旧答 [C1]',snapshots,[doc('差旅需提交发票。',version='v3')]))

    def test_unknown_or_display_ids_fail_structural_gate(self):
        docs=[doc('提交发票。')]
        for marker in ['[S_unknown]','[C1]','[S_'+'0'*24+']','[s_unknown]', '[ S_unknown ]', '[S_unknown']:
            answer=resolve_evidence_markers('提交发票 '+marker,docs)
            self.assertFalse(validate_citation_structure(answer,docs,build_citations(docs,answer))[0])
            mixed=resolve_evidence_markers('提交发票 ['+evidence_id(docs[0])+']，其他要求 '+marker,docs)
            self.assertFalse(validate_citation_structure(mixed,docs,build_citations(docs,mixed))[0])

    def test_retry_feedback_translates_bracketed_and_bare_numbers(self):
        docs=[doc('合同材料。','contract'),doc('差旅凭证。')]
        result=display_to_evidence('引用C1有误，应该使用 [C2]，不要用C0。',docs)
        self.assertIn('['+evidence_id(docs[0])+']',result)
        self.assertIn('['+evidence_id(docs[1])+']',result)
        self.assertNotIn('C1',result)
        self.assertIn('无效引用',result)


class GenerationIdentityTests(unittest.IsolatedAsyncioTestCase):
    async def test_generation_uses_stable_refs_but_commits_display_refs(self):
        travel=doc('差旅需提交发票、行程单和审批记录。');other=doc('合同需验收材料。','contract')
        history=AIMessage(content='差旅审批 [C1]。',additional_kwargs={'citations':build_citations([travel],'差旅审批 [C1]')})
        captured=[]
        class Model:
            async def astream(self,messages):
                captured.extend(messages)
                yield AIMessageChunk(content='差旅需提交发票、行程单和审批记录 ['+evidence_id(travel)+']。')
        with patch('backend.agent.nodes._build_model',return_value=Model()):
            result=await generate({'route':'rag','mode':'knowledge','query':'需要哪些凭证？',
                'retrieved_docs':[other,travel],'messages':[history,HumanMessage(content='需要哪些凭证？')]})
        self.assertIn('[C2]',result['candidate_answer'])
        self.assertNotIn('[S_',result['candidate_answer'])
        self.assertEqual(result['candidate_citations'][0]['evidence_id'],evidence_id(travel))
        self.assertEqual(result['candidate_citations'][0]['source_id'],'travel')
        self.assertNotIn('[C1]',captured[1].content)
        self.assertEqual(history.content,'差旅审批 [C1]。')

    async def test_copied_history_display_marker_is_not_silently_accepted(self):
        docs=[doc('合同材料。','contract'),doc('差旅需提交发票。')]
        class Model:
            async def astream(self,messages):
                yield AIMessageChunk(content='差旅需提交发票 [C1]。')
        with patch('backend.agent.nodes._build_model',return_value=Model()):
            result=await generate({'route':'rag','retrieved_docs':docs,'messages':[],'query':'凭证？'})
        with patch.object(settings,'citation_validation_mode','judge'), patch('backend.agent.nodes._build_model') as model:
            checked=await check_hallucination({'route':'rag','retrieved_docs':docs,**result})
        model.assert_not_called()
        self.assertFalse(checked['hallucination_pass'])
        self.assertEqual(checked['failure_stage'],'citation')
