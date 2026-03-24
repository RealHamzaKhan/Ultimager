'use client'

import { useParams } from 'next/navigation'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { AppShell } from '@/components/layout/app-shell'
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { fetchSession } from '@/lib/api'
import type { SessionStatus } from '@/lib/types'
import Link from 'next/link'
import {
  BarChart3,
  Download,
  Play,
  Square,
  Users,
  CheckCircle2,
  AlertTriangle,
  Gauge,
  Upload,
  RefreshCw,
} from 'lucide-react'
import { StudentTable } from '@/components/session/student-table'
import { GradingTheater } from '@/components/session/grading-theater'
import { UploadZone } from '@/components/session/upload-zone'
import { useStartGrading, useStopGrading } from '@/hooks/use-mutations'
import { uploadSubmissions } from '@/lib/api'

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
  grading: 'Grading',
  complete: 'Complete',
  completed: 'Complete',
  completed_with_errors: 'Completed with Errors',
  error: 'Error',
  stopped: 'Stopped',
  paused: 'Stopped',
}

function isCompleted(status: SessionStatus): boolean {
  return status === 'complete' || status === 'completed' || status === 'completed_with_errors'
}

function isGrading(status: SessionStatus): boolean {
  return status === 'grading'
}

export default function SessionDetailPage() {
  const params = useParams()
  const sessionId = Number(params.id)

  const queryClient = useQueryClient()
  const startGrading = useStartGrading()
  const stopGrading = useStopGrading()

  const handleUpload = async (file: File) => {
    await uploadSubmissions(sessionId, file)
    queryClient.invalidateQueries({ queryKey: ['session', sessionId] })
    queryClient.invalidateQueries({ queryKey: ['students', sessionId] })
  }

  const { data: session, isLoading, isError } = useQuery({
    queryKey: ['session', sessionId],
    queryFn: () => fetchSession(sessionId),
    refetchInterval: (query) => {
      const status = query.state.data?.status
      return status === 'grading' ? 2000 : false
    },
  })

  if (isLoading) {
    return (
      <AppShell>
        <div className="max-w-7xl mx-auto space-y-6">
          <div className="h-8 w-64 bg-[var(--border)] rounded animate-pulse" />
          <div className="grid grid-cols-4 gap-4">
            {[...Array(4)].map((_, i) => (
              <div key={i} className="h-24 bg-[var(--border)] rounded-xl animate-pulse" />
            ))}
          </div>
          <div className="h-96 bg-[var(--border)] rounded-xl animate-pulse" />
        </div>
      </AppShell>
    )
  }

  if (isError || !session) {
    return (
      <AppShell>
        <div className="max-w-7xl mx-auto text-center py-12">
          <p className="text-rose-500 text-lg">Session not found</p>
          <Link href="/">
            <Button variant="outline" className="mt-4">Back to Dashboard</Button>
          </Link>
        </div>
      </AppShell>
    )
  }

  const progress = session.total_students > 0
    ? Math.round((session.graded_count / session.total_students) * 100)
    : 0

  const canStartGrading = session.total_students > 0 && !isCompleted(session.status) && !isGrading(session.status)
  const showUpload = (session.status === 'pending' || session.status === 'stopped' || session.status === 'paused' || session.total_students === 0) && !isGrading(session.status)

  return (
    <AppShell>
      <div className="max-w-7xl mx-auto space-y-6">
        {/* Header */}
        <div className="flex items-start justify-between">
          <div>
            <div className="flex items-center gap-3">
              <h1 className="text-2xl font-bold text-[var(--text-primary)]">
                {session.title}
              </h1>
              <Badge
                variant={statusVariant[session.status] || 'default'}
                pulse={isGrading(session.status)}
              >
                {statusLabel[session.status] || session.status}
              </Badge>
            </div>
            {session.description && (
              <p className="text-sm text-[var(--text-muted)] mt-1 max-w-2xl">
                {session.description}
              </p>
            )}
          </div>
          <div className="flex items-center gap-2">
            {isGrading(session.status) ? (
              <Button
                variant="danger"
                size="sm"
                onClick={() => stopGrading.mutate({ sessionId })}
                disabled={stopGrading.isPending}
                className="gap-1.5"
              >
                <Square className="h-3.5 w-3.5" />
                Stop Grading
              </Button>
            ) : canStartGrading ? (
              <Button
                variant="primary"
                size="sm"
                onClick={() => startGrading.mutate({ sessionId })}
                disabled={startGrading.isPending}
                className="gap-1.5"
              >
                {startGrading.isPending ? (
                  <RefreshCw className="h-3.5 w-3.5 animate-spin" />
                ) : (
                  <Play className="h-3.5 w-3.5" />
                )}
                {startGrading.isPending ? 'Starting...' : 'Start Grading'}
              </Button>
            ) : null}
            {isCompleted(session.status) && (
              <Link href={`/sessions/${sessionId}/results`}>
                <Button variant="outline" size="sm" className="gap-1.5">
                  <BarChart3 className="h-3.5 w-3.5" />
                  Results
                </Button>
              </Link>
            )}
            {session.total_students > 0 && (
              <a href={`http://localhost:8000/session/${sessionId}/export/csv`}>
                <Button variant="outline" size="sm" className="gap-1.5">
                  <Download className="h-3.5 w-3.5" />
                  CSV
                </Button>
              </a>
            )}
          </div>
        </div>

        {/* Stats Cards */}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <StatCard
            icon={<Users className="h-5 w-5 text-indigo-400" />}
            label="Total Students"
            value={session.total_students}
          />
          <StatCard
            icon={<CheckCircle2 className="h-5 w-5 text-emerald-400" />}
            label="Graded"
            value={session.graded_count}
            accent="emerald"
          />
          <StatCard
            icon={<AlertTriangle className="h-5 w-5 text-rose-400" />}
            label="Errors"
            value={session.error_count}
            accent={session.error_count > 0 ? 'rose' : undefined}
          />
          <StatCard
            icon={<Gauge className="h-5 w-5 text-violet-400" />}
            label="Progress"
            value={`${progress}%`}
            accent="violet"
          />
        </div>

        {/* Grading Theater - shown during active grading */}
        {isGrading(session.status) && (
          <GradingTheater
            sessionId={sessionId}
            onComplete={() => {
              queryClient.invalidateQueries({ queryKey: ['session', sessionId] })
              queryClient.invalidateQueries({ queryKey: ['students', sessionId] })
            }}
          />
        )}

        {/* Upload Zone */}
        {showUpload && (
          <UploadZone
            sessionId={sessionId}
            onUpload={handleUpload}
          />
        )}

        {/* Students Table */}
        {session.total_students > 0 && (
          <Card>
            <CardHeader>
              <div className="flex items-center justify-between">
                <CardTitle className="flex items-center gap-2">
                  <Users className="h-5 w-5 text-[var(--text-muted)]" />
                  Students ({session.total_students})
                </CardTitle>
              </div>
            </CardHeader>
            <CardContent>
              <StudentTable sessionId={sessionId} maxScore={session.max_score} />
            </CardContent>
          </Card>
        )}
      </div>
    </AppShell>
  )
}

function StatCard({
  icon,
  label,
  value,
  accent,
}: {
  icon: React.ReactNode
  label: string
  value: number | string
  accent?: 'emerald' | 'rose' | 'violet'
}) {
  const valueColor = accent === 'emerald'
    ? 'text-emerald-400'
    : accent === 'rose'
      ? 'text-rose-400'
      : accent === 'violet'
        ? 'text-violet-400'
        : 'text-[var(--text-primary)]'

  return (
    <Card className="relative overflow-hidden">
      <div className="flex items-center gap-3 p-4">
        <div className="shrink-0 rounded-lg bg-[var(--border)] p-2">
          {icon}
        </div>
        <div>
          <p className="text-xs text-[var(--text-muted)] font-medium">{label}</p>
          <p className={`text-xl font-bold tabular-nums ${valueColor}`}>{value}</p>
        </div>
      </div>
    </Card>
  )
}
