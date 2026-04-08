import React from 'react'
import { render, screen, fireEvent } from '@testing-library/react'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { vi, describe, it, expect, beforeEach } from 'vitest'
import { OverrideForm } from '@/components/student/override-form'

const mockMutate = vi.fn()

vi.mock('@/hooks/use-mutations', () => ({
  useOverrideScore: () => ({
    mutate: mockMutate,
    isPending: false,
    isError: false,
    isSuccess: false,
  }),
}))

function createWrapper() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={qc}>{children}</QueryClientProvider>
  )
}

describe('OverrideForm', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('renders the form', () => {
    render(
      <OverrideForm sessionId={1} studentId={1} currentScore={85} maxScore={100}
        isOverridden={false} overrideScore={null} overrideComments="" grade="B" />,
      { wrapper: createWrapper() }
    )
    expect(screen.getByText('Manual Score Override')).toBeInTheDocument()
    expect(screen.getByText('Save Override')).toBeInTheDocument()
  })

  it('shows AI score info', () => {
    render(
      <OverrideForm sessionId={1} studentId={1} currentScore={85} maxScore={100}
        isOverridden={false} overrideScore={null} overrideComments="" grade="B" />,
      { wrapper: createWrapper() }
    )
    expect(screen.getByText('AI Score')).toBeInTheDocument()
  })

  it('shows existing override info', () => {
    render(
      <OverrideForm sessionId={1} studentId={1} currentScore={85} maxScore={100}
        isOverridden={true} overrideScore={90} overrideComments="Adjusted for late submission" grade="B" />,
      { wrapper: createWrapper() }
    )
    // The override section should appear when isOverridden=true
    const overrideLabels = screen.getAllByText('Override')
    expect(overrideLabels.length).toBeGreaterThanOrEqual(1)
  })

  it('submits valid override', () => {
    render(
      <OverrideForm sessionId={1} studentId={1} currentScore={null} maxScore={100}
        isOverridden={false} overrideScore={null} overrideComments="" grade="B" />,
      { wrapper: createWrapper() }
    )
    // When currentScore is null, score input starts empty
    const scoreInput = screen.getByRole('spinbutton')
    const commentsInput = screen.getByPlaceholderText('Reason for score override...')
    fireEvent.change(scoreInput, { target: { value: '90' } })
    fireEvent.change(commentsInput, { target: { value: 'Good work' } })
    fireEvent.click(screen.getByText('Save Override'))
    expect(mockMutate).toHaveBeenCalledWith({
      sessionId: 1,
      studentId: 1,
      payload: { score: 90, comments: 'Good work', is_reviewed: false },
    })
  })

  it('has override comments input', () => {
    render(
      <OverrideForm sessionId={1} studentId={1} currentScore={85} maxScore={100}
        isOverridden={false} overrideScore={null} overrideComments="" grade="B" />,
      { wrapper: createWrapper() }
    )
    expect(screen.getByPlaceholderText('Reason for score override...')).toBeInTheDocument()
  })

  it('shows score summary section', () => {
    render(
      <OverrideForm sessionId={1} studentId={1} currentScore={85} maxScore={100}
        isOverridden={false} overrideScore={null} overrideComments="" grade="B" />,
      { wrapper: createWrapper() }
    )
    expect(screen.getByText('Score Summary')).toBeInTheDocument()
  })
})
