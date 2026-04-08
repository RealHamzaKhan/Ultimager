'use client'

import { useState, useMemo, useCallback } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { useStudents } from '@/hooks/use-students'
import { regradeStudent, fetchStudent } from '@/lib/api'
import type { Submission } from '@/lib/types'
import { Search, RotateCcw } from 'lucide-react'
import { cn } from '@/lib/utils'

interface StudentSidebarProps {
  sessionId: number
  maxScore: number
  selectedId: number | null
  onSelect: (id: number) => void
}

function getUrgencyLevel(s: Submission): 0 | 1 | 2 | 3 {
  if (s.status === 'error' || s.error_message) return 0
  if (s.is_flagged) return 0
  const score = s.override_score ?? s.ai_score ?? null
  if (score === null && s.status !== 'graded') return 0
  if (score !== null) {
    const pct = score // scores are already out of max; we compare below per-student
    // we'll sort by raw score ascending — low scores bubble up
  }
  if (!s.is_reviewed) return 1
  return 3
}

function getEffectiveScore(s: Submission): number | null {
  return s.override_score ?? s.ai_score ?? null
}

function getDot(s: Submission, maxScore: number): { color: string; glow: string; label: string } {
  // Errors = truly broken grading attempt → always red
  if (s.status === 'error' || (s.error_message && s.error_message.trim()))
    return { color: '#ef4444', glow: '#ef444430', label: 'Error' }
  const score = getEffectiveScore(s)
  if (score === null) {
    if (s.status === 'grading') return { color: '#6366f1', glow: '#6366f130', label: 'Grading' }
    return { color: '#555', glow: 'transparent', label: 'Pending' }
  }
  // Color reflects actual score — is_flagged shows as a separate ⚠ indicator
  const pct = maxScore > 0 ? score / maxScore : 0
  if (pct >= 0.65) return { color: '#22c55e', glow: '#22c55e30', label: 'Good' }
  if (pct >= 0.40) return { color: '#f97316', glow: '#f9731630', label: 'Partial' }
  return { color: '#ef4444', glow: '#ef444430', label: 'Low' }
}

function shortName(identifier: string): string {
  // Strip common prefixes like "22p-9194_Farooq umer_midexam" → "Farooq Umer"
  const parts = identifier.replace(/[-_]/g, ' ').split(' ')
  // Filter out parts that look like IDs (purely numeric or short alphanumeric with digits)
  const nameParts = parts.filter(p => {
    if (p.length === 0) return false
    // skip if it looks like a student ID (e.g. "22p", "9194", "6a")
    if (/^[0-9]+$/.test(p)) return false
    if (/^\d+[a-z]$/i.test(p)) return false
    if (/^[a-z]+\d+/i.test(p) && p.length <= 5) return false
    return true
  })
  return nameParts.join(' ') || identifier
}

// Small badge showing AI confidence level for a student row
function ConfidenceDot({ confidence, isSelected }: { confidence?: string; isSelected: boolean }) {
  if (!confidence || confidence === 'high') return null
  const color = confidence === 'low' ? '#ef4444' : '#f97316'
  const label = confidence === 'low' ? 'Low AI confidence' : 'Medium AI confidence'
  return (
    <div title={label} style={{
      width: 5, height: 5, borderRadius: '50%', flexShrink: 0,
      background: color, opacity: isSelected ? 1 : 0.7,
    }} />
  )
}

type FilterTab = 'all' | 'attention' | 'reviewed'

export function StudentSidebar({ sessionId, maxScore, selectedId, onSelect }: StudentSidebarProps) {
  const [search, setSearch] = useState('')
  const [filter, setFilter] = useState<FilterTab>('all')
  const [hoveredId, setHoveredId] = useState<number | null>(null)
  const [regradingIds, setRegradingIds] = useState<Set<number>>(new Set())
  const queryClient = useQueryClient()
  const { students, isLoading } = useStudents(sessionId)

  const sorted = useMemo(() => {
    if (!students) return []
    let list = [...students]

    // Filter by search
    if (search.trim()) {
      const q = search.toLowerCase()
      list = list.filter(s => s.student_identifier.toLowerCase().includes(q))
    }

    // Filter by tab
    if (filter === 'attention') {
      // "Needs attention" = flagged, errors, or AI says low confidence
      list = list.filter(s =>
        s.status === 'error' || !!s.error_message || s.is_flagged || s.ai_confidence === 'low'
      )
    } else if (filter === 'reviewed') {
      list = list.filter(s => s.is_reviewed)
    }

    // Sort: errors/flagged first, then unreviewed low scores, then by score asc, then reviewed
    list.sort((a, b) => {
      const aScore = getEffectiveScore(a) ?? Infinity
      const bScore = getEffectiveScore(b) ?? Infinity
      const aErr = a.status === 'error' || !!a.error_message || a.is_flagged
      const bErr = b.status === 'error' || !!b.error_message || b.is_flagged
      if (aErr && !bErr) return -1
      if (!aErr && bErr) return 1
      // Then unreviewed before reviewed
      if (!a.is_reviewed && b.is_reviewed) return -1
      if (a.is_reviewed && !b.is_reviewed) return 1
      // Then by score ascending (lowest first needs attention)
      return aScore - bScore
    })

    return list
  }, [students, search, filter, maxScore])

  // Regrade a single student from the sidebar
  const handleRegrade = useCallback(async (e: React.MouseEvent, studentId: number) => {
    e.stopPropagation() // don't select the student row
    if (regradingIds.has(studentId)) return
    setRegradingIds(prev => new Set(prev).add(studentId))
    try {
      await regradeStudent(sessionId, studentId)
      // Poll until grading completes or times out (max ~2 min)
      let attempts = 0
      const poll = setInterval(async () => {
        attempts++
        try {
          const updated = await fetchStudent(sessionId, studentId)
          if (updated.status === 'graded' || updated.status === 'error' || attempts > 60) {
            clearInterval(poll)
            queryClient.invalidateQueries({ queryKey: ['students', sessionId] })
            queryClient.invalidateQueries({ queryKey: ['student', sessionId, studentId] })
            setRegradingIds(prev => {
              const next = new Set(prev)
              next.delete(studentId)
              return next
            })
          }
        } catch {
          // poll errors are non-fatal
        }
      }, 2000)
    } catch {
      setRegradingIds(prev => {
        const next = new Set(prev)
        next.delete(studentId)
        return next
      })
    }
  }, [sessionId, regradingIds, queryClient])

  // Section counts
  const reviewCount = useMemo(() =>
    students?.filter(s => !s.is_reviewed && s.status === 'graded').length ?? 0,
    [students]
  )
  const errorCount = useMemo(() =>
    students?.filter(s => s.status === 'error' || s.is_flagged).length ?? 0,
    [students]
  )
  // Students where AI confidence is explicitly low — needs teacher attention
  const uncertainCount = useMemo(() =>
    students?.filter(s => s.status === 'graded' && s.ai_confidence === 'low' && !s.is_flagged).length ?? 0,
    [students]
  )

  return (
    <div
      style={{
        width: 248,
        flexShrink: 0,
        background: 'var(--bg-sidebar)',
        borderRight: '1px solid var(--border)',
        borderRadius: '12px 0 0 12px',
        display: 'flex',
        flexDirection: 'column',
        position: 'sticky',
        top: 76,
        maxHeight: 'calc(100vh - 88px)',
        overflow: 'hidden',
        zIndex: 10,
      }}
    >
      {/* Search */}
      <div style={{ padding: '12px 10px 8px', borderBottom: '1px solid var(--border)' }}>
        <div style={{ position: 'relative' }}>
          <Search
            style={{
              position: 'absolute', left: 8, top: '50%', transform: 'translateY(-50%)',
              width: 13, height: 13, color: '#555', pointerEvents: 'none',
            }}
          />
          <input
            value={search}
            onChange={e => setSearch(e.target.value)}
            placeholder="Filter students…"
            style={{
              width: '100%',
              background: '#0e0e1a',
              border: '1px solid var(--border)',
              borderRadius: 6,
              padding: '5px 8px 5px 26px',
              fontSize: 11,
              color: '#fff',
              outline: 'none',
            }}
          />
        </div>
      </div>

      {/* Filter tabs */}
      {students && students.length > 0 && (() => {
        const attentionCount = students.filter(s =>
          s.status === 'error' || !!s.error_message || s.is_flagged || s.ai_confidence === 'low'
        ).length
        const reviewedCount = students.filter(s => s.is_reviewed).length
        const tabs: { key: FilterTab; label: string; count?: number }[] = [
          { key: 'all', label: 'All', count: students.length },
          { key: 'attention', label: 'Issues', count: attentionCount },
          { key: 'reviewed', label: 'Done', count: reviewedCount },
        ]
        return (
          <div style={{ padding: '6px 8px', display: 'flex', gap: 4, borderBottom: '1px solid var(--border)' }}>
            {tabs.map(tab => (
              <button
                key={tab.key}
                onClick={() => setFilter(tab.key)}
                style={{
                  flex: 1, background: filter === tab.key ? '#141420' : 'transparent',
                  border: filter === tab.key ? '1px solid #6366f140' : '1px solid transparent',
                  borderRadius: 6, padding: '3px 0',
                  fontSize: 10, color: filter === tab.key ? '#818cf8' : '#555',
                  cursor: 'pointer', fontWeight: filter === tab.key ? 600 : 400,
                }}
              >
                {tab.label}
                {tab.count !== undefined && (
                  <span style={{ marginLeft: 3, opacity: 0.7 }}>({tab.count})</span>
                )}
              </button>
            ))}
          </div>
        )
      })()}

      {/* Summary chips — actionable counts the teacher actually cares about */}
      {(errorCount > 0 || uncertainCount > 0 || reviewCount > 0) && (
        <div style={{ padding: '6px 10px', display: 'flex', gap: 5, flexWrap: 'wrap', borderBottom: '1px solid var(--border)' }}>
          {errorCount > 0 && (
            <div title="Flagged or error students — open each to investigate" style={{
              fontSize: 10, padding: '2px 7px', borderRadius: 99,
              background: '#ef444418', color: '#ef4444',
              border: '1px solid #ef444430', fontWeight: 600,
            }}>
              {errorCount} flagged
            </div>
          )}
          {uncertainCount > 0 && (
            <div title="AI confidence is low — verify before approving" style={{
              fontSize: 10, padding: '2px 7px', borderRadius: 99,
              background: '#f9731618', color: '#f97316',
              border: '1px solid #f9731630', fontWeight: 600,
            }}>
              {uncertainCount} uncertain
            </div>
          )}
          {reviewCount > 0 && (
            <div title="Graded but not yet reviewed by teacher" style={{
              fontSize: 10, padding: '2px 7px', borderRadius: 99,
              background: '#6366f118', color: '#818cf8',
              border: '1px solid #6366f130', fontWeight: 600,
            }}>
              {reviewCount} to review
            </div>
          )}
        </div>
      )}

      {/* List */}
      <div style={{ flex: 1, overflowY: 'auto', padding: '4px 0' }}>
        {isLoading && (
          <div style={{ padding: '20px 14px' }}>
            {[...Array(6)].map((_, i) => (
              <div key={i} style={{
                height: 36, background: '#111', borderRadius: 6,
                marginBottom: 4, opacity: 0.5 + i * 0.05,
              }} />
            ))}
          </div>
        )}
        {!isLoading && sorted.length === 0 && (
          <div style={{ padding: '24px 14px', textAlign: 'center', color: '#555', fontSize: 12 }}>
            No students found
          </div>
        )}
        {sorted.map(s => {
          const dot = getDot(s, maxScore)
          const score = getEffectiveScore(s)
          const name = shortName(s.student_identifier)
          const isSelected = s.id === selectedId
          const isHovered = hoveredId === s.id
          const isRegrading = regradingIds.has(s.id)
          // Show regrade button when hovering or regrading, and student has been graded/errored
          const showRegrade = (isHovered || isRegrading) && (s.status === 'graded' || s.status === 'error')
          return (
            <button
              key={s.id}
              onClick={() => onSelect(s.id)}
              onMouseEnter={() => setHoveredId(s.id)}
              onMouseLeave={() => setHoveredId(null)}
              style={{
                width: '100%',
                display: 'flex',
                alignItems: 'center',
                gap: 8,
                padding: '6px 10px',
                background: isSelected ? '#141420' : isHovered ? '#0e0e1a' : 'transparent',
                border: 'none',
                borderLeft: isSelected ? '2px solid #6366f1' : '2px solid transparent',
                cursor: 'pointer',
                textAlign: 'left',
                transition: 'background 0.1s',
                position: 'relative',
              }}
            >
              {/* Status dot */}
              <div style={{
                width: 7, height: 7, borderRadius: '50%', flexShrink: 0,
                background: dot.color,
                boxShadow: isSelected ? `0 0 6px ${dot.color}` : `0 0 4px ${dot.color}60`,
              }} />

              {/* Name */}
              <div style={{
                flex: 1,
                fontSize: 12,
                color: isSelected ? '#fff' : '#aaa',
                fontWeight: isSelected ? 500 : 400,
                overflow: 'hidden',
                textOverflow: 'ellipsis',
                whiteSpace: 'nowrap',
              }}>
                {name}
                {s.is_flagged && (
                  <span style={{ marginLeft: 4, fontSize: 9, color: '#f97316', verticalAlign: 'middle' }}>⚠</span>
                )}
              </div>

              {/* AI confidence dot (only for medium/low — high is implicit) */}
              <ConfidenceDot confidence={s.ai_confidence} isSelected={isSelected} />

              {/* Regrade button — shown on hover */}
              {showRegrade ? (
                <button
                  onClick={(e) => handleRegrade(e, s.id)}
                  disabled={isRegrading}
                  title="Regrade this student"
                  style={{
                    background: 'transparent', border: 'none', padding: '2px 3px',
                    cursor: isRegrading ? 'not-allowed' : 'pointer', flexShrink: 0,
                    display: 'flex', alignItems: 'center', borderRadius: 4,
                    color: isRegrading ? '#6366f1' : '#555',
                  }}
                >
                  <RotateCcw style={{
                    width: 11, height: 11,
                    animation: isRegrading ? 'spin 1s linear infinite' : 'none',
                  }} />
                </button>
              ) : (
                <>
                  {/* Score — hidden when regrade button is shown */}
                  <div style={{
                    fontFamily: 'var(--font-mono), monospace',
                    fontSize: 11,
                    color: isSelected ? dot.color : dot.color + 'aa',
                    flexShrink: 0,
                    fontWeight: 500,
                  }}>
                    {score !== null ? score.toFixed(1) : '—'}
                  </div>

                  {/* Reviewed checkmark */}
                  {s.is_reviewed && (
                    <div style={{ color: '#22c55e', fontSize: 10, flexShrink: 0 }}>✓</div>
                  )}
                </>
              )}
            </button>
          )
        })}
      </div>

      {/* Footer count */}
      <div style={{
        padding: '8px 12px',
        borderTop: '1px solid var(--border)',
        fontSize: 10,
        color: '#555',
        display: 'flex',
        justifyContent: 'space-between',
      }}>
        <span>{sorted.length} students</span>
        <span>{students?.filter(s => s.is_reviewed).length ?? 0} reviewed</span>
      </div>

      <style>{`@keyframes spin { from { transform: rotate(0deg) } to { transform: rotate(360deg) } }`}</style>
    </div>
  )
}
