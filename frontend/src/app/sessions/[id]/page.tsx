'use client'

import { useParams } from 'next/navigation'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useState, useCallback, useEffect } from 'react'
import { AppShell } from '@/components/layout/app-shell'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { fetchSession, fetchStudents, uploadSubmissions, overrideScore } from '@/lib/api'
import type { SessionStatus, Submission } from '@/lib/types'
import Link from 'next/link'
import {
  BarChart3, Download, Play, Square, AlertTriangle,
  RefreshCw, FileText, BookOpen, ArrowLeft, UserPlus, CheckCheck,
} from 'lucide-react'
import { StudentSidebar } from '@/components/session/student-sidebar'
import { StudentReviewPanel } from '@/components/session/student-review-panel'
import { GradingTheater } from '@/components/session/grading-theater'
import { UploadZone } from '@/components/session/upload-zone'
import { AddStudentDialog } from '@/components/session/add-student-dialog'
import { useStartGrading, useStopGrading } from '@/hooks/use-mutations'

const statusVariant: Record<SessionStatus, 'default' | 'success' | 'warning' | 'error' | 'info'> = {
  pending: 'default',
  uploading: 'info',
  grading: 'warning',
  complete: 'success',
  completed: 'success',
  completed_with_errors: 'warning',
  error: 'error',
  stopped: 'default',
  paused: 'default',
}

const statusLabel: Record<string, string> = {
  pending: 'Pending',
  uploading: 'Uploading',
  grading: 'Grading…',
  complete: 'Complete',
  completed: 'Complete',
  completed_with_errors: 'Done with errors',
  error: 'Error',
  stopped: 'Stopped',
  paused: 'Stopped',
}

function isCompleted(status: SessionStatus) {
  return status === 'complete' || status === 'completed' || status === 'completed_with_errors'
}
function isGrading(status: SessionStatus) {
  return status === 'grading'
}

function getEffectiveScore(s: Submission): number | null {
  return s.override_score ?? s.ai_score ?? null
}

function sortStudents(students: Submission[]): Submission[] {
  return [...students].sort((a, b) => {
    const aErr = a.status === 'error' || a.is_flagged
    const bErr = b.status === 'error' || b.is_flagged
    if (aErr && !bErr) return -1
    if (!aErr && bErr) return 1
    if (!a.is_reviewed && b.is_reviewed) return -1
    if (a.is_reviewed && !b.is_reviewed) return 1
    const aS = getEffectiveScore(a) ?? Infinity
    const bS = getEffectiveScore(b) ?? Infinity
    return aS - bS
  })
}

export default function SessionDetailPage() {
  const params = useParams()
  const sessionId = Number(params.id)
  const queryClient = useQueryClient()
  const startGrading = useStartGrading()
  const stopGrading = useStopGrading()

  const [selectedStudentId, setSelectedStudentId] = useState<number | null>(null)
  const [addStudentOpen, setAddStudentOpen] = useState(false)
  const [infoTab, setInfoTab] = useState<'description' | 'rubric' | null>(null)
  const [bulkApproving, setBulkApproving] = useState(false)
  const [bulkProgress, setBulkProgress] = useState<{ done: number; total: number } | null>(null)

  const { data: session, isLoading, isError } = useQuery({
    queryKey: ['session', sessionId],
    queryFn: () => fetchSession(sessionId),
    refetchInterval: (query) => {
      const status = query.state.data?.status
      return status === 'grading' ? 2000 : false
    },
  })

  const { data: students } = useQuery({
    queryKey: ['students', sessionId],
    queryFn: () => fetchStudents(sessionId),
    // Always fetch when session is loaded — we need students during grading too
    // so GradingTheater can seed the live-feed with already-graded students
    enabled: !!session,
    refetchInterval: session && isGrading(session.status as SessionStatus) ? 3000 : false,
  })

  // Auto-select: prefer first graded/error student, fall back to first student overall
  // so the panel is never empty even when grading is just starting.
  useEffect(() => {
    if (!students || students.length === 0 || selectedStudentId !== null) return
    const sorted = sortStudents(students)
    const firstGraded = sorted.find(s => s.status === 'graded' || s.status === 'error')
    const first = firstGraded ?? sorted[0]
    if (first) setSelectedStudentId(first.id)
  }, [students, selectedStudentId])

  const handleNext = useCallback(() => {
    if (!students || !selectedStudentId) return
    const sorted = sortStudents(students)
    const idx = sorted.findIndex(s => s.id === selectedStudentId)
    const next = sorted[idx + 1]
    if (next) setSelectedStudentId(next.id)
  }, [students, selectedStudentId])

  const handleUpload = async (file: File) => {
    await uploadSubmissions(sessionId, file)
    queryClient.invalidateQueries({ queryKey: ['session', sessionId] })
    queryClient.invalidateQueries({ queryKey: ['students', sessionId] })
  }

  // Bulk approve: approve all students that are graded, not yet reviewed,
  // not flagged, and AI confidence is not low — the "clearly fine" students.
  const handleBulkApprove = useCallback(async () => {
    if (!students || bulkApproving) return
    const eligible = students.filter(s =>
      s.status === 'graded' &&
      !s.is_reviewed &&
      !s.is_flagged &&
      s.ai_confidence !== 'low'
    )
    if (eligible.length === 0) return
    setBulkApproving(true)
    setBulkProgress({ done: 0, total: eligible.length })
    let done = 0
    for (const s of eligible) {
      try {
        const score = s.override_score ?? s.ai_score ?? 0
        await overrideScore(sessionId, s.id, { score, comments: 'Bulk approved', is_reviewed: true })
        done++
        setBulkProgress({ done, total: eligible.length })
      } catch {
        // continue even if one fails
      }
    }
    queryClient.invalidateQueries({ queryKey: ['students', sessionId] })
    queryClient.invalidateQueries({ queryKey: ['session', sessionId] })
    setBulkApproving(false)
    setBulkProgress(null)
  }, [students, bulkApproving, sessionId, queryClient])

  if (isLoading) {
    return (
      <AppShell>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          <div style={{ height: 14, width: 120, background: '#111', borderRadius: 4 }} />
          <div style={{ height: 44, background: '#0e0e1a', borderRadius: 8 }} />
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(5,1fr)', gap: 8 }}>
            {[...Array(5)].map((_, i) => <div key={i} style={{ height: 64, background: '#0e0e1a', borderRadius: 8 }} />)}
          </div>
          <div style={{ height: 400, background: '#0e0e1a', borderRadius: 10 }} />
        </div>
      </AppShell>
    )
  }

  if (isError || !session) {
    return (
      <AppShell>
        <div style={{ textAlign: 'center', padding: '60px 0' }}>
          <AlertTriangle style={{ width: 36, height: 36, margin: '0 auto 10px', color: '#ef4444' }} />
          <p style={{ color: '#555', fontSize: 13 }}>Session not found</p>
          <Link href="/">
            <Button variant="outline" style={{ marginTop: 14 }}>← Back</Button>
          </Link>
        </div>
      </AppShell>
    )
  }

  const progress = session.total_students > 0
    ? Math.round((session.graded_count / session.total_students) * 100)
    : 0
  const hasStudents = session.total_students > 0
  const showUpload = !isGrading(session.status) &&
    (session.status === 'pending' || session.status === 'stopped' || session.status === 'paused' || session.total_students === 0)
  const canStartGrading = hasStudents && !isCompleted(session.status) && !isGrading(session.status)
  // Show split panel whenever students exist — lets teacher see the roster and
  // review already-graded students even while grading is actively running.
  const showSplitPanel = hasStudents

  // Compute avg score
  const avgScore = (() => {
    if (!students || students.length === 0) return '—'
    const graded = students.filter(s => s.ai_score !== null)
    if (graded.length === 0) return '—'
    const avg = graded.reduce((sum, s) => sum + (s.override_score ?? s.ai_score ?? 0), 0) / graded.length
    return avg.toFixed(1)
  })()

  return (
    <AppShell>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 0 }}>

        {/* ── Header ──────────────────────────────────────────────── */}
        <div style={{
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          gap: 12, paddingBottom: 12, flexShrink: 0,
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, minWidth: 0 }}>
            <Link href="/" style={{ color: '#444', textDecoration: 'none', flexShrink: 0, display: 'flex', alignItems: 'center' }}>
              <ArrowLeft style={{ width: 14, height: 14 }} />
            </Link>
            <h1 style={{
              fontSize: 17, fontWeight: 700, color: '#fff',
              letterSpacing: '-0.02em', overflow: 'hidden',
              textOverflow: 'ellipsis', whiteSpace: 'nowrap',
            }}>
              {session.title}
            </h1>
            <Badge variant={statusVariant[session.status] || 'default'} pulse={isGrading(session.status)}>
              {statusLabel[session.status] || session.status}
            </Badge>
          </div>

          <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexShrink: 0 }}>
            {isGrading(session.status) ? (
              <Button variant="danger" size="sm"
                onClick={() => stopGrading.mutate({ sessionId })}
                disabled={stopGrading.isPending}
              >
                <Square style={{ width: 12, height: 12 }} /> Stop
              </Button>
            ) : canStartGrading ? (
              <Button variant="primary" size="sm"
                onClick={() => startGrading.mutate({ sessionId })}
                disabled={startGrading.isPending}
              >
                {startGrading.isPending
                  ? <RefreshCw style={{ width: 12, height: 12, animation: 'spin 1s linear infinite' }} />
                  : <Play style={{ width: 12, height: 12 }} />}
                {startGrading.isPending ? 'Starting…' : 'Grade'}
              </Button>
            ) : null}

            {/* Bulk Approve — only when session is complete and there are approvable students */}
            {isCompleted(session.status) && students && (() => {
              const eligible = students.filter(s =>
                s.status === 'graded' && !s.is_reviewed && !s.is_flagged && s.ai_confidence !== 'low'
              )
              if (eligible.length === 0) return null
              return (
                <Button variant="outline" size="sm"
                  onClick={handleBulkApprove}
                  disabled={bulkApproving}
                  title={`Approve all ${eligible.length} AI-verified students (not flagged, high/medium confidence)`}
                  style={{ borderColor: '#22c55e40', color: bulkApproving ? '#555' : '#22c55e' }}
                >
                  <CheckCheck style={{ width: 12, height: 12 }} />
                  {bulkApproving && bulkProgress
                    ? `Approving ${bulkProgress.done}/${bulkProgress.total}…`
                    : `Approve ${eligible.length} Verified`}
                </Button>
              )
            })()}
            {hasStudents && (
              <>
                <Link href={`/sessions/${sessionId}/results`}>
                  <Button variant="outline" size="sm"><BarChart3 style={{ width: 12, height: 12 }} /> Results</Button>
                </Link>
                <a href={`/api/session/${sessionId}/export/csv`}>
                  <Button variant="outline" size="sm"><Download style={{ width: 12, height: 12 }} /> CSV</Button>
                </a>
              </>
            )}
            {!isGrading(session.status) && (
              <Button variant="outline" size="sm" onClick={() => setAddStudentOpen(true)}>
                <UserPlus style={{ width: 12, height: 12 }} /> Add
              </Button>
            )}
          </div>
        </div>

        {/* ── Stats ───────────────────────────────────────────────── */}
        {(() => {
          const reviewedCount = students?.filter(s => s.is_reviewed).length ?? 0
          const needsReviewCount = students?.filter(s =>
            s.status === 'graded' && !s.is_reviewed && (s.is_flagged || s.ai_confidence === 'low')
          ).length ?? 0
          return (
            <div style={{
              display: 'grid', gridTemplateColumns: 'repeat(6, 1fr)',
              gap: 8, marginBottom: 12, flexShrink: 0,
            }}>
              <StatChip label="Students" value={session.total_students} />
              <StatChip label="Graded" value={session.graded_count} color="#22c55e" />
              <StatChip label="Reviewed" value={reviewedCount} color={reviewedCount === session.graded_count && session.graded_count > 0 ? '#22c55e' : '#6366f1'} />
              <StatChip label="Need Review" value={needsReviewCount} color={needsReviewCount > 0 ? '#f97316' : '#555'} />
              <StatChip label="Errors" value={session.error_count} color={session.error_count > 0 ? '#ef4444' : undefined} />
              <StatChip label="Avg Score" value={avgScore} color="#f59e0b" />
            </div>
          )
        })()}

        {/* ── Grading Theater ─────────────────────────────────────── */}
        {isGrading(session.status) && (
          <div style={{ marginBottom: 12 }}>
            <GradingTheater
              sessionId={sessionId}
              sessionTotal={session.total_students}
              existingStudents={students ?? []}
              onComplete={() => {
                queryClient.invalidateQueries({ queryKey: ['session', sessionId] })
                queryClient.invalidateQueries({ queryKey: ['students', sessionId] })
              }}
            />
          </div>
        )}

        {/* ── Upload Zone ─────────────────────────────────────────── */}
        {showUpload && (
          <div style={{ marginBottom: 12, flexShrink: 0 }}>
            <UploadZone sessionId={sessionId} onUpload={handleUpload} />
          </div>
        )}

        {/* ── Split Panel ─────────────────────────────────────────── */}
        {showSplitPanel && (
          <div style={{
            display: 'flex',
            border: '1px solid var(--border)', borderRadius: 12,
            overflow: 'visible',
            alignItems: 'flex-start',
          }}>
            <StudentSidebar
              sessionId={sessionId}
              maxScore={session.max_score}
              selectedId={selectedStudentId}
              onSelect={setSelectedStudentId}
            />
            {selectedStudentId ? (
              <StudentReviewPanel
                sessionId={sessionId}
                studentId={selectedStudentId}
                maxScore={session.max_score}
                onNext={handleNext}
                onApproved={() => queryClient.invalidateQueries({ queryKey: ['students', sessionId] })}
              />
            ) : (
              <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#333', fontSize: 13 }}>
                Select a student to review
              </div>
            )}
          </div>
        )}

        {/* ── Info Toggles ────────────────────────────────────────── */}
        {(session.description || session.rubric) && (
          <div style={{ marginTop: 10, flexShrink: 0, display: 'flex', gap: 6 }}>
            {session.description && (
              <button onClick={() => setInfoTab(infoTab === 'description' ? null : 'description')}
                style={{
                  background: infoTab === 'description' ? '#141420' : 'transparent',
                  border: '1px solid var(--border)', borderRadius: 6, padding: '4px 10px',
                  fontSize: 11, color: '#555', cursor: 'pointer',
                  display: 'flex', alignItems: 'center', gap: 4,
                }}>
                <FileText style={{ width: 11, height: 11 }} /> Description
              </button>
            )}
            {session.rubric && (
              <button onClick={() => setInfoTab(infoTab === 'rubric' ? null : 'rubric')}
                style={{
                  background: infoTab === 'rubric' ? '#141420' : 'transparent',
                  border: '1px solid var(--border)', borderRadius: 6, padding: '4px 10px',
                  fontSize: 11, color: '#555', cursor: 'pointer',
                  display: 'flex', alignItems: 'center', gap: 4,
                }}>
                <BookOpen style={{ width: 11, height: 11 }} /> Rubric
              </button>
            )}
          </div>
        )}
        {infoTab === 'description' && session.description && (
          <InfoBox text={session.description} />
        )}
        {infoTab === 'rubric' && session.rubric && (
          <InfoBox text={session.rubric} />
        )}
      </div>

      <AddStudentDialog
        sessionId={sessionId}
        open={addStudentOpen}
        onClose={() => setAddStudentOpen(false)}
        onSuccess={() => {
          queryClient.invalidateQueries({ queryKey: ['session', sessionId] })
          queryClient.invalidateQueries({ queryKey: ['students', sessionId] })
        }}
      />

      <style>{`
        @keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }
      `}</style>
    </AppShell>
  )
}

function StatChip({ label, value, color }: { label: string; value: number | string; color?: string }) {
  return (
    <div style={{
      background: 'var(--bg-card)', border: '1px solid var(--border)',
      borderRadius: 8, padding: '8px 12px', display: 'flex', flexDirection: 'column', gap: 2,
    }}>
      <div style={{ fontSize: 9, color: '#666', textTransform: 'uppercase', letterSpacing: '0.08em' }}>{label}</div>
      <div style={{
        fontSize: 20, fontWeight: 700, color: color || '#fff',
        fontFamily: 'var(--font-mono), monospace',
        letterSpacing: '-0.03em', lineHeight: 1,
      }}>
        {value}
      </div>
    </div>
  )
}

function InfoBox({ text }: { text: string }) {
  return (
    <div style={{
      marginTop: 8, padding: '12px 14px',
      background: 'var(--bg-card)', border: '1px solid var(--border)',
      borderRadius: 8, fontSize: 12, color: '#777', lineHeight: 1.7,
      whiteSpace: 'pre-wrap', maxHeight: 180, overflowY: 'auto',
    }}>
      {text}
    </div>
  )
}
