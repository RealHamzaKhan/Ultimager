'use client'

import { useParams } from 'next/navigation'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { AppShell } from '@/components/layout/app-shell'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Card, CardContent } from '@/components/ui/card'
import {
  fetchSession,
  fetchStudent,
  fetchStudents,
  fetchStudentFiles,
  fetchIngestionReport,
  regradeStudent,
} from '@/lib/api'
import { FileBrowser } from '@/components/student/file-browser'
import { AIFeedbackPanel } from '@/components/student/ai-feedback-panel'
import { OverrideForm } from '@/components/student/override-form'
import { TransparencyVault } from '@/components/student/transparency-vault'
import { StudentNav } from '@/components/student/student-nav'
import { formatScore, scoreToGrade } from '@/lib/utils'
import { cn } from '@/lib/utils'
import Link from 'next/link'
import React, { useState, useCallback, useRef, useEffect } from 'react'
import {
  ArrowLeft,
  Clock,
  FileText,
  Shield,
  CheckCircle2,
  XCircle,
  AlertTriangle,
  Flag,
  RefreshCw,
  Sparkles,
  TrendingUp,
  Award,
  BarChart3,
  GitBranch,
} from 'lucide-react'
import { ProcessingMapping } from '@/components/student/processing-mapping'

function getGradeColor(grade: string): string {
  if (!grade) return 'text-[var(--text-muted)]'
  if (grade.startsWith('A')) return 'text-emerald-400'
  if (grade.startsWith('B')) return 'text-blue-400'
  if (grade.startsWith('C')) return 'text-amber-400'
  if (grade.startsWith('D')) return 'text-orange-400'
  return 'text-rose-400'
}

function getGradeBg(grade: string): string {
  if (!grade) return 'bg-[var(--border)]'
  if (grade.startsWith('A')) return 'bg-emerald-500/10 border-emerald-500/20'
  if (grade.startsWith('B')) return 'bg-blue-500/10 border-blue-500/20'
  if (grade.startsWith('C')) return 'bg-amber-500/10 border-amber-500/20'
  if (grade.startsWith('D')) return 'bg-orange-500/10 border-orange-500/20'
  return 'bg-rose-500/10 border-rose-500/20'
}

function getConfidenceBadge(confidence: string) {
  if (confidence === 'high') return { variant: 'success' as const, icon: CheckCircle2 }
  if (confidence === 'medium') return { variant: 'warning' as const, icon: AlertTriangle }
  return { variant: 'error' as const, icon: XCircle }
}

function formatTimeAgo(dateStr: string | null | undefined): string {
  if (!dateStr) return 'Not graded'
  const date = new Date(dateStr)
  const now = new Date()
  const diffMs = now.getTime() - date.getTime()
  const mins = Math.floor(diffMs / 60000)
  if (mins < 1) return 'Just now'
  if (mins < 60) return `${mins}m ago`
  const hours = Math.floor(mins / 60)
  if (hours < 24) return `${hours}h ago`
  const days = Math.floor(hours / 24)
  return `${days}d ago`
}

export default function StudentDetailPage() {
  const params = useParams()
  const sessionId = Number(params.id)
  const studentId = Number(params.studentId)
  const queryClient = useQueryClient()
  const [isRegrading, setIsRegrading] = useState(false)
  const [regradeError, setRegradeError] = useState<string | null>(null)
  const [activeTab, setActiveTab] = useState<'feedback' | 'files' | 'transparency' | 'override' | 'mapping'>('feedback')
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  // Cleanup polling on unmount
  useEffect(() => {
    return () => {
      if (pollRef.current) clearInterval(pollRef.current)
    }
  }, [])

  const { data: session } = useQuery({
    queryKey: ['session', sessionId],
    queryFn: () => fetchSession(sessionId),
  })

  const { data: student, isLoading, isError } = useQuery({
    queryKey: ['student', sessionId, studentId],
    queryFn: () => fetchStudent(sessionId, studentId),
  })

  const { data: studentFiles } = useQuery({
    queryKey: ['student-files', sessionId, studentId],
    queryFn: () => fetchStudentFiles(sessionId, studentId),
    enabled: !!student,
  })

  const { data: ingestionReport } = useQuery({
    queryKey: ['ingestion-report', sessionId, studentId],
    queryFn: () => fetchIngestionReport(sessionId, studentId),
    enabled: !!student,
  })

  const { data: allStudents } = useQuery({
    queryKey: ['students', sessionId],
    queryFn: () => fetchStudents(sessionId),
  })

  const handleRegrade = useCallback(async () => {
    setIsRegrading(true)
    setRegradeError(null)
    try {
      await regradeStudent(sessionId, studentId)
      // Immediately invalidate cache so next poll gets fresh data
      queryClient.removeQueries({ queryKey: ['student', sessionId, studentId] })

      // Poll for completion. The backend sets status to "pending" then "grading"
      // then finally "graded" or "error". We wait until we see a terminal state
      // AND the graded_at timestamp has changed (to avoid catching stale "graded").
      let attempts = 0
      const maxAttempts = 90 // 3 minutes max for image-heavy submissions
      let seenPendingOrGrading = false

      pollRef.current = setInterval(async () => {
        attempts++
        try {
          const updated = await fetchStudent(sessionId, studentId)

          // Track if we've seen the regrade actually start
          if (updated.status === 'pending' || updated.status === 'grading') {
            seenPendingOrGrading = true
          }

          // Only consider it done if we saw pending/grading first, OR enough time passed
          const isTerminal = updated.status === 'graded' || updated.status === 'error'
          const canStop = isTerminal && (seenPendingOrGrading || attempts >= 3)

          if (canStop) {
            if (pollRef.current) clearInterval(pollRef.current)
            pollRef.current = null
            queryClient.setQueryData(['student', sessionId, studentId], updated)
            queryClient.invalidateQueries({ queryKey: ['students', sessionId] })
            queryClient.invalidateQueries({ queryKey: ['ingestion-report', sessionId, studentId] })
            queryClient.invalidateQueries({ queryKey: ['student-files', sessionId, studentId] })
            setIsRegrading(false)
          }
        } catch {
          // ignore fetch errors during polling
        }
        if (attempts >= maxAttempts) {
          if (pollRef.current) clearInterval(pollRef.current)
          pollRef.current = null
          queryClient.invalidateQueries({ queryKey: ['student', sessionId, studentId] })
          setRegradeError('Regrade is taking longer than expected. Please refresh the page.')
          setIsRegrading(false)
        }
      }, 2000)
    } catch (err) {
      setRegradeError(err instanceof Error ? err.message : 'Regrade failed')
      setIsRegrading(false)
    }
  }, [sessionId, studentId, queryClient])

  if (isLoading) {
    return (
      <AppShell>
        <div className="max-w-7xl mx-auto space-y-6">
          <div className="h-8 w-48 bg-[var(--border)] rounded animate-pulse" />
          <div className="grid grid-cols-4 gap-4">
            {[...Array(4)].map((_, i) => (
              <div key={i} className="h-28 bg-[var(--border)] rounded-xl animate-pulse" />
            ))}
          </div>
          <div className="h-96 bg-[var(--border)] rounded-xl animate-pulse" />
        </div>
      </AppShell>
    )
  }

  if (isError || !student) {
    return (
      <AppShell>
        <div className="max-w-7xl mx-auto text-center py-12">
          <XCircle className="h-12 w-12 text-rose-400 mx-auto mb-3" />
          <p className="text-rose-400 text-lg font-semibold">Student not found</p>
          <p className="text-[var(--text-muted)] text-sm mt-1">
            The student may have been removed or the session doesn&apos;t exist.
          </p>
          <Link href={`/sessions/${sessionId}`}>
            <Button variant="outline" className="mt-4">Back to Session</Button>
          </Link>
        </div>
      </AppShell>
    )
  }

  const maxScore = session?.max_score ?? 100
  const effectiveScore = student.is_overridden && student.override_score != null
    ? student.override_score
    : student.ai_score
  const grade = student.ai_letter_grade || (effectiveScore != null ? scoreToGrade(effectiveScore, maxScore) : null)
  const percentage = effectiveScore != null && maxScore > 0
    ? Math.round((effectiveScore / maxScore) * 100)
    : null
  const confidence = student.ai_confidence || student.ai_result?.confidence || ''
  const confidenceBadge = getConfidenceBadge(confidence)
  const ConfidenceIcon = confidenceBadge.icon

  const navStudents = (allStudents ?? []).map((s) => ({
    id: s.id,
    student_identifier: s.student_identifier,
  }))

  const rubricBreakdown = student.rubric_breakdown?.length
    ? student.rubric_breakdown
    : student.ai_result?.rubric_breakdown ?? []

  const strengths = student.strengths?.length ? student.strengths : student.ai_result?.strengths ?? []
  const weaknesses = student.weaknesses?.length ? student.weaknesses : student.ai_result?.weaknesses ?? []
  const feedback = student.ai_feedback || student.ai_result?.overall_feedback || ''
  const suggestions = student.suggestions_for_improvement || student.ai_result?.suggestions_for_improvement || ''
  const criticalErrors = student.critical_errors || student.ai_result?.critical_errors || []

  const tabs = [
    { key: 'feedback' as const, label: 'AI Analysis', icon: Sparkles, count: rubricBreakdown.length },
    { key: 'files' as const, label: 'Files', icon: FileText, count: studentFiles?.length ?? student.file_count },
    { key: 'mapping' as const, label: 'Mapping', icon: GitBranch },
    { key: 'transparency' as const, label: 'Audit Trail', icon: Shield },
    { key: 'override' as const, label: 'Override', icon: Award },
  ]

  return (
    <AppShell>
      <div className="max-w-7xl mx-auto space-y-6">
        {/* Navigation */}
        {navStudents.length > 0 && (
          <StudentNav
            sessionId={sessionId}
            students={navStudents}
            currentStudentId={studentId}
          />
        )}

        {/* Hero Header Card */}
        <Card className={cn('overflow-hidden border', getGradeBg(grade || ''))}>
          <CardContent className="p-0">
            <div className="flex flex-col md:flex-row">
              {/* Score Section */}
              <div className="flex flex-col items-center justify-center px-8 py-6 md:border-r border-[var(--border)] min-w-[200px]">
                <div className={cn('text-5xl font-black tabular-nums', getGradeColor(grade || ''))}>
                  {grade || '\u2014'}
                </div>
                <div className="text-2xl font-bold text-[var(--text-primary)] mt-1 tabular-nums">
                  {effectiveScore != null ? formatScore(effectiveScore) : '\u2014'}
                  <span className="text-sm font-normal text-[var(--text-muted)]"> / {maxScore}</span>
                </div>
                {percentage != null && (
                  <div className="text-sm text-[var(--text-muted)] font-medium mt-0.5">
                    {percentage}%
                  </div>
                )}
                {student.is_overridden && (
                  <Badge variant="info" className="mt-2 text-[10px]">Override Applied</Badge>
                )}
              </div>

              {/* Info Section */}
              <div className="flex-1 p-6 space-y-4">
                <div className="flex items-start justify-between">
                  <div>
                    <div className="flex items-center gap-3">
                      <Link href={`/sessions/${sessionId}`}>
                        <Button variant="ghost" size="sm" className="h-7 w-7 p-0">
                          <ArrowLeft className="h-4 w-4" />
                        </Button>
                      </Link>
                      <h1 className="text-xl font-bold text-[var(--text-primary)]">
                        {student.student_identifier}
                      </h1>
                      <Badge
                        variant={
                          student.status === 'graded' ? 'success'
                          : student.status === 'error' ? 'error'
                          : student.status === 'grading' ? 'warning'
                          : 'default'
                        }
                      >
                        {student.status}
                      </Badge>
                      {student.is_flagged && (
                        <Badge variant="warning" className="gap-1">
                          <Flag className="h-3 w-3" />
                          {student.flag_reason || 'Flagged'}
                        </Badge>
                      )}
                    </div>
                    {session && (
                      <p className="text-sm text-[var(--text-muted)] mt-1 ml-10">
                        {session.title}
                      </p>
                    )}
                  </div>
                  <div className="flex items-center gap-2">
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={handleRegrade}
                      disabled={isRegrading || student.status === 'grading'}
                      className="h-8 gap-1.5 text-xs"
                    >
                      <RefreshCw className={cn('h-3.5 w-3.5', isRegrading && 'animate-spin')} />
                      {isRegrading ? 'Regrading...' : 'Regrade'}
                    </Button>
                  </div>
                </div>

                {/* Regrade error banner */}
                {regradeError && (
                  <div className="ml-10 mt-2 rounded-md bg-rose-500/10 border border-rose-500/20 p-3 text-sm text-rose-300">
                    <strong>Regrade failed:</strong> {regradeError}
                  </div>
                )}

                {/* Quick Stats Row */}
                <div className="grid grid-cols-2 md:grid-cols-4 gap-3 ml-10">
                  <QuickStat
                    icon={<BarChart3 className="h-3.5 w-3.5 text-indigo-400" />}
                    label="Confidence"
                    value={
                      <span className="flex items-center gap-1">
                        <ConfidenceIcon className="h-3 w-3" />
                        <span className="capitalize">{confidence || 'N/A'}</span>
                      </span>
                    }
                  />
                  <QuickStat
                    icon={<FileText className="h-3.5 w-3.5 text-violet-400" />}
                    label="Files"
                    value={String(studentFiles?.length ?? student.file_count ?? 0)}
                  />
                  <QuickStat
                    icon={<Clock className="h-3.5 w-3.5 text-amber-400" />}
                    label="Graded"
                    value={formatTimeAgo(student.graded_at)}
                  />
                  <QuickStat
                    icon={<TrendingUp className="h-3.5 w-3.5 text-emerald-400" />}
                    label="Criteria"
                    value={`${rubricBreakdown.length} items`}
                  />
                </div>

                {/* Error Banner */}
                {student.status === 'error' && student.error_message && (
                  <div className="ml-10 rounded-lg border border-rose-500/30 bg-rose-500/5 px-4 py-3 flex items-start gap-3">
                    <AlertTriangle className="h-4 w-4 shrink-0 text-rose-400 mt-0.5" />
                    <div>
                      <p className="text-sm font-medium text-rose-400">Grading Error</p>
                      <p className="text-xs text-rose-400/80 mt-0.5">{student.error_message}</p>
                    </div>
                  </div>
                )}
              </div>
            </div>
          </CardContent>
        </Card>

        {/* Tab Navigation */}
        <div className="flex items-center gap-1 border-b border-[var(--border)] pb-px">
          {tabs.map((tab) => {
            const Icon = tab.icon
            const isActive = activeTab === tab.key
            return (
              <button
                key={tab.key}
                onClick={() => setActiveTab(tab.key)}
                className={cn(
                  'flex items-center gap-2 px-4 py-2.5 text-sm font-medium rounded-t-lg transition-colors border-b-2 -mb-px',
                  isActive
                    ? 'text-indigo-400 border-indigo-400 bg-indigo-500/5'
                    : 'text-[var(--text-muted)] border-transparent hover:text-[var(--text-primary)] hover:bg-[var(--border)]/50'
                )}
              >
                <Icon className="h-4 w-4" />
                {tab.label}
                {tab.count != null && tab.count > 0 && (
                  <span className={cn(
                    'text-[10px] font-bold rounded-full px-1.5 py-0.5 min-w-[20px] text-center',
                    isActive ? 'bg-indigo-500/20 text-indigo-400' : 'bg-[var(--border)] text-[var(--text-muted)]'
                  )}>
                    {tab.count}
                  </span>
                )}
              </button>
            )
          })}
        </div>

        {/* Tab Content */}
        <div className="min-h-[500px]">
          {activeTab === 'feedback' && (
            <AIFeedbackPanel
              submission={student}
              rubricBreakdown={rubricBreakdown}
              strengths={strengths}
              weaknesses={weaknesses}
              feedback={feedback}
              suggestions={suggestions}
              criticalErrors={criticalErrors}
              maxScore={maxScore}
            />
          )}

          {activeTab === 'files' && (
            <FileBrowser
              sessionId={sessionId}
              studentId={studentId}
              files={studentFiles ?? []}
            />
          )}

          {activeTab === 'transparency' && (
            <TransparencyVault
              submission={student}
              ingestionReport={ingestionReport ?? undefined}
            />
          )}

          {activeTab === 'mapping' && (
            <ProcessingMapping
              submission={student}
              ingestionReport={ingestionReport ?? undefined}
              files={studentFiles ?? []}
            />
          )}

          {activeTab === 'override' && (
            <OverrideForm
              sessionId={sessionId}
              studentId={studentId}
              currentScore={student.ai_score}
              maxScore={maxScore}
              isOverridden={student.is_overridden}
              overrideScore={student.override_score}
              overrideComments={student.override_comments}
              grade={grade || ''}
            />
          )}
        </div>
      </div>
    </AppShell>
  )
}

function QuickStat({ icon, label, value }: { icon: React.ReactNode; label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-center gap-2 rounded-lg px-3 py-2 bg-[var(--bg-card)] border border-[var(--border)]">
      {icon}
      <div>
        <p className="text-[10px] text-[var(--text-muted)] uppercase tracking-wider">{label}</p>
        <p className="text-xs font-semibold text-[var(--text-primary)]">{value}</p>
      </div>
    </div>
  )
}
