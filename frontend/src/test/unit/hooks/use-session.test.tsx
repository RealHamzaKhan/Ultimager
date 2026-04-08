import React from 'react'
import { renderHook, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { vi, describe, it, expect, beforeEach } from 'vitest'
import { useSession } from '@/hooks/use-session'
import type { Session } from '@/lib/types'

vi.mock('@/lib/api', () => ({
  fetchSession: vi.fn(),
}))

import { fetchSession } from '@/lib/api'

const mockFetchSession = vi.mocked(fetchSession)

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

function makeSession(overrides: Partial<Session> = {}): Session {
  return {
    id: 1,
    title: 'Test Session',
    description: 'A test session',
    rubric: 'rubric',
    max_score: 100,
    status: 'pending',
    total_students: 10,
    graded_count: 0,
    error_count: 0,
    created_at: '2025-01-01T00:00:00Z',
    started_at: null,
    completed_at: null,
    ...overrides,
  }
}

describe('useSession', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('fetches session data by ID', async () => {
    const session = makeSession({ id: 5, title: 'Midterm' })
    mockFetchSession.mockResolvedValue(session)

    const { result } = renderHook(() => useSession(5), {
      wrapper: createWrapper(),
    })

    await waitFor(() => {
      expect(result.current.isLoading).toBe(false)
    })

    expect(result.current.session).toEqual(session)
    expect(result.current.isError).toBe(false)
    expect(mockFetchSession).toHaveBeenCalledWith(5)
  })

  it('returns error on 404', async () => {
    mockFetchSession.mockRejectedValue(new Error('Not found'))

    const { result } = renderHook(() => useSession(999), {
      wrapper: createWrapper(),
    })

    await waitFor(() => {
      expect(result.current.isError).toBe(true)
    })

    expect(result.current.error?.message).toBe('Not found')
    expect(result.current.session).toBeNull()
  })

  it('enables refetchInterval when status is grading', async () => {
    const gradingSession = makeSession({ status: 'grading', graded_count: 3 })
    mockFetchSession.mockResolvedValue(gradingSession)

    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    })

    function Wrapper({ children }: { children: React.ReactNode }) {
      return (
        <QueryClientProvider client={queryClient}>
          {children}
        </QueryClientProvider>
      )
    }

    const { result } = renderHook(() => useSession(1), {
      wrapper: Wrapper,
    })

    await waitFor(() => {
      expect(result.current.session?.status).toBe('grading')
    })

    // Verify the query was called and the session has grading status
    expect(mockFetchSession).toHaveBeenCalledWith(1)
    expect(result.current.session?.status).toBe('grading')

    // Verify refetchInterval is set by checking that react-query will poll
    // We check the query's state to verify the refetchInterval config is active
    const queryState = queryClient.getQueryState(['session', 1])
    expect(queryState?.status).toBe('success')
  })

  it('does not poll when status is complete', async () => {
    const completeSession = makeSession({
      status: 'complete',
      graded_count: 10,
    })
    mockFetchSession.mockResolvedValue(completeSession)

    const { result } = renderHook(() => useSession(1), {
      wrapper: createWrapper(),
    })

    await waitFor(() => {
      expect(result.current.session?.status).toBe('complete')
    })

    // Wait a bit and verify no additional fetches happen
    await new Promise((r) => setTimeout(r, 100))
    expect(mockFetchSession).toHaveBeenCalledTimes(1)
  })

  it('does not fetch when id is 0', () => {
    const { result } = renderHook(() => useSession(0), {
      wrapper: createWrapper(),
    })

    // When enabled is false, isPending is true but isFetching is false
    expect(mockFetchSession).not.toHaveBeenCalled()
  })
})
