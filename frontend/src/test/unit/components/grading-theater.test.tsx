import React from 'react'
import { render, screen, fireEvent } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { vi, describe, it, expect, beforeEach } from 'vitest'
import { GradingTheater } from '@/components/session/grading-theater'

const mockDisconnect = vi.fn()
const mockReconnect = vi.fn()
const mockMutate = vi.fn()

let mockStreamState = {
  connected: true,
  graded: 5,
  failed: 0,
  total: 10,
  currentStudent: 'alice',
  stage: 'Grading alice...',
  error: null as string | null,
  isComplete: false,
  isStopped: false,
  completedStudents: [] as Array<{
    id: number; name: string; score: number | null; grade: string;
    confidence: string; status: 'graded' | 'error'; timestamp: number;
  }>,
  elapsed: 120,
  disconnect: mockDisconnect,
  reconnect: mockReconnect,
}

vi.mock('@/hooks/use-grade-stream', () => ({
  useGradeStream: () => mockStreamState,
}))

vi.mock('@/hooks/use-mutations', () => ({
  useStopGrading: () => ({
    mutate: mockMutate,
    isPending: false,
  }),
}))

function createWrapper() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  )
}

describe('GradingTheater', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockStreamState = {
      connected: true,
      graded: 5,
      failed: 0,
      total: 10,
      currentStudent: 'alice',
      stage: 'Grading alice...',
      error: null,
      isComplete: false,
      isStopped: false,
      completedStudents: [],
      elapsed: 120,
      disconnect: mockDisconnect,
      reconnect: mockReconnect,
    }
  })

  it('renders the grading theater', () => {
    render(<GradingTheater sessionId={1} />, { wrapper: createWrapper() })
    expect(screen.getByTestId('grading-theater')).toBeInTheDocument()
  })

  it('shows progress bar with correct percentage', () => {
    render(<GradingTheater sessionId={1} />, { wrapper: createWrapper() })
    expect(screen.getByTestId('progress-bar')).toBeInTheDocument()
    expect(screen.getByText('50%')).toBeInTheDocument()
    expect(screen.getByText('5/10 students')).toBeInTheDocument()
  })

  it('shows current student name', () => {
    render(<GradingTheater sessionId={1} />, { wrapper: createWrapper() })
    expect(screen.getByText('alice')).toBeInTheDocument()
  })

  it('shows AI Grading label', () => {
    render(<GradingTheater sessionId={1} />, { wrapper: createWrapper() })
    expect(screen.getByText('AI Grading')).toBeInTheDocument()
  })

  it('shows elapsed time', () => {
    render(<GradingTheater sessionId={1} />, { wrapper: createWrapper() })
    expect(screen.getByText('Elapsed')).toBeInTheDocument()
    // 2:00 may appear multiple times (elapsed + ETA)
    expect(screen.getAllByText('2:00').length).toBeGreaterThanOrEqual(1)
  })

  it('shows complete state when grading complete', () => {
    mockStreamState.isComplete = true
    mockStreamState.graded = 10
    render(<GradingTheater sessionId={1} />, { wrapper: createWrapper() })
    expect(screen.getByText('Grading Complete')).toBeInTheDocument()
  })

  it('calls onComplete when grading finishes', () => {
    const onComplete = vi.fn()
    mockStreamState.isComplete = true
    render(<GradingTheater sessionId={1} onComplete={onComplete} />, { wrapper: createWrapper() })
    expect(onComplete).toHaveBeenCalled()
  })

  it('shows error banner when error exists', () => {
    mockStreamState.error = 'Stream failed'
    render(<GradingTheater sessionId={1} />, { wrapper: createWrapper() })
    expect(screen.getByText('Stream failed')).toBeInTheDocument()
  })

  it('stop button calls stopGrading mutation', () => {
    render(<GradingTheater sessionId={1} />, { wrapper: createWrapper() })
    fireEvent.click(screen.getByTestId('stop-btn'))
    expect(mockMutate).toHaveBeenCalledWith({ sessionId: 1 })
  })

  it('shows live results when students are completed', () => {
    mockStreamState.completedStudents = [
      { id: 1, name: 'Bob', score: 85, grade: 'B+', confidence: 'high', status: 'graded', timestamp: Date.now() },
      { id: 2, name: 'Carol', score: 92, grade: 'A-', confidence: 'high', status: 'graded', timestamp: Date.now() },
    ]
    render(<GradingTheater sessionId={1} />, { wrapper: createWrapper() })
    expect(screen.getByText('Live Results')).toBeInTheDocument()
    expect(screen.getByText('Bob')).toBeInTheDocument()
    expect(screen.getByText('Carol')).toBeInTheDocument()
  })
})
