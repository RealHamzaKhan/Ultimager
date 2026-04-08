import React from 'react'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { vi, describe, it, expect, beforeEach } from 'vitest'
import { StudentTable } from '@/components/session/student-table'
import type { Submission } from '@/lib/types'

const mockStudents: Submission[] = [
  {
    id: 1, session_id: 1, student_identifier: 'alice', status: 'graded',
    file_count: 2, ai_score: 85, ai_letter_grade: 'B', ai_confidence: 'high',
    final_score: 85, is_overridden: false, override_score: null, override_comments: '',
    is_reviewed: false, tests_passed: 0, tests_total: 0, graded_at: '2025-01-01',
    error_message: '', files: [], ai_result: null, ai_feedback: '',
    rubric_breakdown: [], strengths: [], weaknesses: [],
    suggestions_for_improvement: '', confidence_reasoning: '',
    is_flagged: false, flag_reason: '', flagged_by: '', flagged_at: null,
  },
  {
    id: 2, session_id: 1, student_identifier: 'bob', status: 'error',
    file_count: 1, ai_score: null, ai_letter_grade: '', ai_confidence: '',
    final_score: null, is_overridden: false, override_score: null, override_comments: '',
    is_reviewed: false, tests_passed: 0, tests_total: 0, graded_at: null,
    error_message: 'Failed', files: [], ai_result: null, ai_feedback: '',
    rubric_breakdown: [], strengths: [], weaknesses: [],
    suggestions_for_improvement: '', confidence_reasoning: '',
    is_flagged: true, flag_reason: 'suspicious', flagged_by: 'admin', flagged_at: '2025-01-01',
  },
]

vi.mock('@/hooks/use-students', () => ({
  useStudents: () => ({
    students: mockStudents,
    isLoading: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  }),
  studentsQueryKey: vi.fn(),
}))

vi.mock('next/link', () => ({
  default: ({ children, href }: { children: React.ReactNode; href: string }) => (
    <a href={href}>{children}</a>
  ),
}))

vi.mock('lucide-react', () => ({
  Search: () => <span data-testid="search-icon" />,
  Flag: () => <span data-testid="flag-icon" />,
  ChevronUp: () => <span>▲</span>,
  ChevronDown: () => <span>▼</span>,
  RotateCcw: () => <span>↺</span>,
}))

function createWrapper() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  )
}

describe('StudentTable', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('renders the student table', () => {
    render(<StudentTable sessionId={1} maxScore={100} />, { wrapper: createWrapper() })
    expect(screen.getByTestId('student-table')).toBeInTheDocument()
  })

  it('shows student rows', () => {
    render(<StudentTable sessionId={1} maxScore={100} />, { wrapper: createWrapper() })
    expect(screen.getByText('alice')).toBeInTheDocument()
    expect(screen.getByText('bob')).toBeInTheDocument()
  })

  it('renders search input', () => {
    render(<StudentTable sessionId={1} maxScore={100} />, { wrapper: createWrapper() })
    expect(screen.getByTestId('search-input')).toBeInTheDocument()
  })

  it('renders status filter tabs', () => {
    render(<StudentTable sessionId={1} maxScore={100} />, { wrapper: createWrapper() })
    expect(screen.getByTestId('status-tab-all')).toBeInTheDocument()
    expect(screen.getByTestId('status-tab-graded')).toBeInTheDocument()
    expect(screen.getByTestId('status-tab-error')).toBeInTheDocument()
  })

  it('renders sortable column headers', () => {
    render(<StudentTable sessionId={1} maxScore={100} />, { wrapper: createWrapper() })
    expect(screen.getByTestId('sort-student')).toBeInTheDocument()
    expect(screen.getByTestId('sort-score')).toBeInTheDocument()
  })

  it('shows select-all checkbox', () => {
    render(<StudentTable sessionId={1} maxScore={100} />, { wrapper: createWrapper() })
    expect(screen.getByTestId('select-all-checkbox')).toBeInTheDocument()
  })

  it('shows flag icon for flagged students', () => {
    render(<StudentTable sessionId={1} maxScore={100} />, { wrapper: createWrapper() })
    expect(screen.getByTestId('flag-icon')).toBeInTheDocument()
  })

  it('displays scores correctly', () => {
    render(<StudentTable sessionId={1} maxScore={100} />, { wrapper: createWrapper() })
    expect(screen.getByText('85 / 100')).toBeInTheDocument()
  })
})
