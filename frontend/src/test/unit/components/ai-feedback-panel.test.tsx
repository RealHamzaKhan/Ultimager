import React from 'react'
import { render, screen, fireEvent } from '@testing-library/react'
import { vi, describe, it, expect } from 'vitest'
import { AIFeedbackPanel } from '@/components/student/ai-feedback-panel'
import type { Submission, CriterionScore } from '@/lib/types'

const mockSubmission: Submission = {
  id: 1, session_id: 1, student_identifier: 'alice', status: 'graded',
  file_count: 2, ai_score: 85, ai_letter_grade: 'B', ai_confidence: 'high',
  final_score: 85, is_overridden: false, override_score: null, override_comments: '',
  is_reviewed: false, tests_passed: 0, tests_total: 0, graded_at: '2025-01-01',
  error_message: '', files: [],
  ai_result: {
    rubric_breakdown: [
      { criterion: 'Code Quality', score: 35, max: 40, justification: 'Good structure' },
      { criterion: 'Testing', score: 25, max: 30, justification: 'Decent tests' },
    ],
    total_score: 85, overall_feedback: 'Solid work overall.',
    strengths: ['Clean code', 'Good naming'], weaknesses: ['Missing edge cases'],
    suggestions_for_improvement: 'Add more tests',
    confidence: 'high', confidence_reasoning: 'Clear submission',
  },
  ai_feedback: 'Solid work.',
  rubric_breakdown: [
    { criterion: 'Code Quality', score: 35, max: 40, justification: 'Good structure' },
    { criterion: 'Testing', score: 25, max: 30, justification: 'Decent tests' },
  ],
  strengths: ['Clean code', 'Good naming'], weaknesses: ['Missing edge cases'],
  suggestions_for_improvement: 'Add more tests',
  confidence_reasoning: 'Clear submission',
  is_flagged: false, flag_reason: '', flagged_by: '', flagged_at: null,
}

const mockRubricBreakdown: CriterionScore[] = [
  { criterion: 'Code Quality', score: 35, max: 40, justification: 'Good structure' },
  { criterion: 'Testing', score: 25, max: 30, justification: 'Decent tests' },
]

const defaultProps = {
  submission: mockSubmission,
  rubricBreakdown: mockRubricBreakdown,
  strengths: ['Clean code', 'Good naming'],
  weaknesses: ['Missing edge cases'],
  feedback: 'Solid work overall.',
  suggestions: 'Add more tests',
  criticalErrors: [],
  maxScore: 100,
}

describe('AIFeedbackPanel', () => {
  it('renders the panel with feedback', () => {
    render(<AIFeedbackPanel {...defaultProps} />)
    expect(screen.getByText('Overall Assessment')).toBeInTheDocument()
  })

  it('shows rubric breakdown criteria', () => {
    render(<AIFeedbackPanel {...defaultProps} />)
    expect(screen.getByText('Rubric Breakdown')).toBeInTheDocument()
    expect(screen.getByText('Code Quality')).toBeInTheDocument()
    expect(screen.getByText('Testing')).toBeInTheDocument()
  })

  it('shows criterion scores', () => {
    render(<AIFeedbackPanel {...defaultProps} />)
    expect(screen.getByText('35/40')).toBeInTheDocument()
    expect(screen.getByText('25/30')).toBeInTheDocument()
  })

  it('shows overall feedback', () => {
    render(<AIFeedbackPanel {...defaultProps} />)
    expect(screen.getByText('Solid work overall.')).toBeInTheDocument()
  })

  it('shows strengths list', () => {
    render(<AIFeedbackPanel {...defaultProps} />)
    expect(screen.getByText('Clean code')).toBeInTheDocument()
    expect(screen.getByText('Good naming')).toBeInTheDocument()
  })

  it('shows weaknesses list', () => {
    render(<AIFeedbackPanel {...defaultProps} />)
    expect(screen.getByText('Missing edge cases')).toBeInTheDocument()
  })

  it('shows justification text after expanding criterion', () => {
    render(<AIFeedbackPanel {...defaultProps} />)
    // Justification is hidden until criterion is expanded
    fireEvent.click(screen.getByText('Code Quality'))
    expect(screen.getByText('Good structure')).toBeInTheDocument()
  })

  it('shows suggestions for improvement', () => {
    render(<AIFeedbackPanel {...defaultProps} />)
    expect(screen.getByText('Suggestions for Improvement')).toBeInTheDocument()
    expect(screen.getByText('Add more tests')).toBeInTheDocument()
  })
})
