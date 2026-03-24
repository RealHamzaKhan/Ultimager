import React from 'react'
import { renderHook, act } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { vi, describe, it, expect, beforeEach, afterEach } from 'vitest'
import { useGradeStream } from '@/hooks/use-grade-stream'

// Mock EventSource
type EventSourceListener = (event: MessageEvent) => void
type EventSourceHandler = (() => void) | null

class MockEventSource {
  static instances: MockEventSource[] = []
  url: string
  onopen: EventSourceHandler = null
  onmessage: EventSourceListener | null = null
  onerror: EventSourceHandler = null
  readyState = 0
  closed = false

  constructor(url: string) {
    this.url = url
    MockEventSource.instances.push(this)
  }

  close() {
    this.closed = true
    this.readyState = 2
  }

  triggerOpen() {
    this.readyState = 1
    this.onopen?.()
  }

  simulateMessage(data: Record<string, unknown>) {
    const event = new MessageEvent('message', {
      data: JSON.stringify(data),
    })
    this.onmessage?.(event)
  }

  simulateError() {
    this.onerror?.()
  }
}

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

describe('useGradeStream', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.useFakeTimers()
    MockEventSource.instances = []
    vi.stubGlobal('EventSource', MockEventSource)
  })

  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllGlobals()
  })

  it('connects to the correct URL', () => {
    renderHook(() => useGradeStream(42), {
      wrapper: createWrapper(),
    })

    expect(MockEventSource.instances).toHaveLength(1)
    expect(MockEventSource.instances[0].url).toContain('/session/42/grade-stream')
  })

  it('updates connected state on open', () => {
    const { result } = renderHook(() => useGradeStream(1), {
      wrapper: createWrapper(),
    })

    const es = MockEventSource.instances[0]

    act(() => {
      es.triggerOpen()
    })

    expect(result.current.connected).toBe(true)
  })

  it('updates state on progress event', () => {
    const { result } = renderHook(() => useGradeStream(1), {
      wrapper: createWrapper(),
    })

    const es = MockEventSource.instances[0]

    act(() => {
      es.triggerOpen()
    })

    act(() => {
      es.simulateMessage({
        type: 'progress',
        graded_count: 5,
        total: 20,
        student: 'Alice',
        stage: 'grading',
      })
    })

    expect(result.current.graded).toBe(5)
    expect(result.current.total).toBe(20)
    expect(result.current.currentStudent).toBe('Alice')
    expect(result.current.stage).toBe('grading')
  })

  it('tracks completed students on student_graded event', () => {
    const { result } = renderHook(() => useGradeStream(1), {
      wrapper: createWrapper(),
    })

    const es = MockEventSource.instances[0]

    act(() => {
      es.triggerOpen()
    })

    act(() => {
      es.simulateMessage({
        type: 'student_graded',
        student_id: 1,
        student: 'Alice',
        score: 85,
        letter_grade: 'B+',
        confidence: 'high',
      })
    })

    expect(result.current.completedStudents).toHaveLength(1)
    expect(result.current.completedStudents[0].name).toBe('Alice')
    expect(result.current.completedStudents[0].score).toBe(85)
    expect(result.current.completedStudents[0].grade).toBe('B+')
  })

  it('tracks error students on student_error event', () => {
    const { result } = renderHook(() => useGradeStream(1), {
      wrapper: createWrapper(),
    })

    const es = MockEventSource.instances[0]

    act(() => {
      es.triggerOpen()
    })

    act(() => {
      es.simulateMessage({
        type: 'student_error',
        student_id: 2,
        student: 'Bob',
        error: 'Timeout',
      })
    })

    expect(result.current.completedStudents).toHaveLength(1)
    expect(result.current.completedStudents[0].status).toBe('error')
    expect(result.current.completedStudents[0].errorMessage).toBe('Timeout')
  })

  it('disconnects on complete event', () => {
    const { result } = renderHook(() => useGradeStream(1), {
      wrapper: createWrapper(),
    })

    const es = MockEventSource.instances[0]

    act(() => {
      es.triggerOpen()
    })

    act(() => {
      es.simulateMessage({ type: 'complete' })
    })

    expect(result.current.isComplete).toBe(true)
    expect(result.current.connected).toBe(false)
    expect(es.closed).toBe(true)
  })

  it('handles stopped event', () => {
    const { result } = renderHook(() => useGradeStream(1), {
      wrapper: createWrapper(),
    })

    const es = MockEventSource.instances[0]

    act(() => {
      es.triggerOpen()
    })

    act(() => {
      es.simulateMessage({ type: 'stopped' })
    })

    expect(result.current.isStopped).toBe(true)
    expect(result.current.connected).toBe(false)
  })

  it('reconnects on error with retry delays', async () => {
    renderHook(() => useGradeStream(1), {
      wrapper: createWrapper(),
    })

    expect(MockEventSource.instances).toHaveLength(1)

    act(() => {
      MockEventSource.instances[0].simulateError()
    })

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1000)
    })

    expect(MockEventSource.instances).toHaveLength(2)
  })

  it('shows error after max retries', async () => {
    const { result } = renderHook(() => useGradeStream(1), {
      wrapper: createWrapper(),
    })

    // Exhaust all retries (MAX_RETRIES = 5)
    const delays = [1000, 2000, 4000, 8000, 16000, 32000]

    for (let i = 0; i < 6; i++) {
      act(() => {
        MockEventSource.instances[MockEventSource.instances.length - 1].simulateError()
      })
      await act(async () => {
        await vi.advanceTimersByTimeAsync(delays[i])
      })
    }

    expect(result.current.error).toContain('connection lost')
  })

  it('ignores malformed JSON messages', () => {
    const { result } = renderHook(() => useGradeStream(1), {
      wrapper: createWrapper(),
    })

    const es = MockEventSource.instances[0]

    act(() => {
      es.triggerOpen()
    })

    act(() => {
      const event = new MessageEvent('message', {
        data: 'not valid json {{{',
      })
      es.onmessage?.(event)
    })

    expect(result.current.graded).toBe(0)
    expect(result.current.total).toBe(0)
    expect(result.current.error).toBeNull()
  })

  it('closes connection on unmount', () => {
    const { unmount } = renderHook(() => useGradeStream(1), {
      wrapper: createWrapper(),
    })

    const es = MockEventSource.instances[0]
    expect(es.closed).toBe(false)

    unmount()

    expect(es.closed).toBe(true)
  })

  it('does not connect when enabled is false', () => {
    renderHook(() => useGradeStream(1, false), {
      wrapper: createWrapper(),
    })

    expect(MockEventSource.instances).toHaveLength(0)
  })

  it('handles error event from server', () => {
    const { result } = renderHook(() => useGradeStream(1), {
      wrapper: createWrapper(),
    })

    const es = MockEventSource.instances[0]

    act(() => {
      es.triggerOpen()
    })

    act(() => {
      es.simulateMessage({
        type: 'error',
        message: 'Grading failed for student',
      })
    })

    expect(result.current.error).toBe('Grading failed for student')
  })
})
