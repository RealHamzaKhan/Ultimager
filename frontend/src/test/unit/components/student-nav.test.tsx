import React from 'react'
import { render, screen } from '@testing-library/react'
import { vi, describe, it, expect } from 'vitest'
import { StudentNav } from '@/components/student/student-nav'

vi.mock('next/link', () => ({
  default: ({ children, href }: { children: React.ReactNode; href: string }) => (
    <a href={href}>{children}</a>
  ),
}))

const mockStudents = [
  { id: 1, student_identifier: 'alice' },
  { id: 2, student_identifier: 'bob' },
  { id: 3, student_identifier: 'charlie' },
]

describe('StudentNav', () => {
  it('renders position indicator', () => {
    render(<StudentNav sessionId={1} students={mockStudents} currentStudentId={2} />)
    expect(screen.getByText('2')).toBeInTheDocument()
    expect(screen.getByText('3')).toBeInTheDocument()
    expect(screen.getByText('of')).toBeInTheDocument()
  })

  it('shows prev and next student names for middle student', () => {
    render(<StudentNav sessionId={1} students={mockStudents} currentStudentId={2} />)
    expect(screen.getByText('alice')).toBeInTheDocument()
    expect(screen.getByText('charlie')).toBeInTheDocument()
  })

  it('hides prev button at first student', () => {
    render(<StudentNav sessionId={1} students={mockStudents} currentStudentId={1} />)
    expect(screen.queryByText('alice')).not.toBeInTheDocument()
    expect(screen.getByText('bob')).toBeInTheDocument()
  })

  it('hides next button at last student', () => {
    render(<StudentNav sessionId={1} students={mockStudents} currentStudentId={3} />)
    expect(screen.getByText('bob')).toBeInTheDocument()
    expect(screen.queryByText('charlie')).not.toBeInTheDocument()
  })

  it('prev link points to correct student', () => {
    render(<StudentNav sessionId={1} students={mockStudents} currentStudentId={2} />)
    const prevLink = screen.getByText('alice').closest('a')
    expect(prevLink?.getAttribute('href')).toBe('/sessions/1/students/1')
  })

  it('next link points to correct student', () => {
    render(<StudentNav sessionId={1} students={mockStudents} currentStudentId={2} />)
    const nextLink = screen.getByText('charlie').closest('a')
    expect(nextLink?.getAttribute('href')).toBe('/sessions/1/students/3')
  })
})
