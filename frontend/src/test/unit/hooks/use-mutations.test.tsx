import React from 'react'
import { renderHook, waitFor, act } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { vi, describe, it, expect, beforeEach } from 'vitest'
import {
  useCreateSession,
  useStartGrading,
  useStopGrading,
  useRegradeStudent,
  useOverrideScore,
  useFlagStudent,
  useDeleteSession,
} from '@/hooks/use-mutations'
import type { Session } from '@/lib/types'

vi.mock('@/lib/api', () => ({
  createSession: vi.fn(),
  startGrading: vi.fn(),
  stopGrading: vi.fn(),
  regradeStudent: vi.fn(),
  overrideScore: vi.fn(),
  flagStudent: vi.fn(),
  deleteSession: vi.fn(),
}))

import {
  createSession,
  startGrading,
  stopGrading,
  regradeStudent,
  overrideScore,
  flagStudent,
  deleteSession,
} from '@/lib/api'

const mockCreateSession = vi.mocked(createSession)
const mockStartGrading = vi.mocked(startGrading)
const mockStopGrading = vi.mocked(stopGrading)
const mockRegradeStudent = vi.mocked(regradeStudent)
const mockOverrideScore = vi.mocked(overrideScore)
const mockFlagStudent = vi.mocked(flagStudent)
const mockDeleteSession = vi.mocked(deleteSession)

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

const mockSession: Session = {
  id: 1,
  title: 'Test',
  description: 'desc',
  rubric: 'rubric',
  max_score: 100,
  status: 'pending',
  total_students: 10,
  graded_count: 0,
  error_count: 0,
  created_at: '2025-01-01T00:00:00Z',
  started_at: null,
  completed_at: null,
}

describe('useCreateSession', () => {
  beforeEach(() => vi.clearAllMocks())

  it('calls createSession API and returns created session', async () => {
    mockCreateSession.mockResolvedValue(mockSession)

    const { result } = renderHook(() => useCreateSession(), {
      wrapper: createWrapper(),
    })

    await act(async () => {
      result.current.mutate({
        title: 'Test',
        description: 'desc',
        rubric: 'rubric',
        max_score: 100,
      })
    })

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true)
    })

    expect(mockCreateSession).toHaveBeenCalledWith({
      title: 'Test',
      description: 'desc',
      rubric: 'rubric',
      max_score: 100,
    })
    expect(result.current.data).toEqual(mockSession)
  })
})

describe('useStartGrading', () => {
  beforeEach(() => vi.clearAllMocks())

  it('calls startGrading API with session ID', async () => {
    mockStartGrading.mockResolvedValue(undefined)

    const { result } = renderHook(() => useStartGrading(), {
      wrapper: createWrapper(),
    })

    await act(async () => {
      result.current.mutate({ sessionId: 5 })
    })

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true)
    })

    expect(mockStartGrading).toHaveBeenCalledWith(5)
  })

  it('invalidates session query on success', async () => {
    mockStartGrading.mockResolvedValue(undefined)
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    })
    const spy = vi.spyOn(queryClient, 'invalidateQueries')

    function Wrapper({ children }: { children: React.ReactNode }) {
      return (
        <QueryClientProvider client={queryClient}>
          {children}
        </QueryClientProvider>
      )
    }

    const { result } = renderHook(() => useStartGrading(), {
      wrapper: Wrapper,
    })

    await act(async () => {
      result.current.mutate({ sessionId: 3 })
    })

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true)
    })

    expect(spy).toHaveBeenCalledWith({
      queryKey: ['session', 3],
    })
  })
})

describe('useStopGrading', () => {
  beforeEach(() => vi.clearAllMocks())

  it('calls stopGrading API', async () => {
    mockStopGrading.mockResolvedValue(undefined)

    const { result } = renderHook(() => useStopGrading(), {
      wrapper: createWrapper(),
    })

    await act(async () => {
      result.current.mutate({ sessionId: 2 })
    })

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true)
    })

    expect(mockStopGrading).toHaveBeenCalledWith(2)
  })
})

describe('useRegradeStudent', () => {
  beforeEach(() => vi.clearAllMocks())

  it('calls regradeStudent API with both IDs', async () => {
    mockRegradeStudent.mockResolvedValue({ message: 'Re-grading started', student_id: 5 })

    const { result } = renderHook(() => useRegradeStudent(), {
      wrapper: createWrapper(),
    })

    await act(async () => {
      result.current.mutate({ sessionId: 1, studentId: 5 })
    })

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true)
    })

    expect(mockRegradeStudent).toHaveBeenCalledWith(1, 5)
  })
})

describe('useOverrideScore', () => {
  beforeEach(() => vi.clearAllMocks())

  it('calls overrideScore API with payload', async () => {
    mockOverrideScore.mockResolvedValue(undefined)

    const { result } = renderHook(() => useOverrideScore(), {
      wrapper: createWrapper(),
    })

    await act(async () => {
      result.current.mutate({
        sessionId: 1,
        studentId: 3,
        payload: { score: 95, comments: 'Adjusted' },
      })
    })

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true)
    })

    expect(mockOverrideScore).toHaveBeenCalledWith(1, 3, {
      score: 95,
      comments: 'Adjusted',
    })
  })
})

describe('useFlagStudent', () => {
  beforeEach(() => vi.clearAllMocks())

  it('calls flagStudent API with reason', async () => {
    mockFlagStudent.mockResolvedValue(undefined)

    const { result } = renderHook(() => useFlagStudent(), {
      wrapper: createWrapper(),
    })

    await act(async () => {
      result.current.mutate({
        sessionId: 1,
        studentId: 2,
        reason: 'Potential plagiarism',
      })
    })

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true)
    })

    expect(mockFlagStudent).toHaveBeenCalledWith(1, 2, 'Potential plagiarism')
  })
})

describe('useDeleteSession', () => {
  beforeEach(() => vi.clearAllMocks())

  it('calls deleteSession API', async () => {
    mockDeleteSession.mockResolvedValue(undefined)

    const { result } = renderHook(() => useDeleteSession(), {
      wrapper: createWrapper(),
    })

    await act(async () => {
      result.current.mutate({ sessionId: 7 })
    })

    await waitFor(() => {
      expect(result.current.isSuccess).toBe(true)
    })

    expect(mockDeleteSession).toHaveBeenCalledWith(7)
  })

  it('handles error from API', async () => {
    mockDeleteSession.mockRejectedValue(new Error('Forbidden'))

    const { result } = renderHook(() => useDeleteSession(), {
      wrapper: createWrapper(),
    })

    await act(async () => {
      result.current.mutate({ sessionId: 1 })
    })

    await waitFor(() => {
      expect(result.current.isError).toBe(true)
    })

    expect(result.current.error?.message).toBe('Forbidden')
  })
})
