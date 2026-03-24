'use client'

import Link from 'next/link'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import { ChevronLeft, ChevronRight, Users } from 'lucide-react'

interface StudentNavProps {
  sessionId: number
  students: { id: number; student_identifier: string }[]
  currentStudentId: number
}

export function StudentNav({ sessionId, students, currentStudentId }: StudentNavProps) {
  const currentIndex = students.findIndex((s) => s.id === currentStudentId)
  const prevStudent = currentIndex > 0 ? students[currentIndex - 1] : null
  const nextStudent = currentIndex < students.length - 1 ? students[currentIndex + 1] : null

  return (
    <div className="flex items-center justify-between rounded-lg border border-[var(--border)] bg-[var(--bg-card)] px-4 py-2">
      {/* Previous */}
      <div className="w-40">
        {prevStudent ? (
          <Link href={`/sessions/${sessionId}/students/${prevStudent.id}`}>
            <Button variant="ghost" size="sm" className="h-8 gap-1.5 text-xs">
              <ChevronLeft className="h-3.5 w-3.5" />
              <span className="truncate max-w-[100px]">{prevStudent.student_identifier}</span>
            </Button>
          </Link>
        ) : (
          <div />
        )}
      </div>

      {/* Center */}
      <div className="flex items-center gap-2 text-sm">
        <Users className="h-3.5 w-3.5 text-[var(--text-muted)]" />
        <span className="text-[var(--text-muted)]">
          <span className="font-semibold text-[var(--text-primary)]">{currentIndex + 1}</span>
          {' '}of{' '}
          <span className="font-semibold text-[var(--text-primary)]">{students.length}</span>
        </span>
      </div>

      {/* Next */}
      <div className="w-40 flex justify-end">
        {nextStudent ? (
          <Link href={`/sessions/${sessionId}/students/${nextStudent.id}`}>
            <Button variant="ghost" size="sm" className="h-8 gap-1.5 text-xs">
              <span className="truncate max-w-[100px]">{nextStudent.student_identifier}</span>
              <ChevronRight className="h-3.5 w-3.5" />
            </Button>
          </Link>
        ) : (
          <div />
        )}
      </div>
    </div>
  )
}
