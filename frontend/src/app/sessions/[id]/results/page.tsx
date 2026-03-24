'use client'

import { useMemo } from 'react'
import { useParams } from 'next/navigation'
import { useQuery } from '@tanstack/react-query'
import { AppShell } from '@/components/layout/app-shell'
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { fetchSession, fetchStudents } from '@/lib/api'
import Link from 'next/link'
import { ArrowLeft } from 'lucide-react'
import { KeyMetrics } from '@/components/analytics/key-metrics'
import { ScoreHistogram } from '@/components/analytics/score-histogram'
import { ExportControls } from '@/components/analytics/export-controls'
import type { AnalyticsData, Submission } from '@/lib/types'

function computeAnalytics(students: Submission[], session: { total_students: number; graded_count: number; error_count: number }): AnalyticsData {
  const scores = students
    .filter((s) => s.status === 'graded' && s.ai_score != null)
    .map((s) => s.override_score ?? s.ai_score!)

  const sorted = [...scores].sort((a, b) => a - b)
  const average = scores.length > 0 ? scores.reduce((a, b) => a + b, 0) / scores.length : null
  const median = sorted.length > 0
    ? sorted.length % 2 === 0
      ? (sorted[sorted.length / 2 - 1] + sorted[sorted.length / 2]) / 2
      : sorted[Math.floor(sorted.length / 2)]
    : null

  const gradeDistribution: Record<string, number> = {}
  const flaggedCount = students.filter((s) => s.is_flagged).length

  return {
    total_students: session.total_students,
    graded_count: session.graded_count,
    error_count: session.error_count,
    average_score: average,
    median_score: median,
    pass_rate: scores.length > 0 ? scores.filter((s) => s >= 60).length / scores.length : null,
    grade_distribution: gradeDistribution,
    score_distribution: scores,
    flagged_count: flaggedCount,
  }
}

export default function ResultsPage() {
  const params = useParams()
  const sessionId = Number(params.id)

  const { data: session, isLoading } = useQuery({
    queryKey: ['session', sessionId],
    queryFn: () => fetchSession(sessionId),
  })

  const { data: students } = useQuery({
    queryKey: ['students', sessionId],
    queryFn: () => fetchStudents(sessionId),
    enabled: !!session,
  })

  const analytics = useMemo(() => {
    if (!session || !students) return null
    return computeAnalytics(students, session)
  }, [session, students])

  if (isLoading) {
    return (
      <AppShell>
        <div className="max-w-7xl mx-auto animate-pulse">
          <div className="h-8 w-48 bg-slate-200 dark:bg-slate-700 rounded mb-4" />
          <div className="grid grid-cols-3 gap-4 mb-6">
            {[1, 2, 3].map((i) => (
              <div key={i} className="h-24 bg-slate-200 dark:bg-slate-700 rounded-xl" />
            ))}
          </div>
        </div>
      </AppShell>
    )
  }

  if (!session) {
    return (
      <AppShell>
        <div className="text-center py-12 text-rose-500">Session not found</div>
      </AppShell>
    )
  }

  const maxScore = session.max_score

  return (
    <AppShell>
      <div className="max-w-7xl mx-auto space-y-6">
        {/* Header */}
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-3">
            <Link href={`/sessions/${sessionId}`}>
              <Button variant="ghost" size="sm">
                <ArrowLeft className="h-4 w-4" />
              </Button>
            </Link>
            <h1 className="text-2xl font-bold text-[var(--text-primary)]">
              Results: {session.title}
            </h1>
          </div>
          <ExportControls sessionId={sessionId} />
        </div>

        {/* Key Metrics */}
        <KeyMetrics data={analytics} isLoading={!analytics} />

        {/* Score Distribution */}
        {analytics && analytics.score_distribution.length > 0 && (
          <Card>
            <CardHeader>
              <CardTitle>Score Distribution</CardTitle>
            </CardHeader>
            <CardContent>
              <ScoreHistogram scores={analytics.score_distribution} maxScore={maxScore} />
            </CardContent>
          </Card>
        )}

        {/* Summary stats */}
        <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-4">
          <Card>
            <p className="text-xs text-[var(--text-muted)]">Total Students</p>
            <p className="text-xl font-bold text-[var(--text-primary)]">{session.total_students}</p>
          </Card>
          <Card>
            <p className="text-xs text-[var(--text-muted)]">Graded</p>
            <p className="text-xl font-bold text-emerald-500">{session.graded_count}</p>
          </Card>
          <Card>
            <p className="text-xs text-[var(--text-muted)]">Errors</p>
            <p className="text-xl font-bold text-rose-500">{session.error_count}</p>
          </Card>
          <Card>
            <p className="text-xs text-[var(--text-muted)]">Max Score</p>
            <p className="text-xl font-bold text-[var(--text-primary)]">{session.max_score}</p>
          </Card>
          <Card>
            <p className="text-xs text-[var(--text-muted)]">Status</p>
            <Badge variant={session.status === 'complete' ? 'success' : 'default'}>
              {session.status}
            </Badge>
          </Card>
          <Card>
            <p className="text-xs text-[var(--text-muted)]">Pass Rate</p>
            <p className="text-xl font-bold text-[var(--text-primary)]">
              {session.total_students > 0
                ? `${Math.round((session.graded_count / session.total_students) * 100)}%`
                : '—'}
            </p>
          </Card>
        </div>
      </div>
    </AppShell>
  )
}
