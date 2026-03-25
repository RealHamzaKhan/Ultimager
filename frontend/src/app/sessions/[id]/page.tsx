'use client'

import { useParams } from 'next/navigation'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useState } from 'react'
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
  ChevronDown,
  ChevronUp,
  FileText,
  BookOpen,
  ArrowLeft,
  Clock,
  UserPlus,
} from 'lucide-react'
import { StudentTable } from '@/components/session/student-table'
import { GradingTheater } from '@/components/session/grading-theater'
import { UploadZone } from '@/components/session/upload-zone'
import { AddStudentDialog } from '@/components/session/add-student-dialog'
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

type TabKey = 'students' | 'description' | 'rubric'

export default function SessionDetailPage() {
  const params = useParams()
  const sessionId = Number(params.id)

  const queryClient = useQueryClient()
  const startGrading = useStartGrading()
  const stopGrading = useStopGrading()
  const [activeTab, setActiveTab] = useState<TabKey>('students')
  const [descExpanded, setDescExpanded] = useState(false)
  const [addStudentOpen, setAddStudentOpen] = useState(false)

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
          {/* Skeleton header */}
          <div className="space-y-3">
            <div className="h-5 w-32 bg-zinc-800 rounded animate-pulse" />
            <div className="h-9 w-80 bg-zinc-800 rounded animate-pulse" />
          </div>
          {/* Skeleton stats */}
          <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
            {[...Array(4)].map((_, i) => (
              <div key={i} className="h-28 bg-zinc-800/60 rounded-xl animate-pulse border border-zinc-700/50" />
            ))}
          </div>
          {/* Skeleton table */}
          <div className="h-96 bg-zinc-800/60 rounded-xl animate-pulse border border-zinc-700/50" />
        </div>
      </AppShell>
    )
  }

  if (isError || !session) {
    return (
      <AppShell>
        <div className="max-w-7xl mx-auto text-center py-20">
          <div className="inline-flex items-center justify-center w-16 h-16 rounded-full bg-rose-500/10 mb-4">
            <AlertTriangle className="h-8 w-8 text-rose-400" />
          </div>
          <p className="text-rose-400 text-lg font-medium">Session not found</p>
          <p className="text-zinc-500 text-sm mt-1">This session may have been deleted or does not exist.</p>
          <Link href="/">
            <Button variant="outline" className="mt-6 gap-2">
              <ArrowLeft className="h-4 w-4" />
              Back to Dashboard
            </Button>
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

  const descriptionLines = session.description?.split('\n') || []
  const hasLongDescription = session.description ? session.description.length > 200 || descriptionLines.length > 3 : false

  const canAddStudent = !isGrading(session.status)

  const tabs: { key: TabKey; label: string; icon: React.ReactNode; count?: number }[] = [
    { key: 'students', label: 'Students', icon: <Users className="h-4 w-4" />, count: session.total_students },
    { key: 'description', label: 'Description', icon: <FileText className="h-4 w-4" /> },
    { key: 'rubric', label: 'Rubric', icon: <BookOpen className="h-4 w-4" /> },
  ]

  return (
    <AppShell>
      <div className="max-w-7xl mx-auto space-y-6">
        {/* Breadcrumb */}
        <Link href="/" className="inline-flex items-center gap-1.5 text-sm text-zinc-500 hover:text-zinc-300 transition-colors">
          <ArrowLeft className="h-3.5 w-3.5" />
          Sessions
        </Link>

        {/* Header Card */}
        <div className="rounded-xl border border-zinc-700/50 bg-zinc-800/40 p-6">
          <div className="flex items-start justify-between gap-4">
            {/* Left: Title + status + short description */}
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-3 flex-wrap">
                <h1 className="text-2xl font-bold text-zinc-100 truncate">
                  {session.title}
                </h1>
                <Badge
                  variant={statusVariant[session.status] || 'default'}
                  pulse={isGrading(session.status)}
                >
                  {statusLabel[session.status] || session.status}
                </Badge>
              </div>

              {/* Inline short description preview */}
              {session.description && (
                <div className="mt-3">
                  <p className={`text-sm text-zinc-400 leading-relaxed whitespace-pre-wrap ${!descExpanded && hasLongDescription ? 'line-clamp-3' : ''}`}>
                    {session.description}
                  </p>
                  {hasLongDescription && (
                    <button
                      onClick={() => setDescExpanded(!descExpanded)}
                      className="mt-1.5 inline-flex items-center gap-1 text-xs text-indigo-400 hover:text-indigo-300 transition-colors font-medium"
                    >
                      {descExpanded ? (
                        <>Show less <ChevronUp className="h-3 w-3" /></>
                      ) : (
                        <>Show more <ChevronDown className="h-3 w-3" /></>
                      )}
                    </button>
                  )}
                </div>
              )}
            </div>

            {/* Right: Action buttons */}
            <div className="flex items-center gap-2 shrink-0">
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
        </div>

        {/* Stats Cards */}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          <StatCard
            icon={<Users className="h-5 w-5" />}
            iconBg="bg-indigo-500/10"
            iconColor="text-indigo-400"
            label="Total Students"
            value={session.total_students}
          />
          <StatCard
            icon={<CheckCircle2 className="h-5 w-5" />}
            iconBg="bg-emerald-500/10"
            iconColor="text-emerald-400"
            label="Graded"
            value={session.graded_count}
            accent="emerald"
          />
          <StatCard
            icon={<AlertTriangle className="h-5 w-5" />}
            iconBg="bg-rose-500/10"
            iconColor="text-rose-400"
            label="Errors"
            value={session.error_count}
            accent={session.error_count > 0 ? 'rose' : undefined}
          />
          <StatCard
            icon={<Gauge className="h-5 w-5" />}
            iconBg="bg-violet-500/10"
            iconColor="text-violet-400"
            label="Progress"
            value={`${progress}%`}
            accent="violet"
            progressBar={progress}
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

        {/* Tabs */}
        <div>
          {/* Tab bar */}
          <div className="flex items-center gap-1 border-b border-zinc-700/50 mb-0">
            {tabs.map((tab) => (
              <button
                key={tab.key}
                onClick={() => setActiveTab(tab.key)}
                className={`
                  inline-flex items-center gap-2 px-4 py-2.5 text-sm font-medium transition-colors relative
                  ${activeTab === tab.key
                    ? 'text-zinc-100'
                    : 'text-zinc-500 hover:text-zinc-300'
                  }
                `}
              >
                {tab.icon}
                {tab.label}
                {tab.count !== undefined && tab.count > 0 && (
                  <span className={`
                    text-xs px-1.5 py-0.5 rounded-full
                    ${activeTab === tab.key
                      ? 'bg-indigo-500/20 text-indigo-300'
                      : 'bg-zinc-700/50 text-zinc-500'
                    }
                  `}>
                    {tab.count}
                  </span>
                )}
                {/* Active indicator */}
                {activeTab === tab.key && (
                  <span className="absolute bottom-0 left-0 right-0 h-0.5 bg-indigo-500 rounded-full" />
                )}
              </button>
            ))}
          </div>

          {/* Tab content */}
          <div className="mt-5">
            {/* Students tab */}
            {activeTab === 'students' && (
              <>
                {session.total_students > 0 ? (
                  <Card className="p-0 overflow-hidden">
                    <div className="px-6 py-4 border-b border-zinc-700/50 flex items-center justify-between">
                      <div className="flex items-center gap-2">
                        <Users className="h-4 w-4 text-zinc-500" />
                        <span className="text-sm font-medium text-zinc-300">
                          {session.total_students} student{session.total_students !== 1 ? 's' : ''}
                        </span>
                      </div>
                      {canAddStudent && (
                        <Button
                          variant="outline"
                          size="sm"
                          onClick={() => setAddStudentOpen(true)}
                          className="gap-1.5"
                        >
                          <UserPlus className="h-3.5 w-3.5" />
                          Add Student
                        </Button>
                      )}
                    </div>
                    <div className="p-0">
                      <StudentTable sessionId={sessionId} maxScore={session.max_score} />
                    </div>
                  </Card>
                ) : (
                  <div className="text-center py-16 rounded-xl border border-zinc-700/30 border-dashed bg-zinc-800/20">
                    <Users className="h-10 w-10 text-zinc-600 mx-auto mb-3" />
                    <p className="text-zinc-400 font-medium">No students yet</p>
                    <p className="text-zinc-600 text-sm mt-1">Upload submissions to get started</p>
                    {canAddStudent && (
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() => setAddStudentOpen(true)}
                        className="gap-1.5 mt-4"
                      >
                        <UserPlus className="h-3.5 w-3.5" />
                        Add Student Manually
                      </Button>
                    )}
                  </div>
                )}

                {/* Add Student Dialog */}
                <AddStudentDialog
                  sessionId={sessionId}
                  open={addStudentOpen}
                  onClose={() => setAddStudentOpen(false)}
                  onSuccess={() => {
                    queryClient.invalidateQueries({ queryKey: ['session', sessionId] })
                    queryClient.invalidateQueries({ queryKey: ['students', sessionId] })
                  }}
                />
              </>
            )}

            {/* Description tab */}
            {activeTab === 'description' && (
              <Card>
                <CardHeader>
                  <CardTitle className="flex items-center gap-2 text-base">
                    <FileText className="h-4 w-4 text-zinc-500" />
                    Assignment Description
                  </CardTitle>
                </CardHeader>
                <CardContent>
                  {session.description ? (
                    <div className="prose prose-invert prose-sm max-w-none">
                      <p className="text-sm text-zinc-300 leading-relaxed whitespace-pre-wrap">
                        {session.description}
                      </p>
                    </div>
                  ) : (
                    <p className="text-sm text-zinc-500 italic">No description provided.</p>
                  )}
                </CardContent>
              </Card>
            )}

            {/* Rubric tab */}
            {activeTab === 'rubric' && (
              <Card>
                <CardHeader>
                  <CardTitle className="flex items-center gap-2 text-base">
                    <BookOpen className="h-4 w-4 text-zinc-500" />
                    Rubric
                  </CardTitle>
                </CardHeader>
                <CardContent>
                  {session.rubric ? (
                    <div className="prose prose-invert prose-sm max-w-none">
                      <p className="text-sm text-zinc-300 leading-relaxed whitespace-pre-wrap">
                        {session.rubric}
                      </p>
                    </div>
                  ) : (
                    <div className="text-center py-10">
                      <BookOpen className="h-8 w-8 text-zinc-600 mx-auto mb-2" />
                      <p className="text-sm text-zinc-500">No rubric defined for this session.</p>
                    </div>
                  )}
                </CardContent>
              </Card>
            )}
          </div>
        </div>
      </div>
    </AppShell>
  )
}

function StatCard({
  icon,
  iconBg,
  iconColor,
  label,
  value,
  accent,
  progressBar,
}: {
  icon: React.ReactNode
  iconBg: string
  iconColor: string
  label: string
  value: number | string
  accent?: 'emerald' | 'rose' | 'violet'
  progressBar?: number
}) {
  const valueColor = accent === 'emerald'
    ? 'text-emerald-400'
    : accent === 'rose'
      ? 'text-rose-400'
      : accent === 'violet'
        ? 'text-violet-400'
        : 'text-zinc-100'

  return (
    <div className="rounded-xl border border-zinc-700/50 bg-zinc-800/40 p-4 relative overflow-hidden group hover:border-zinc-600/50 transition-colors">
      <div className="flex items-start justify-between">
        <div className={`rounded-lg ${iconBg} p-2.5 ${iconColor}`}>
          {icon}
        </div>
      </div>
      <div className="mt-3">
        <p className="text-xs text-zinc-500 font-medium uppercase tracking-wide">{label}</p>
        <p className={`text-2xl font-bold tabular-nums mt-0.5 ${valueColor}`}>{value}</p>
      </div>
      {/* Optional progress bar for the progress card */}
      {progressBar !== undefined && (
        <div className="mt-3 h-1.5 bg-zinc-700/50 rounded-full overflow-hidden">
          <div
            className="h-full bg-violet-500 rounded-full transition-all duration-500 ease-out"
            style={{ width: `${progressBar}%` }}
          />
        </div>
      )}
    </div>
  )
}
