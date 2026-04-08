import React from 'react'
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { vi, describe, it, expect, beforeEach } from 'vitest'
import { FileBrowser } from '@/components/student/file-browser'
import type { StudentFile } from '@/lib/types'

const mockFiles: StudentFile[] = [
  { filename: 'main.py', type: 'python', display_type: 'code', size: 1024, extension: '.py', exists: true, view_url: '/api/files/1' },
  { filename: 'readme.txt', type: 'text', display_type: 'text', size: 256, extension: '.txt', exists: true, view_url: '/api/files/2' },
]

// Mock fetch for file content loading
const mockFetch = vi.fn()

beforeEach(() => {
  vi.clearAllMocks()
  global.fetch = mockFetch
  mockFetch.mockResolvedValue({
    ok: true,
    text: () => Promise.resolve('print("hello")'),
  })
})

describe('FileBrowser', () => {
  it('renders file list', () => {
    render(<FileBrowser sessionId={1} studentId={1} files={mockFiles} />)
    expect(screen.getAllByText('main.py').length).toBeGreaterThanOrEqual(1)
    expect(screen.getAllByText('readme.txt').length).toBeGreaterThanOrEqual(1)
  })

  it('shows first file content by default', async () => {
    render(<FileBrowser sessionId={1} studentId={1} files={mockFiles} />)
    await waitFor(() => {
      expect(screen.getByText('print("hello")')).toBeInTheDocument()
    })
  })

  it('switches file content when clicking a different file', async () => {
    mockFetch
      .mockResolvedValueOnce({ ok: true, text: () => Promise.resolve('print("hello")') })
      .mockResolvedValueOnce({ ok: true, text: () => Promise.resolve('Project description') })
    render(<FileBrowser sessionId={1} studentId={1} files={mockFiles} />)
    await waitFor(() => {
      expect(screen.getByText('print("hello")')).toBeInTheDocument()
    })
    fireEvent.click(screen.getByText('readme.txt'))
    await waitFor(() => {
      expect(screen.getByText('Project description')).toBeInTheDocument()
    })
  })

  it('shows empty state when no files', () => {
    render(<FileBrowser sessionId={1} studentId={1} files={[]} />)
    expect(screen.getByText('No files submitted.')).toBeInTheDocument()
  })

  it('displays file size when available', () => {
    render(<FileBrowser sessionId={1} studentId={1} files={mockFiles} />)
    expect(screen.getByText('1.0 KB')).toBeInTheDocument()
  })

  it('shows file count', () => {
    render(<FileBrowser sessionId={1} studentId={1} files={mockFiles} />)
    expect(screen.getByText('Files (2)')).toBeInTheDocument()
  })
})
