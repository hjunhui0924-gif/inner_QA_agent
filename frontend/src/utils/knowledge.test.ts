import { describe, expect, it } from 'vitest'

import type { KnowledgeRecord } from '../types/api'
import {
  filterKnowledgeRecords,
  formatKnowledgeDateRange,
  knowledgeStatusLabel,
  readableKnowledgeUploadError,
  validateKnowledgeFile,
} from './knowledge'

const records: KnowledgeRecord[] = [
  {
    source_id: 'finance-v2',
    title: '费用报销制度',
    source: 'synthetic_seed',
    original_filename: 'finance-v2.md',
    department: 'Finance',
    version: 'v2',
    status: 'active',
    owner: '财务部',
    preview: '单笔超过5000元还需财务负责人复核。',
  },
  {
    source_id: 'hr-v1',
    title: '请假与调休流程（旧版）',
    source: 'synthetic_seed',
    original_filename: 'hr-v1.md',
    department: 'HR',
    version: 'v1',
    status: 'deprecated',
    owner: '人力资源部',
  },
]

describe('knowledge workbench helpers', () => {
  it('filters by query and metadata without mutating source records', () => {
    const filtered = filterKnowledgeRecords(records, {
      query: '5000',
      department: 'Finance',
      status: 'active',
      version: 'v2',
    })

    expect(filtered).toEqual([records[0]])
    expect(records).toHaveLength(2)
  })

  it('uses readable status and effective-date labels', () => {
    expect(knowledgeStatusLabel('active')).toBe('已生效')
    expect(knowledgeStatusLabel('unknown-status')).toBe('状态未知')
    expect(formatKnowledgeDateRange('2026-01-01', null)).toBe('2026-01-01 起')
    expect(formatKnowledgeDateRange('', '2026-12-31')).toBe('截至 2026-12-31')
  })

  it('rejects empty, oversized, and unsupported files before upload', () => {
    expect(validateKnowledgeFile(null)).toContain('请选择')
    expect(validateKnowledgeFile(new File([], 'empty.pdf'))).toContain('为空')
    expect(validateKnowledgeFile(new File(['x'], 'script.exe'))).toContain('不支持')
    expect(validateKnowledgeFile(new File(['x'.repeat(21 * 1024 * 1024)], 'large.pdf')))
      .toContain('20 MB')
    expect(validateKnowledgeFile(new File(['ok'], 'policy.PDF'))).toBeNull()
  })

  it('keeps upload errors user-facing and hides unexpected backend details', () => {
    expect(readableKnowledgeUploadError(new Error('JSON 文件解析失败。')))
      .toBe('JSON 文件解析失败。')
    expect(readableKnowledgeUploadError(new Error('知识库入库失败：Traceback secret')))
      .toContain('请检查文件内容后重试')
    expect(readableKnowledgeUploadError(new Error('知识库入库失败：Traceback secret')))
      .not.toContain('Traceback')
  })
})
