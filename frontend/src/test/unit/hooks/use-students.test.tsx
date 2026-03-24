import React from 'react'
import { renderHook, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { vi, describe, it, expect, beforeEach } from 'vitest'
import { useStudents } from '@/hooks/use-students'
import type { Submission } from '@/lib/types'

vi.mock('@/lib/api', () => ({
  fetchStudents: vi.fn(),
}))

import { fetchStudents } from '@/lib/api'

const mockFetchStudents = vi.mocked(fetchStudents)

function createWrapper() {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  })
  return function Wrapper({ children }: { children: React.ReactNode }) {
    return (
      <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
    )
  }
}

function makeSubmission(overrides: Partial<Submission> = {}): Submission {
  return {
    id: 1,
    session_id: 1,
    student_identifier: 'student_1',
    status: 'graded',
    file_count: 2,
    ai_score: 85,
    ai_letter_grade: 'B',
    ai_confidence: 'high',
    final_score: 85,
    is_overridden: false,
    override_score: null,
    override_comments: '',
    is_reviewed: false,
    tests_passed: 0,
    tests_total: 0,
    graded_at: '2025-01-01T00:00:00Z',
    error_message: '',
    files: [],
    ai_result: null,
    ai_feedback: '',
    rubric_breakdown: [],
    strengths: [],
    weaknesses: [],
    suggestions_for_improvement: '',
    confidence_reasoning: '',
    is_flagged: false,
    flag_reason: '',
    flagged_by: '',
    flagged_at: null,
    ...overrides,
  }
}

const mockStudents: Submission[] = [
  makeSubmission({ id: 1, student_identifier: 'Alice', ai_score: 90, status: 'graded' }),
  makeSubmission({ id: 2, student_identifier: 'Bob', ai_score: 75, status: 'graded' }),
  makeSubmission({ id: 3, student_identifier: 'Charlie', ai_score: 60, status: 'error' }),
  makeSubmission({ id: 4, student_identifier: 'Diana', ai_score: 95, status: 'pending' }),
]

describe('useStudents', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('returns student list for a session', async () => {
    mockFetchStudents.mockResolvedValue(mockStudents)
    const { result } = renderHook(() => useStudents(1), {
      wrapper: createWrapper(),
    })

    await waitFor(() => {
      expect(result.current.isLoading).toBe(false)
    })

    expect(result.current.students).toHaveLength(4)
    expect(result.current.students[0].student_identifier).toBe('Alice')
    expect(mockFetchStudents).toHaveBeenCalledWith(1)
  })

  it('filters by status', async () => {
    mockFetchStudents.mockResolvedValue(mockStudents)
    const { result } = renderHook(
      () => useStudents(1, { status: 'graded' }),
      { wrapper: createWrapper() }
    )

    await waitFor(() => {
      expect(result.current.isLoading).toBe(false)
    })

    expect(result.current.students).toHaveLength(2)
    expect(result.current.students.every((s) => s.status === 'graded')).toBe(
      true
    )
  })

  it('filters by search term', async () => {
    mockFetchStudents.mockResolvedValue(mockStudents)
    const { result } = renderHook(
      () => useStudents(1, { search: 'ali' }),
      { wrapper: createWrapper() }
    )

    await waitFor(() => {
      expect(result.current.isLoading).toBe(false)
    })

    expect(result.current.students).toHaveLength(1)
    expect(result.current.students[0].student_identifier).toBe('Alice')
  })

  it('sorts by ai_score ascending', async () => {
    mockFetchStudents.mockResolvedValue(mockStudents)
    const { result } = renderHook(
      () => useStudents(1, { sortBy: 'ai_score', sortDir: 'asc' }),
      { wrapper: createWrapper() }
    )

    await waitFor(() => {
      expect(result.current.isLoading).toBe(false)
    })

    const scores = result.current.students.map((s) => s.ai_score)
    expect(scores).toEqual([60, 75, 90, 95])
  })

  it('sorts by ai_score descending', async () => {
    mockFetchStudents.mockResolvedValue(mockStudents)
    const { result } = renderHook(
      () => useStudents(1, { sortBy: 'ai_score', sortDir: 'desc' }),
      { wrapper: createWrapper() }
    )

    await waitFor(() => {
      expect(result.current.isLoading).toBe(false)
    })

    const scores = result.current.students.map((s) => s.ai_score)
    expect(scores).toEqual([95, 90, 75, 60])
  })

  it('uses different query keys for different filters', async () => {
    mockFetchStudents.mockResolvedValue(mockStudents)
    const wrapper = createWrapper()

    const { result: result1 } = renderHook(
      () => useStudents(1, { status: 'graded' }),
      { wrapper }
    )
    const { result: result2 } = renderHook(
      () => useStudents(1, { status: 'error' }),
      { wrapper }
    )

    await waitFor(() => {
      expect(result1.current.isLoading).toBe(false)
      expect(result2.current.isLoading).toBe(false)
    })

    expect(result1.current.students).toHaveLength(2)
    expect(result2.current.students).toHaveLength(1)
  })

  it('does not fetch when sessionId is 0', () => {
    const { result } = renderHook(() => useStudents(0), {
      wrapper: createWrapper(),
    })

    expect(result.current.isLoading).toBe(false)
    expect(mockFetchStudents).not.toHaveBeenCalled()
  })
})
