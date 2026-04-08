import React from 'react'
import { render, screen, fireEvent } from '@testing-library/react'
import { vi, describe, it, expect } from 'vitest'
import { TransparencyVault } from '@/components/student/transparency-vault'
import type { Submission } from '@/lib/types'

const mockSubmission: Submission = {
  id: 1, session_id: 1, student_identifier: 'alice', status: 'graded',
  file_count: 2, ai_score: 85, ai_letter_grade: 'B', ai_confidence: 'high',
  final_score: 85, is_overridden: false, override_score: null, override_comments: '',
  is_reviewed: false, tests_passed: 0, tests_total: 0, graded_at: '2025-01-01',
  error_message: '', files: [],
  ai_result: {
    rubric_breakdown: [],
    total_score: 85, overall_feedback: '',
    strengths: [], weaknesses: [],
    suggestions_for_improvement: '',
    confidence: 'high', confidence_reasoning: '',
    grading_hash: 'abc123def456',
    transparency: {
      llm_call: {
        model: 'gpt-4',
        provider: 'openai',
        usage: { prompt_tokens: 1000, completion_tokens: 500, total_tokens: 1500 },
        fallback_used: false,
      },
      text_chars_sent: 5000,
      images_sent: 0,
    },
  },
  ai_feedback: '', rubric_breakdown: [],
  strengths: [], weaknesses: [],
  suggestions_for_improvement: '', confidence_reasoning: '',
  is_flagged: false, flag_reason: '', flagged_by: '', flagged_at: null,
}

describe('TransparencyVault', () => {
  it('renders vault with transparency data', () => {
    render(<TransparencyVault submission={mockSubmission} />)
    expect(screen.getByText('Model Information')).toBeInTheDocument()
  })

  it('shows model info (expanded by default)', () => {
    render(<TransparencyVault submission={mockSubmission} />)
    // Model and tokens sections are expanded by default
    expect(screen.getAllByText('gpt-4').length).toBeGreaterThanOrEqual(1)
    expect(screen.getAllByText('openai').length).toBeGreaterThanOrEqual(1)
  })

  it('shows token usage (expanded by default)', () => {
    render(<TransparencyVault submission={mockSubmission} />)
    expect(screen.getByText('Token Usage')).toBeInTheDocument()
    expect(screen.getByText('1,000')).toBeInTheDocument()
    expect(screen.getByText('500')).toBeInTheDocument()
    expect(screen.getByText('1,500')).toBeInTheDocument()
  })

  it('shows grading hash after expanding section', () => {
    render(<TransparencyVault submission={mockSubmission} />)
    fireEvent.click(screen.getByText('Grading Hash'))
    expect(screen.getByText('abc123def456')).toBeInTheDocument()
  })

  it('shows empty state when no transparency data', () => {
    const noDataSubmission = {
      ...mockSubmission,
      ai_result: null,
    }
    render(<TransparencyVault submission={noDataSubmission} />)
    expect(screen.getByText(/No transparency data/)).toBeInTheDocument()
  })
})
