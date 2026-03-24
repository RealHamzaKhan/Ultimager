import React from 'react'
import { renderHook, waitFor } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { vi, describe, it, expect, beforeEach } from 'vitest'
import { useSessions } from '@/hooks/use-sessions'
import type { SessionListResponse } from '@/lib/types'

vi.mock('@/lib/api', () => ({
  fetchSessions: vi.fn(),
}))

import { fetchSessions } from '@/lib/api'

const mockFetchSessions = vi.mocked(fetchSessions)

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

const mockResponse: SessionListResponse = {
  count: 2,
  sessions: [
    {
      id: 1,
      title: 'Midterm Exam',
      description: 'Fall 2025 midterm',
      rubric: 'rubric text',
      max_score: 100,
      status: 'complete',
      total_students: 30,
      graded_count: 30,
      error_count: 0,
      created_at: '2025-10-01T00:00:00Z',
      started_at: '2025-10-01T01:00:00Z',
      completed_at: '2025-10-01T02:00:00Z',
    },
    {
      id: 2,
      title: 'Final Exam',
      description: 'Fall 2025 final',
      rubric: 'rubric text',
      max_score: 100,
      status: 'pending',
      total_students: 25,
      graded_count: 0,
      error_count: 0,
      created_at: '2025-12-01T00:00:00Z',
      started_at: null,
      completed_at: null,
    },
  ],
}

describe('useSessions', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('returns loading state initially', () => {
    mockFetchSessions.mockReturnValue(new Promise(() => {})) // never resolves
    const { result } = renderHook(() => useSessions(), {
      wrapper: createWrapper(),
    })

    expect(result.current.isLoading).toBe(true)
    expect(result.current.sessions).toEqual([])
    expect(result.current.isError).toBe(false)
  })

  it('returns session list on success', async () => {
    mockFetchSessions.mockResolvedValue(mockResponse)
    const { result } = renderHook(() => useSessions(), {
      wrapper: createWrapper(),
    })

    await waitFor(() => {
      expect(result.current.isLoading).toBe(false)
    })

    expect(result.current.sessions).toHaveLength(2)
    expect(result.current.sessions[0].title).toBe('Midterm Exam')
    expect(result.current.sessions[1].title).toBe('Final Exam')
    expect(result.current.isError).toBe(false)
    expect(result.current.error).toBeNull()
  })

  it('returns error state on failure', async () => {
    mockFetchSessions.mockRejectedValue(new Error('Network error'))
    const { result } = renderHook(() => useSessions(), {
      wrapper: createWrapper(),
    })

    await waitFor(() => {
      expect(result.current.isError).toBe(true)
    })

    expect(result.current.error).toBeInstanceOf(Error)
    expect(result.current.error?.message).toBe('Network error')
    expect(result.current.sessions).toEqual([])
    expect(result.current.isLoading).toBe(false)
  })

  it('provides a refetch function', async () => {
    mockFetchSessions.mockResolvedValue(mockResponse)
    const { result } = renderHook(() => useSessions(), {
      wrapper: createWrapper(),
    })

    await waitFor(() => {
      expect(result.current.isLoading).toBe(false)
    })

    expect(typeof result.current.refetch).toBe('function')
    expect(mockFetchSessions).toHaveBeenCalledTimes(1)

    await result.current.refetch()
    expect(mockFetchSessions).toHaveBeenCalledTimes(2)
  })
})
