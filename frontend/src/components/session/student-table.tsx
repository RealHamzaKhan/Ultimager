'use client'

import { useState, useEffect, useCallback } from 'react'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { useStudents, type StudentFilters } from '@/hooks/use-students'
import { useUIStore } from '@/stores/ui-store'
import { scoreToGrade, formatScore, cn } from '@/lib/utils'
import { regradeStudent } from '@/lib/api'
import type { Submission } from '@/lib/types'
import { Search, Flag, ChevronUp, ChevronDown, RotateCcw, Loader2 } from 'lucide-react'
import Link from 'next/link'

interface StudentTableProps {
  sessionId: number
  maxScore: number
}

type StatusFilter = 'all' | 'pending' | 'grading' | 'graded' | 'error'
type SortColumn = 'student' | 'score' | 'grade' | 'status' | 'confidence'

const STATUS_TABS: { label: string; value: StatusFilter }[] = [
  { label: 'All', value: 'all' },
  { label: 'Pending', value: 'pending' },
  { label: 'Grading', value: 'grading' },
  { label: 'Graded', value: 'graded' },
  { label: 'Error', value: 'error' },
]

const STATUS_BADGE_VARIANT: Record<string, string> = {
  pending: 'default',
  grading: 'warning',
  graded: 'success',
  error: 'error',
}

const CONFIDENCE_BADGE_VARIANT: Record<string, string> = {
  high: 'success',
  medium: 'warning',
  low: 'error',
}

function getEffectiveScore(student: Submission): number | null {
  if (student.override_score != null) return student.override_score
  if (student.ai_score != null) return student.ai_score
  return null
}

export function StudentTable({ sessionId, maxScore }: StudentTableProps) {
  const [searchTerm, setSearchTerm] = useState('')
  const [debouncedSearch, setDebouncedSearch] = useState('')
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('all')
  const [isRegrading, setIsRegrading] = useState(false)

  const {
    selectedStudents,
    toggleStudentSelection,
    clearStudentSelection,
    selectAllStudents,
    setTableSort,
    tableSortColumn,
    tableSortDirection,
  } = useUIStore()

  const [regradeMessage, setRegradeMessage] = useState<string | null>(null)

  // Debounce search input
  useEffect(() => {
    const timer = setTimeout(() => {
      setDebouncedSearch(searchTerm)
    }, 300)
    return () => clearTimeout(timer)
  }, [searchTerm])

  const filters: StudentFilters = {
    search: debouncedSearch || undefined,
    status: statusFilter === 'all' ? undefined : statusFilter,
    sortBy: tableSortColumn ?? undefined,
    sortDir: tableSortDirection ?? undefined,
  }

  const { students, isLoading, refetch } = useStudents(sessionId, filters)

  const handleRegradeSelected = useCallback(async () => {
    if (selectedStudents.size === 0 || isRegrading) return
    setIsRegrading(true)
    setRegradeMessage(null)
    try {
      const ids = Array.from(selectedStudents)
      const count = ids.length
      let succeeded = 0
      let failed = 0
      // Send regrades sequentially to avoid backend 409 conflicts
      for (const studentId of ids) {
        try {
          await regradeStudent(sessionId, studentId)
          succeeded++
        } catch {
          failed++
        }
      }
      clearStudentSelection()
      await refetch()
      if (failed > 0) {
        setRegradeMessage(`Regrade started for ${succeeded}/${count} students (${failed} failed)`)
      } else {
        setRegradeMessage(`Regrade started for ${count} student${count > 1 ? 's' : ''}`)
      }
      setTimeout(() => setRegradeMessage(null), 5000)
    } catch (err) {
      console.error('Regrade selected failed:', err)
      setRegradeMessage('Regrade failed — check console for details')
      setTimeout(() => setRegradeMessage(null), 5000)
    } finally {
      setIsRegrading(false)
    }
  }, [selectedStudents, sessionId, isRegrading, clearStudentSelection, refetch])

  const handleSort = useCallback(
    (column: SortColumn) => {
      setTableSort(column)
    },
    [setTableSort]
  )

  const allSelected =
    students && students.length > 0 && students.every((s: Submission) => selectedStudents.has(s.id))

  const handleSelectAll = useCallback(() => {
    if (!students) return
    if (allSelected) {
      clearStudentSelection()
    } else {
      selectAllStudents(students.map((s: Submission) => s.id))
    }
  }, [students, allSelected, clearStudentSelection, selectAllStudents])

  const renderSortIcon = (column: SortColumn) => {
    if (tableSortColumn !== column) {
      return (
        <span className="ml-1 inline-flex flex-col opacity-30">
          <ChevronUp className="h-3 w-3" />
          <ChevronDown className="-mt-1 h-3 w-3" />
        </span>
      )
    }
    return tableSortDirection === 'asc' ? (
      <ChevronUp className="ml-1 h-3.5 w-3.5" />
    ) : (
      <ChevronDown className="ml-1 h-3.5 w-3.5" />
    )
  }

  const renderSortableHeader = (column: SortColumn, label: string) => (
    <th
      data-testid={`sort-${column}`}
      className="cursor-pointer select-none px-4 py-3 text-left text-sm font-medium"
      style={{ color: 'var(--text-muted)' }}
      onClick={() => handleSort(column)}
    >
      <span className="inline-flex items-center">
        {label}
        {renderSortIcon(column)}
      </span>
    </th>
  )

  return (
    <div data-testid="student-table" className="flex flex-col gap-4">
      {/* Search and filters */}
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="relative max-w-sm flex-1">
          <Search
            className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2"
            style={{ color: 'var(--text-muted)' }}
          />
          <Input
            data-testid="search-input"
            type="text"
            placeholder="Search students..."
            value={searchTerm}
            onChange={(e) => setSearchTerm(e.target.value)}
            className="pl-9"
          />
        </div>

        <div className="flex gap-1 rounded-lg p-1" style={{ backgroundColor: 'var(--bg-card)' }}>
          {STATUS_TABS.map((tab) => (
            <button
              key={tab.value}
              data-testid={`status-tab-${tab.value}`}
              onClick={() => setStatusFilter(tab.value)}
              className={cn(
                'rounded-md px-3 py-1.5 text-sm font-medium transition-colors',
                statusFilter === tab.value
                  ? 'bg-white/10 shadow-sm'
                  : 'hover:bg-white/5'
              )}
              style={{
                color:
                  statusFilter === tab.value
                    ? 'var(--text-primary)'
                    : 'var(--text-muted)',
              }}
            >
              {tab.label}
            </button>
          ))}
        </div>
      </div>

      {/* Batch action bar */}
      {selectedStudents.size > 0 && (
        <div
          data-testid="batch-bar"
          className="flex items-center justify-between rounded-lg border px-4 py-3"
          style={{
            borderColor: 'var(--border)',
            backgroundColor: 'var(--bg-card)',
          }}
        >
          <span className="text-sm" style={{ color: 'var(--text-secondary)' }}>
            {selectedStudents.size} student{selectedStudents.size !== 1 ? 's' : ''} selected
          </span>
          <Button
            data-testid="regrade-selected-btn"
            variant="outline"
            size="sm"
            className="gap-2"
            onClick={handleRegradeSelected}
            disabled={isRegrading}
          >
            {isRegrading ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <RotateCcw className="h-4 w-4" />
            )}
            {isRegrading ? 'Regrading...' : 'Regrade Selected'}
          </Button>
        </div>
      )}

      {/* Regrade status message */}
      {regradeMessage && (
        <div className="rounded-lg border border-indigo-500/30 bg-indigo-500/10 px-4 py-2 text-sm text-indigo-300">
          {regradeMessage}
        </div>
      )}

      {/* Table */}
      <div
        className="overflow-hidden rounded-lg border"
        style={{ borderColor: 'var(--border)' }}
      >
        <div className="overflow-x-auto">
          <table className="w-full">
            <thead style={{ backgroundColor: 'var(--bg-card)' }}>
              <tr
                className="border-b"
                style={{ borderColor: 'var(--border)' }}
              >
                <th className="w-12 px-4 py-3">
                  <input
                    data-testid="select-all-checkbox"
                    type="checkbox"
                    checked={!!allSelected}
                    onChange={handleSelectAll}
                    className="h-4 w-4 rounded border-gray-600 bg-transparent"
                  />
                </th>
                {renderSortableHeader('student', 'Student')}
                {renderSortableHeader('score', 'Score')}
                {renderSortableHeader('grade', 'Grade')}
                {renderSortableHeader('status', 'Status')}
                {renderSortableHeader('confidence', 'Confidence')}
                <th
                  className="px-4 py-3 text-left text-sm font-medium"
                  style={{ color: 'var(--text-muted)' }}
                >
                  Flags
                </th>
              </tr>
            </thead>
            <tbody>
              {isLoading ? (
                // Loading skeleton
                Array.from({ length: 5 }).map((_, i) => (
                  <tr
                    key={`skeleton-${i}`}
                    className="border-b"
                    style={{ borderColor: 'var(--border)' }}
                  >
                    <td className="px-4 py-3">
                      <div className="h-4 w-4 animate-pulse rounded bg-white/10" />
                    </td>
                    <td className="px-4 py-3">
                      <div className="h-4 w-32 animate-pulse rounded bg-white/10" />
                    </td>
                    <td className="px-4 py-3">
                      <div className="h-4 w-16 animate-pulse rounded bg-white/10" />
                    </td>
                    <td className="px-4 py-3">
                      <div className="h-4 w-12 animate-pulse rounded bg-white/10" />
                    </td>
                    <td className="px-4 py-3">
                      <div className="h-5 w-20 animate-pulse rounded-full bg-white/10" />
                    </td>
                    <td className="px-4 py-3">
                      <div className="h-5 w-16 animate-pulse rounded-full bg-white/10" />
                    </td>
                    <td className="px-4 py-3">
                      <div className="h-4 w-4 animate-pulse rounded bg-white/10" />
                    </td>
                  </tr>
                ))
              ) : students && students.length > 0 ? (
                students.map((student) => {
                  const score = getEffectiveScore(student)
                  const isOverridden = student.override_score != null
                  const grade = score != null ? scoreToGrade(score, maxScore) : null
                  const isSelected = selectedStudents.has(student.id)

                  return (
                    <tr
                      key={student.id}
                      data-testid={`student-row-${student.id}`}
                      className={cn(
                        'border-b transition-colors hover:bg-white/[0.02]',
                        isSelected && 'bg-white/[0.04]'
                      )}
                      style={{ borderColor: 'var(--border)' }}
                    >
                      <td className="px-4 py-3">
                        <input
                          data-testid={`student-checkbox-${student.id}`}
                          type="checkbox"
                          checked={isSelected}
                          onChange={(e) => {
                            e.stopPropagation()
                            toggleStudentSelection(student.id)
                          }}
                          className="h-4 w-4 rounded border-gray-600 bg-transparent"
                        />
                      </td>
                      <td className="px-4 py-3">
                        <Link
                          href={`/sessions/${sessionId}/students/${student.id}`}
                          className="text-sm font-medium hover:underline"
                          style={{ color: 'var(--text-primary)' }}
                        >
                          {student.student_identifier}
                        </Link>
                      </td>
                      <td className="px-4 py-3">
                        <span
                          className={cn(
                            'text-sm',
                            isOverridden && 'italic'
                          )}
                          style={{ color: 'var(--text-secondary)' }}
                          title={
                            isOverridden
                              ? `Overridden (original: ${formatScore(student.ai_score)})`
                              : undefined
                          }
                        >
                          {score != null
                            ? `${formatScore(score)} / ${maxScore}`
                            : '--'}
                        </span>
                      </td>
                      <td className="px-4 py-3">
                        <span
                          className="text-sm font-medium"
                          style={{ color: 'var(--text-primary)' }}
                        >
                          {grade ?? '--'}
                        </span>
                      </td>
                      <td className="px-4 py-3">
                        <Badge
                          variant={
                            (STATUS_BADGE_VARIANT[student.status] ?? 'default') as any
                          }
                        >
                          {student.status.charAt(0).toUpperCase() +
                            student.status.slice(1)}
                        </Badge>
                      </td>
                      <td className="px-4 py-3">
                        {student.ai_confidence ? (
                          <Badge
                            variant={
                              (CONFIDENCE_BADGE_VARIANT[student.ai_confidence] ??
                                'default') as any
                            }
                          >
                            {student.ai_confidence.charAt(0).toUpperCase() +
                              student.ai_confidence.slice(1)}
                          </Badge>
                        ) : (
                          <span
                            className="text-sm"
                            style={{ color: 'var(--text-muted)' }}
                          >
                            --
                          </span>
                        )}
                      </td>
                      <td className="px-4 py-3">
                        {student.is_flagged && (
                          <span
                            title={student.flag_reason || 'Flagged for review'}
                            className="inline-flex items-center gap-1.5 cursor-help"
                          >
                            <Flag className="h-3.5 w-3.5 text-amber-500 shrink-0" />
                            <span className="text-xs text-amber-500/80 max-w-[140px] truncate">
                              {student.flag_reason || 'Review'}
                            </span>
                          </span>
                        )}
                      </td>
                    </tr>
                  )
                })
              ) : (
                // Empty state
                <tr>
                  <td colSpan={7} className="px-4 py-12 text-center">
                    <p
                      className="text-sm"
                      style={{ color: 'var(--text-muted)' }}
                    >
                      No students found
                    </p>
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  )
}
