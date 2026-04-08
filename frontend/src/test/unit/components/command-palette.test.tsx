import React from 'react'
import { render, screen, fireEvent, act } from '@testing-library/react'
import { vi, describe, it, expect, beforeEach } from 'vitest'
import CommandPalette from '@/components/layout/command-palette'

const mockPush = vi.fn()

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: mockPush }),
  usePathname: () => '/',
}))

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

describe('CommandPalette', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('does not render when closed', () => {
    render(<CommandPalette />)
    expect(screen.queryByTestId('command-palette')).not.toBeInTheDocument()
  })

  it('opens on Cmd+K keydown', () => {
    render(<CommandPalette />)
    act(() => {
      fireEvent.keyDown(document, { key: 'k', metaKey: true })
    })
    expect(screen.getByTestId('command-palette')).toBeInTheDocument()
  })

  it('shows search input when open', () => {
    render(<CommandPalette />)
    act(() => {
      fireEvent.keyDown(document, { key: 'k', metaKey: true })
    })
    expect(screen.getByTestId('command-input')).toBeInTheDocument()
  })

  it('shows command items', () => {
    render(<CommandPalette />)
    act(() => {
      fireEvent.keyDown(document, { key: 'k', metaKey: true })
    })
    expect(screen.getByText('Go to Dashboard')).toBeInTheDocument()
    expect(screen.getByText('Create New Session')).toBeInTheDocument()
    expect(screen.getByText('Toggle Theme')).toBeInTheDocument()
  })

  it('filters commands by search query', () => {
    render(<CommandPalette />)
    act(() => {
      fireEvent.keyDown(document, { key: 'k', metaKey: true })
    })
    fireEvent.change(screen.getByTestId('command-input'), { target: { value: 'dashboard' } })
    expect(screen.getByText('Go to Dashboard')).toBeInTheDocument()
    expect(screen.queryByText('Create New Session')).not.toBeInTheDocument()
  })

  it('closes on Escape', () => {
    render(<CommandPalette />)
    act(() => {
      fireEvent.keyDown(document, { key: 'k', metaKey: true })
    })
    expect(screen.getByTestId('command-palette')).toBeInTheDocument()
    fireEvent.keyDown(screen.getByTestId('command-palette'), { key: 'Escape' })
    expect(screen.queryByTestId('command-palette')).not.toBeInTheDocument()
  })

  it('navigates on Enter', () => {
    render(<CommandPalette />)
    act(() => {
      fireEvent.keyDown(document, { key: 'k', metaKey: true })
    })
    fireEvent.keyDown(screen.getByTestId('command-palette'), { key: 'Enter' })
    expect(mockPush).toHaveBeenCalledWith('/')
  })

  it('closes on backdrop click', () => {
    render(<CommandPalette />)
    act(() => {
      fireEvent.keyDown(document, { key: 'k', metaKey: true })
    })
    fireEvent.click(screen.getByTestId('command-backdrop'))
    expect(screen.queryByTestId('command-palette')).not.toBeInTheDocument()
  })
})
