import React from 'react'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { vi, describe, it, expect, beforeEach } from 'vitest'

// Mock next/navigation
const mockPush = vi.fn()
const mockBack = vi.fn()
vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: mockPush, back: mockBack }),
  usePathname: () => '/sessions/new',
  useSearchParams: () => new URLSearchParams(),
}))

// Mock the API
vi.mock('@/lib/api', () => ({
  createSession: vi.fn(),
  generateRubric: vi.fn(),
}))

// Mock the stores used by AppShell/Sidebar/Topbar
vi.mock('@/stores/ui-store', () => ({
  useUIStore: Object.assign(
    (selector?: (state: Record<string, unknown>) => unknown) => {
      const state = {
        theme: 'dark',
        setTheme: vi.fn(),
        toggleSidebar: vi.fn(),
        sidebarOpen: true,
      }
      return typeof selector === 'function' ? selector(state) : state
    },
    { getState: () => ({ theme: 'dark', setTheme: vi.fn(), toggleSidebar: vi.fn(), sidebarOpen: true }) }
  ),
}))

// Mock QueryClientProvider used by Providers
vi.mock('@/components/providers', () => ({
  Providers: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}))

import { generateRubric } from '@/lib/api'
import NewSessionPage from '@/app/sessions/new/page'

const mockGenerateRubric = vi.mocked(generateRubric)

describe('Create Session Page - AI Rubric Generation', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('renders the generate with AI button', () => {
    render(<NewSessionPage />)
    expect(screen.getByTestId('generate-rubric-btn')).toBeInTheDocument()
    expect(screen.getByText('Generate with AI')).toBeInTheDocument()
  })

  it('generate button is disabled when description is empty', () => {
    render(<NewSessionPage />)
    expect(screen.getByTestId('generate-rubric-btn')).toBeDisabled()
  })

  it('shows hint when description is empty', () => {
    render(<NewSessionPage />)
    expect(screen.getByText(/Add a description above/)).toBeInTheDocument()
  })

  it('generate button is enabled when description has text', () => {
    render(<NewSessionPage />)
    const descTextarea = screen.getByPlaceholderText(/Optional description/)
    fireEvent.change(descTextarea, { target: { value: 'Binary search tree implementation' } })
    expect(screen.getByTestId('generate-rubric-btn')).not.toBeDisabled()
  })

  it('renders strictness select with default balanced', () => {
    render(<NewSessionPage />)
    const select = screen.getByTestId('strictness-select')
    expect(select).toBeInTheDocument()
    expect(select).toHaveValue('balanced')
  })

  it('calls generateRubric API and populates rubric on success', async () => {
    mockGenerateRubric.mockResolvedValueOnce({
      success: true,
      rubric_text: 'Code Quality (50): Correctness\nDesign (50): Architecture',
      criteria: [
        { criterion: 'Code Quality', max: 50, description: 'Correctness' },
        { criterion: 'Design', max: 50, description: 'Architecture' },
      ],
      strictness: 'balanced',
      max_score: 100,
      reasoning: 'Standard rubric',
      quality_warnings: [],
    })

    render(<NewSessionPage />)

    // Fill in description first
    const descTextarea = screen.getByPlaceholderText(/Optional description/)
    fireEvent.change(descTextarea, { target: { value: 'BST lab assignment' } })

    // Click generate
    fireEvent.click(screen.getByTestId('generate-rubric-btn'))

    await waitFor(() => {
      expect(mockGenerateRubric).toHaveBeenCalledWith('BST lab assignment', 100, 'balanced', 'balanced')
    })

    await waitFor(() => {
      const rubricTextarea = screen.getByTestId('rubric-input')
      expect(rubricTextarea).toHaveValue('Code Quality (50): Correctness\nDesign (50): Architecture')
    })
  })

  it('shows error message on generation failure', async () => {
    mockGenerateRubric.mockRejectedValueOnce(new Error('Network error'))

    render(<NewSessionPage />)

    const descTextarea = screen.getByPlaceholderText(/Optional description/)
    fireEvent.change(descTextarea, { target: { value: 'Some assignment' } })
    fireEvent.click(screen.getByTestId('generate-rubric-btn'))

    await waitFor(() => {
      expect(screen.getByText(/Failed to generate rubric/)).toBeInTheDocument()
    })
  })

  it('shows error when AI returns success=false', async () => {
    mockGenerateRubric.mockResolvedValueOnce({
      success: false,
      rubric_text: '',
      criteria: [],
      strictness: 'balanced',
      max_score: 100,
      reasoning: '',
      quality_warnings: [],
    })

    render(<NewSessionPage />)

    const descTextarea = screen.getByPlaceholderText(/Optional description/)
    fireEvent.change(descTextarea, { target: { value: 'Some assignment' } })
    fireEvent.click(screen.getByTestId('generate-rubric-btn'))

    await waitFor(() => {
      expect(screen.getByText(/AI could not generate/)).toBeInTheDocument()
    })
  })
})
