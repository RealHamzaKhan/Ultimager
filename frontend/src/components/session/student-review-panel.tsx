'use client'

import { useState, useCallback, useEffect } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { fetchStudent, overrideScore, regradeStudent } from '@/lib/api'
import type { CriterionScore, CheckpointResult } from '@/lib/types'
import { ChevronDown, ChevronRight, RotateCcw, ExternalLink, AlertCircle, CheckCircle2, HelpCircle, TrendingDown } from 'lucide-react'
import Link from 'next/link'

interface StudentReviewPanelProps {
  sessionId: number
  studentId: number
  maxScore: number
  onNext: () => void
  onApproved: () => void
}

function pct(score: number, max: number) {
  if (max <= 0) return 0
  return Math.min(100, (score / max) * 100)
}

function barColor(score: number, max: number): string {
  const p = pct(score, max)
  if (p >= 65) return '#22c55e'
  if (p >= 40) return '#f97316'
  return '#ef4444'
}

function shortName(identifier: string): string {
  const parts = identifier.replace(/[-_]/g, ' ').split(' ')
  const nameParts = parts.filter(p => {
    if (p.length === 0) return false
    if (/^[0-9]+$/.test(p)) return false
    if (/^\d+[a-z]$/i.test(p)) return false
    if (/^[a-z]+\d+/i.test(p) && p.length <= 5) return false
    return true
  })
  return nameParts.join(' ') || identifier
}

// ── Convert raw AI flags to human-readable, deduplicated messages ────────────
function processFlags(flags: string[]): { serious: string[]; minor: string[] } {
  const seen = new Set<string>()
  const serious: string[] = []
  const minor: string[] = []

  // Extract criterion-level flags (deduplicate per criterion)
  const criterionFlags = new Map<string, string>()
  const summaryFlags: string[] = []

  for (const flag of flags) {
    // criterion-level flag: "[Q1a(i) - ...] flag_type"
    const criterionMatch = flag.match(/^\[(.+?)\]\s+(.+)$/)
    if (criterionMatch) {
      const [, criterion, flagType] = criterionMatch
      const key = `${criterion}::${flagType}`
      if (!seen.has(key)) {
        seen.add(key)
        criterionFlags.set(criterion, flagType)
      }
    } else {
      summaryFlags.push(flag)
    }
  }

  // Convert criterion flags to human messages
  for (const [criterion, flagType] of criterionFlags.entries()) {
    const shortCrit = criterion.replace(/^Q\d+[a-z]?\([ivx]+\)\s*-\s*/i, '').slice(0, 30)
    if (flagType === 'evidence_likely_hallucinated') {
      serious.push(`"${shortCrit}" — exact quote not found in submission (verify manually)`)
    } else if (flagType === 'evidence_slightly_misquoted') {
      minor.push(`"${shortCrit}" — quote has minor formatting difference (likely correct)`)
    } else if (flagType === 'grading_error') {
      serious.push(`"${shortCrit}" — grading system error`)
    } else {
      minor.push(`"${shortCrit}" — ${flagType.replace(/_/g, ' ')}`)
    }
  }

  // Handle summary-level flags (the "Evidence could not be verified for X" lines)
  for (const flag of summaryFlags) {
    const key = flag.slice(0, 60)
    if (!seen.has(key)) {
      seen.add(key)
      if (flag.includes('hallucination') || flag.includes('could not be verified')) {
        // Already covered by criterion flags above — skip duplicate
      } else if (flag.startsWith('GRADING_CORRECTED')) {
        serious.push('⚙ ' + flag.replace('GRADING_CORRECTED: ', ''))
      }
    }
  }

  return { serious, minor }
}

// ── Compute AI confidence tier ────────────────────────────────────────────────
// Uses BOTH the flags array (hallucination/error detection) AND the backend's
// ai_confidence field, which is set independently by the grading engine.
// This fixes the bug where a student scoring 10/50 showed "Quick Approve".
function getConfidenceTier(
  flags: string[],
  rubric: CriterionScore[],
  aiConfidence: string,          // backend field: 'high' | 'medium' | 'low'
  effectiveScore: number | null,
  maxScore: number,
): {
  tier: 'confident' | 'uncertain' | 'check' | 'low_score'
  label: string
  detail: string
  color: string
  bg: string
  border: string
  Icon: typeof CheckCircle2
} {
  const hallucinations = flags.filter(f => f.includes('hallucinated')).length
  const errors = flags.filter(f => f.includes('grading_error')).length

  // Tier 1 — serious issues: grading errors or many hallucinations
  if (errors > 0 || hallucinations >= 4) {
    return {
      tier: 'check',
      label: 'Manual Review Required',
      detail: `${hallucinations > 0 ? `${hallucinations} criteria have suspicious evidence` : 'Grading error detected'} — verify carefully before approving`,
      color: '#ef4444',
      bg: '#ef444410',
      border: '#ef444430',
      Icon: AlertCircle,
    }
  }

  // Tier 2 — uncertain: some flag issues OR backend explicitly reports low confidence
  if (hallucinations >= 1 || aiConfidence === 'low') {
    const reasons: string[] = []
    if (hallucinations > 0) reasons.push(`${hallucinations} criteria have uncertain evidence`)
    if (aiConfidence === 'low') reasons.push('AI confidence is low')
    return {
      tier: 'uncertain',
      label: 'Verify Before Approving',
      detail: (reasons.join(' · ') || 'Uncertain grading') + ' — spot-check key sections',
      color: '#f97316',
      bg: '#f9731610',
      border: '#f9731630',
      Icon: HelpCircle,
    }
  }

  // Tier 3 — low score: AI may be correct but teacher should confirm a very low result
  const scorePct = effectiveScore !== null && maxScore > 0 ? effectiveScore / maxScore : null
  if (scorePct !== null && scorePct < 0.25) {
    return {
      tier: 'low_score',
      label: 'Low Score — Please Verify',
      detail: `Score is ${Math.round(scorePct * 100)}% of total — confirm the AI assessment matches the submission`,
      color: '#eab308',
      bg: '#eab30810',
      border: '#eab30830',
      Icon: TrendingDown,
    }
  }

  // Tier 4 — all clear
  return {
    tier: 'confident',
    label: 'AI Grading Verified',
    detail: 'No evidence issues detected. Review the score and approve.',
    color: '#22c55e',
    bg: '#22c55e10',
    border: '#22c55e30',
    Icon: CheckCircle2,
  }
}

// ── Criterion row ─────────────────────────────────────────────────────────────
function CriterionRow({ criterion, expanded, onToggle }: {
  criterion: CriterionScore
  expanded: boolean
  onToggle: () => void
}) {
  const hasDetail = !!(criterion.justification || (criterion.checkpoints && criterion.checkpoints.length > 0))
  const barPct = pct(criterion.score, criterion.max)
  const color = barColor(criterion.score, criterion.max)
  const hasFlagged = criterion.flagged && criterion.flag_reasons && criterion.flag_reasons.length > 0

  return (
    <div style={{ borderBottom: '1px solid var(--border)' }}>
      <div
        onClick={hasDetail ? onToggle : undefined}
        style={{
          display: 'grid',
          gridTemplateColumns: '1fr 80px 52px 14px',
          alignItems: 'center',
          gap: 10,
          padding: '7px 16px',
          cursor: hasDetail ? 'pointer' : 'default',
        }}
        onMouseEnter={e => { if (hasDetail) (e.currentTarget as HTMLDivElement).style.background = '#0e0e1a' }}
        onMouseLeave={e => { (e.currentTarget as HTMLDivElement).style.background = 'transparent' }}
      >
        <div style={{ overflow: 'hidden' }}>
          <div style={{ fontSize: 11.5, color: '#aaa', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            {criterion.criterion}
          </div>
          {hasFlagged && (
            <div style={{ fontSize: 10, color: '#f97316', marginTop: 1 }}>
              ⚠ AI evidence uncertain
            </div>
          )}
        </div>
        <div>
          <div style={{ height: 3, background: '#18181f', borderRadius: 99, overflow: 'hidden' }}>
            <div style={{ height: '100%', borderRadius: 99, width: `${barPct}%`, background: color, transition: 'width 0.4s ease' }} />
          </div>
        </div>
        <div style={{ fontFamily: 'var(--font-mono), monospace', fontSize: 11, color, fontWeight: 500, textAlign: 'right' }}>
          {criterion.score.toFixed(1)}<span style={{ color: '#555' }}>/{criterion.max}</span>
        </div>
        {hasDetail
          ? <div style={{ color: '#555' }}>{expanded ? <ChevronDown style={{ width: 12, height: 12 }} /> : <ChevronRight style={{ width: 12, height: 12 }} />}</div>
          : <div />
        }
      </div>

      {expanded && hasDetail && (
        <div style={{ padding: '0 16px 10px', background: '#08080c', borderTop: '1px solid var(--border)' }}>
          {criterion.justification && (
            <p style={{ fontSize: 11, color: '#555', lineHeight: 1.6, marginTop: 8, paddingLeft: 8, borderLeft: '2px solid #6366f1' }}>
              {criterion.justification}
            </p>
          )}
          {criterion.checkpoints?.map((cp, i) => (
            <CheckpointRow key={i} checkpoint={cp} criterionName={criterion.criterion} />
          ))}
        </div>
      )}
    </div>
  )
}

function CheckpointRow({ checkpoint, criterionName }: { checkpoint: CheckpointResult; criterionName?: string }) {
  const passed = checkpoint.pass || (checkpoint.score_percent !== undefined && checkpoint.score_percent >= 50)
  const color = passed ? '#22c55e' : '#ef4444'

  // If description was not stored (empty or fell back to criterion name during old grading),
  // use checkpoint.reasoning as a more informative fallback — the judge always writes
  // unique reasoning per checkpoint. Also try to format the checkpoint ID nicely.
  const descriptionIsUseful =
    checkpoint.description &&
    checkpoint.description !== criterionName &&
    checkpoint.description.trim() !== ''

  const displayText = descriptionIsUseful
    ? checkpoint.description
    : checkpoint.reasoning
      ? checkpoint.reasoning.slice(0, 120) + (checkpoint.reasoning.length > 120 ? '…' : '')
      : checkpoint.id
        ? checkpoint.id.replace(/_/g, ' ')
        : '(checkpoint)'

  return (
    <div style={{ display: 'flex', alignItems: 'flex-start', gap: 6, padding: '4px 0', borderBottom: '1px solid #0e0e14' }}>
      <div style={{
        width: 15, height: 15, borderRadius: '50%', flexShrink: 0,
        background: `${color}18`, border: `1px solid ${color}40`,
        display: 'flex', alignItems: 'center', justifyContent: 'center',
        fontSize: 8, color, marginTop: 2,
      }}>
        {passed ? '✓' : '✗'}
      </div>
      <div style={{ flex: 1 }}>
        <div style={{ fontSize: 11, color: '#777' }}>{displayText}</div>
        {checkpoint.evidence_quote && checkpoint.evidence_quote !== 'No relevant content found.' && (
          <div style={{ fontSize: 10, color: '#444', marginTop: 2, fontFamily: 'var(--font-mono), monospace', fontStyle: 'italic' }}>
            "{checkpoint.evidence_quote.slice(0, 140)}{checkpoint.evidence_quote.length > 140 ? '…' : ''}"
          </div>
        )}
      </div>
      {checkpoint.points !== undefined && (
        <div style={{ fontFamily: 'var(--font-mono), monospace', fontSize: 10, color, flexShrink: 0 }}>
          {checkpoint.points_awarded ?? (passed ? checkpoint.points : 0)}/{checkpoint.points}
        </div>
      )}
    </div>
  )
}

// ── Main panel ────────────────────────────────────────────────────────────────
export function StudentReviewPanel({ sessionId, studentId, maxScore, onNext, onApproved }: StudentReviewPanelProps) {
  const queryClient = useQueryClient()
  const [expandedCriteria, setExpandedCriteria] = useState<Set<number>>(new Set())
  const [isApproving, setIsApproving] = useState(false)
  const [isRegrading, setIsRegrading] = useState(false)
  const [editMode, setEditMode] = useState(false)
  const [editScore, setEditScore] = useState('')
  const [editComment, setEditComment] = useState('')
  const [showCode, setShowCode] = useState(false)

  const { data: student, isLoading, isError } = useQuery({
    queryKey: ['student', sessionId, studentId],
    queryFn: () => fetchStudent(sessionId, studentId),
    staleTime: 10_000,
  })

  // Keyboard shortcuts
  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      if (editMode || e.target instanceof HTMLInputElement || e.target instanceof HTMLTextAreaElement) return
      if (e.key === 'a' || e.key === 'A') { if (student && !student.is_reviewed) handleApprove() }
      if (e.key === 'n' || e.key === 'N') onNext()
      if (e.key === 'e' || e.key === 'E') {
        if (student) {
          setEditScore(String(student.override_score ?? student.ai_score ?? ''))
          setEditComment(student.override_comments || '')
          setEditMode(true)
        }
      }
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [student, editMode, onNext])

  const toggleCriterion = useCallback((i: number) => {
    setExpandedCriteria(prev => {
      const next = new Set(prev)
      if (next.has(i)) next.delete(i)
      else next.add(i)
      return next
    })
  }, [])

  const handleApprove = useCallback(async () => {
    if (!student || isApproving) return
    const score = student.override_score ?? student.ai_score ?? 0
    setIsApproving(true)
    try {
      await overrideScore(sessionId, studentId, { score, comments: student.override_comments || '', is_reviewed: true })
      queryClient.invalidateQueries({ queryKey: ['student', sessionId, studentId] })
      queryClient.invalidateQueries({ queryKey: ['students', sessionId] })
      onApproved()
      onNext()
    } finally { setIsApproving(false) }
  }, [student, sessionId, studentId, isApproving, queryClient, onApproved, onNext])

  const handleSaveEdit = useCallback(async () => {
    if (!student) return
    const score = parseFloat(editScore)
    if (isNaN(score) || score < 0 || score > maxScore) return
    setIsApproving(true)
    try {
      await overrideScore(sessionId, studentId, { score, comments: editComment, is_reviewed: true })
      queryClient.invalidateQueries({ queryKey: ['student', sessionId, studentId] })
      queryClient.invalidateQueries({ queryKey: ['students', sessionId] })
      setEditMode(false)
      onApproved()
    } finally { setIsApproving(false) }
  }, [editScore, editComment, maxScore, sessionId, studentId, student, queryClient, onApproved])

  const handleRegrade = useCallback(async () => {
    if (!student || isRegrading) return
    setIsRegrading(true)
    try {
      await regradeStudent(sessionId, studentId)
      queryClient.removeQueries({ queryKey: ['student', sessionId, studentId] })
      let attempts = 0
      const poll = setInterval(async () => {
        attempts++
        const updated = await fetchStudent(sessionId, studentId)
        if (updated.status === 'graded' || updated.status === 'error' || attempts > 60) {
          clearInterval(poll)
          queryClient.setQueryData(['student', sessionId, studentId], updated)
          queryClient.invalidateQueries({ queryKey: ['students', sessionId] })
          setIsRegrading(false)
        }
      }, 2000)
    } catch { setIsRegrading(false) }
  }, [student, sessionId, studentId, isRegrading, queryClient])

  if (isLoading) {
    return (
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column' }}>
        <div style={{ padding: '20px', borderBottom: '1px solid var(--border)' }}>
          <div style={{ height: 12, width: 160, background: '#111', borderRadius: 4, marginBottom: 8 }} />
          <div style={{ height: 36, width: 100, background: '#111', borderRadius: 4 }} />
        </div>
        <div style={{ padding: 16, flex: 1 }}>
          {[...Array(5)].map((_, i) => (
            <div key={i} style={{ height: 32, background: '#0e0e1a', borderRadius: 4, marginBottom: 6 }} />
          ))}
        </div>
      </div>
    )
  }

  if (isError || !student) {
    return (
      <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
        <div style={{ textAlign: 'center', color: '#444', fontSize: 13 }}>Failed to load student data.</div>
      </div>
    )
  }

  const effectiveScore = student.override_score ?? student.ai_score ?? null
  const scoreColor = effectiveScore !== null ? barColor(effectiveScore, maxScore) : '#444'
  const name = shortName(student.student_identifier)
  const rubric: CriterionScore[] = student.rubric_breakdown || student.ai_result?.rubric_breakdown || []
  const feedback = student.ai_feedback || student.ai_result?.overall_feedback || ''
  const rawFlags: string[] = student.ai_result?.flags || []
  const isGraded = student.status === 'graded'
  const isErr = student.status === 'error'
  const { serious, minor } = processFlags(rawFlags)
  const confidence = getConfidenceTier(rawFlags, rubric, student.ai_confidence ?? '', effectiveScore, maxScore)
  const { Icon: ConfIcon } = confidence

  // Get file list for code preview link
  const fileCount = student.file_count || 0

  return (
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minWidth: 0 }}>

      {/* ── Header ─────────────────────────────────────────────────────── */}
      <div style={{
        padding: '14px 18px 12px',
        borderBottom: '1px solid var(--border)',
        display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between',
        gap: 12, flexShrink: 0,
      }}>
        <div>
          <div style={{ fontSize: 9, color: '#666', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 2 }}>Student</div>
          <div style={{ fontSize: 19, fontWeight: 700, color: '#fff', letterSpacing: '-0.02em', lineHeight: 1.1 }}>{name}</div>
          <div style={{ fontSize: 10, color: '#2a2a3a', marginTop: 2 }}>{student.student_identifier}</div>
        </div>
        <div style={{ textAlign: 'right', flexShrink: 0 }}>
          <div style={{ fontSize: 9, color: '#666', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 2 }}>Score</div>
          <div style={{ fontFamily: 'var(--font-mono), monospace', fontSize: 30, fontWeight: 500, color: scoreColor, letterSpacing: '-0.04em', lineHeight: 1 }}>
            {effectiveScore !== null ? effectiveScore.toFixed(2) : '—'}
            <span style={{ fontSize: 13, color: '#2a2a3a', fontWeight: 400 }}>/{maxScore}</span>
          </div>
          {student.is_overridden && (
            <div style={{ fontSize: 9, color: '#6366f1', marginTop: 2 }}>✎ manually corrected</div>
          )}
        </div>
      </div>

      {/* ── Confidence Verdict (THE KEY UX FIX) ───────────────────────── */}
      {isGraded && (
        <div style={{
          margin: '10px 14px 0',
          padding: '10px 12px',
          background: confidence.bg,
          border: `1px solid ${confidence.border}`,
          borderRadius: 8,
          display: 'flex', alignItems: 'flex-start', gap: 10,
          flexShrink: 0,
        }}>
          <ConfIcon style={{ width: 14, height: 14, color: confidence.color, flexShrink: 0, marginTop: 1 }} />
          <div>
            <div style={{ fontSize: 12, fontWeight: 600, color: confidence.color }}>{confidence.label}</div>
            <div style={{ fontSize: 11, color: '#666', marginTop: 2 }}>{confidence.detail}</div>
          </div>
        </div>
      )}

      {/* ── Body — flows naturally with the page ──────────────────────── */}
      <div>

        {/* Error */}
        {isErr && (
          <div style={{ margin: '10px 14px 0', padding: '8px 12px', background: '#ef444412', border: '1px solid #ef444430', borderRadius: 8 }}>
            <div style={{ fontSize: 11, color: '#ef4444', fontWeight: 600, marginBottom: 2 }}>Grading Error</div>
            <div style={{ fontSize: 11, color: '#f87171' }}>{student.error_message || 'An error occurred.'}</div>
          </div>
        )}

        {/* Serious flags — human readable */}
        {serious.length > 0 && (
          <div style={{ margin: '10px 14px 0', padding: '8px 12px', background: '#ef444410', border: '1px solid #ef444428', borderRadius: 8 }}>
            <div style={{ fontSize: 10, color: '#ef4444', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 5 }}>
              Issues Found
            </div>
            {serious.map((f, i) => (
              <div key={i} style={{ fontSize: 11, color: '#f87171', display: 'flex', gap: 5, marginBottom: 2 }}>
                <span style={{ flexShrink: 0 }}>•</span><span>{f}</span>
              </div>
            ))}
          </div>
        )}

        {/* Minor flags */}
        {minor.length > 0 && (
          <div style={{ margin: '8px 14px 0', padding: '6px 10px', background: '#f9731608', border: '1px solid #f9731620', borderRadius: 6 }}>
            <div style={{ fontSize: 10, color: '#f97316', fontWeight: 600, marginBottom: 3 }}>Minor Notes</div>
            {minor.map((f, i) => (
              <div key={i} style={{ fontSize: 10, color: '#666', display: 'flex', gap: 4, marginBottom: 1 }}>
                <span style={{ flexShrink: 0 }}>·</span><span>{f}</span>
              </div>
            ))}
          </div>
        )}

        {/* AI Assessment */}
        {feedback && (
          <div style={{ margin: '10px 14px 0', padding: '8px 12px', background: '#6366f108', borderLeft: '2px solid #6366f1', borderRadius: '0 6px 6px 0' }}>
            <div style={{ fontSize: 9, color: '#6366f1', fontWeight: 700, textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 4 }}>AI Assessment</div>
            <p style={{ fontSize: 11, color: '#555', lineHeight: 1.6, margin: 0 }}>
              {feedback.length > 280 ? feedback.slice(0, 280) + '…' : feedback}
            </p>
          </div>
        )}

        {/* Rubric breakdown */}
        {rubric.length > 0 && (
          <div style={{ marginTop: 12 }}>
            <div style={{
              padding: '5px 16px', fontSize: 9, color: '#2a2a3a',
              textTransform: 'uppercase', letterSpacing: '0.08em', fontWeight: 700,
              borderTop: '1px solid var(--border)', borderBottom: '1px solid var(--border)',
              display: 'flex', justifyContent: 'space-between', alignItems: 'center',
            }}>
              <span>Rubric Breakdown</span>
              <span style={{ color: '#555' }}>{rubric.length} criteria · {effectiveScore?.toFixed(1)}/{maxScore}</span>
            </div>
            {rubric.map((criterion, i) => (
              <CriterionRow
                key={i}
                criterion={criterion}
                expanded={expandedCriteria.has(i)}
                onToggle={() => toggleCriterion(i)}
              />
            ))}
          </div>
        )}

        {/* Not graded */}
        {!isGraded && !isErr && (
          <div style={{ padding: '32px 20px', textAlign: 'center', color: '#555', fontSize: 12 }}>
            {student.status === 'grading' ? 'Grading in progress…' : 'Not yet graded.'}
          </div>
        )}

        {/* Edit score form */}
        {editMode && (
          <div style={{ margin: '10px 14px', padding: '12px', background: '#0e0e1a', border: '1px solid var(--border)', borderRadius: 8 }}>
            <div style={{ fontSize: 11, color: '#777', marginBottom: 8 }}>Override Score (keyboard: save with Enter)</div>
            <div style={{ display: 'flex', gap: 8, alignItems: 'center', marginBottom: 8 }}>
              <input
                type="number" min={0} max={maxScore} step={0.25}
                value={editScore} onChange={e => setEditScore(e.target.value)}
                placeholder={`0–${maxScore}`}
                onKeyDown={e => e.key === 'Enter' && handleSaveEdit()}
                autoFocus
                style={{
                  width: 80, background: '#141420', border: '1px solid var(--border-muted)',
                  borderRadius: 6, padding: '5px 8px', fontSize: 13, color: '#fff',
                  fontFamily: 'var(--font-mono), monospace', outline: 'none',
                }}
              />
              <span style={{ fontSize: 12, color: '#444' }}>/ {maxScore}</span>
            </div>
            <textarea
              value={editComment} onChange={e => setEditComment(e.target.value)}
              placeholder="Reason for override…" rows={2}
              style={{
                width: '100%', background: '#141420', border: '1px solid var(--border-muted)',
                borderRadius: 6, padding: '6px 8px', fontSize: 11, color: '#777',
                resize: 'none', outline: 'none', marginBottom: 8,
              }}
            />
            <div style={{ display: 'flex', gap: 6 }}>
              <button onClick={handleSaveEdit} disabled={isApproving}
                style={{ flex: 1, background: '#6366f1', color: '#fff', border: 'none', borderRadius: 6, padding: '6px', fontSize: 11, fontWeight: 600, cursor: 'pointer' }}>
                {isApproving ? 'Saving…' : 'Save & Approve'}
              </button>
              <button onClick={() => setEditMode(false)}
                style={{ background: '#141420', color: '#555', border: '1px solid var(--border)', borderRadius: 6, padding: '6px 10px', fontSize: 11, cursor: 'pointer' }}>
                Cancel
              </button>
            </div>
          </div>
        )}

        <div style={{ height: 64 }} />
      </div>

      {/* ── Action Bar — sticky to bottom of viewport ─────────────────── */}
      <div style={{
        position: 'sticky', bottom: 0, zIndex: 20,
        borderTop: '1px solid var(--border)', padding: '8px 12px',
        display: 'flex', gap: 5, background: 'var(--bg-page)',
        alignItems: 'center',
      }}>
        {/* Keyboard hint */}
        <div style={{ fontSize: 9, color: '#222', marginRight: 4, flexShrink: 0 }}>
          A·E·N
        </div>

        {/* Approve */}
        {isGraded && !student.is_reviewed && (
          <button onClick={handleApprove} disabled={isApproving || editMode}
            style={{
              flex: 1, background: isApproving ? '#1a1a2a' : '#fff', color: isApproving ? '#555' : '#000',
              border: 'none', borderRadius: 7, padding: '8px', fontSize: 12, fontWeight: 700,
              cursor: isApproving ? 'not-allowed' : 'pointer', letterSpacing: '0.02em',
            }}>
            {isApproving ? 'Approving…' : '✓ Approve'}
          </button>
        )}

        {/* Already reviewed */}
        {student.is_reviewed && (
          <div style={{
            flex: 1, background: '#22c55e12', border: '1px solid #22c55e30', color: '#22c55e',
            borderRadius: 7, padding: '8px', fontSize: 12, fontWeight: 600, textAlign: 'center',
          }}>
            ✓ Reviewed
          </div>
        )}

        {/* Edit */}
        {isGraded && (
          <button onClick={() => {
            setEditScore(String(effectiveScore ?? student.ai_score ?? ''))
            setEditComment(student.override_comments || '')
            setEditMode(!editMode)
          }}
            style={{
              background: '#141420', color: editMode ? '#818cf8' : '#555',
              border: `1px solid ${editMode ? '#6366f1' : 'var(--border)'}`,
              borderRadius: 7, padding: '8px 11px', fontSize: 12, cursor: 'pointer',
            }}>
            Edit
          </button>
        )}

        {/* Regrade */}
        <button onClick={handleRegrade} disabled={isRegrading || isApproving} title="Regrade (re-run AI)"
          style={{
            background: '#141420', color: '#444', border: '1px solid var(--border)',
            borderRadius: 7, padding: '8px 9px', cursor: isRegrading ? 'not-allowed' : 'pointer',
            display: 'flex', alignItems: 'center',
          }}>
          <RotateCcw style={{ width: 12, height: 12, animation: isRegrading ? 'spin 1s linear infinite' : 'none' }} />
        </button>

        {/* Open full detail */}
        <Link href={`/sessions/${sessionId}/students/${studentId}`} target="_blank" title="Open full detail"
          style={{
            background: '#141420', color: '#444', border: '1px solid var(--border)',
            borderRadius: 7, padding: '8px 9px', cursor: 'pointer',
            display: 'flex', alignItems: 'center', textDecoration: 'none',
          }}>
          <ExternalLink style={{ width: 12, height: 12 }} />
        </Link>

        {/* Next */}
        <button onClick={onNext}
          style={{
            background: '#141420', color: '#555', border: '1px solid var(--border)',
            borderRadius: 7, padding: '8px 11px', fontSize: 12, cursor: 'pointer',
          }}>
          Next →
        </button>
      </div>

      <style>{`@keyframes spin { from{transform:rotate(0deg)} to{transform:rotate(360deg)} }`}</style>
    </div>
  )
}
