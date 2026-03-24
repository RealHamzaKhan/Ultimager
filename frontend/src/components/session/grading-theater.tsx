'use client'

import { useEffect } from 'react'
import { Card, CardContent } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { useGradeStream, type GradedStudent } from '@/hooks/use-grade-stream'
import { useStopGrading } from '@/hooks/use-mutations'
import { cn } from '@/lib/utils'
import {
  Square,
  Wifi,
  WifiOff,
  RefreshCw,
  CheckCircle2,
  Loader2,
  AlertCircle,
  Clock,
  Zap,
  Brain,
  Users,
  XCircle,
  TrendingUp,
} from 'lucide-react'

function formatTime(seconds: number): string {
  const m = Math.floor(seconds / 60)
  const s = seconds % 60
  return `${m}:${s.toString().padStart(2, '0')}`
}

function getEta(graded: number, total: number, elapsed: number): string {
  if (graded === 0 || total === 0) return '--:--'
  const rate = graded / elapsed
  const remaining = total - graded
  const etaSeconds = Math.ceil(remaining / rate)
  return formatTime(etaSeconds)
}

function getGradeColor(grade: string): string {
  if (!grade) return 'text-[var(--text-muted)]'
  if (grade.startsWith('A')) return 'text-emerald-400'
  if (grade.startsWith('B')) return 'text-blue-400'
  if (grade.startsWith('C')) return 'text-amber-400'
  if (grade.startsWith('D')) return 'text-orange-400'
  return 'text-rose-400'
}

interface GradingTheaterProps {
  sessionId: number
  onComplete?: () => void
}

export function GradingTheater({ sessionId, onComplete }: GradingTheaterProps) {
  const stream = useGradeStream(sessionId)
  const stopGrading = useStopGrading()

  useEffect(() => {
    if (stream.isComplete && onComplete) onComplete()
  }, [stream.isComplete, onComplete])

  const { graded, failed, total, elapsed, completedStudents } = stream
  const percentage = total > 0 ? Math.round((graded / total) * 100) : 0
  const rate = elapsed > 0 ? (graded / (elapsed / 60)).toFixed(1) : '0.0'

  // ---- Complete state ----
  if (stream.isComplete) {
    return (
      <div data-testid="grading-theater" className="space-y-4">
        <Card className="border-emerald-500/20 bg-gradient-to-br from-emerald-500/5 to-emerald-600/10 overflow-hidden">
          <CardContent className="relative py-8">
            {/* Background glow */}
            <div className="absolute inset-0 bg-gradient-to-r from-emerald-500/5 via-transparent to-emerald-500/5 animate-pulse" />
            <div className="relative flex flex-col items-center gap-4">
              <div className="rounded-full bg-emerald-500/10 p-4">
                <CheckCircle2 className="h-12 w-12 text-emerald-400" />
              </div>
              <h2 className="text-xl font-bold text-[var(--text-primary)]">
                Grading Complete
              </h2>
              <div className="flex items-center gap-8">
                <Stat label="Graded" value={graded} color="text-emerald-400" />
                <div className="h-8 w-px bg-[var(--border)]" />
                <Stat label="Failed" value={failed} color="text-rose-400" />
                <div className="h-8 w-px bg-[var(--border)]" />
                <Stat label="Total" value={total} color="text-[var(--text-primary)]" />
                <div className="h-8 w-px bg-[var(--border)]" />
                <Stat label="Time" value={formatTime(elapsed)} color="text-indigo-400" isText />
              </div>
            </div>
          </CardContent>
        </Card>
      </div>
    )
  }

  return (
    <div data-testid="grading-theater" className="space-y-4">
      {/* Error banner */}
      {stream.error && (
        <div className="rounded-lg border border-rose-500/30 bg-rose-500/5 px-4 py-3 flex items-center gap-3">
          <AlertCircle className="h-4 w-4 shrink-0 text-rose-400" />
          <p className="text-sm text-rose-400">{stream.error}</p>
        </div>
      )}

      {/* Main progress card */}
      <Card className="border-indigo-500/10 overflow-hidden">
        <CardContent className="p-0">
          {/* Header bar */}
          <div className="flex items-center justify-between px-5 py-3 border-b border-[var(--border)]">
            <div className="flex items-center gap-3">
              <ConnectionDot connected={stream.connected} />
              <div className="flex items-center gap-2">
                <Brain className="h-4 w-4 text-indigo-400" />
                <span className="text-sm font-semibold text-[var(--text-primary)]">
                  AI Grading
                </span>
              </div>
              {stream.stage && (
                <Badge variant="info" className="text-xs">
                  {stream.stage}
                </Badge>
              )}
            </div>
            <div className="flex items-center gap-2">
              {!stream.connected && !stream.isStopped && (
                <Button variant="ghost" size="sm" onClick={stream.reconnect} className="h-7 gap-1 px-2 text-xs">
                  <RefreshCw className="h-3 w-3" />
                  Retry
                </Button>
              )}
              {!stream.isStopped && (
                <Button
                  data-testid="stop-btn"
                  variant="danger"
                  size="sm"
                  onClick={() => stopGrading.mutate({ sessionId })}
                  disabled={stopGrading.isPending}
                  className="h-7 gap-1 px-3 text-xs"
                >
                  {stopGrading.isPending ? (
                    <Loader2 className="h-3 w-3 animate-spin" />
                  ) : (
                    <Square className="h-3 w-3" />
                  )}
                  Stop
                </Button>
              )}
              {stream.isStopped && (
                <Badge variant="warning">Stopped</Badge>
              )}
            </div>
          </div>

          {/* Progress bar section */}
          <div className="px-5 py-4">
            <div className="flex items-end justify-between mb-2">
              <div className="flex items-baseline gap-2">
                <span className="text-3xl font-bold tabular-nums text-indigo-400">
                  {percentage}%
                </span>
                <span className="text-sm text-[var(--text-muted)]">
                  {graded}/{total} students
                </span>
              </div>
              {stream.currentStudent && (
                <div className="flex items-center gap-2 max-w-[40%]">
                  <Loader2 className="h-3.5 w-3.5 animate-spin text-indigo-400 shrink-0" />
                  <span className="text-sm text-[var(--text-muted)] truncate">
                    {stream.currentStudent}
                  </span>
                </div>
              )}
            </div>

            {/* Progress bar */}
            <div className="relative h-2.5 w-full rounded-full bg-[var(--border)] overflow-hidden">
              <div
                data-testid="progress-bar"
                className="h-full rounded-full bg-gradient-to-r from-indigo-500 via-violet-500 to-purple-500 transition-all duration-700 ease-out"
                style={{ width: `${percentage}%` }}
              />
              {/* Shimmer effect */}
              {percentage < 100 && percentage > 0 && (
                <div
                  className="absolute inset-0 bg-gradient-to-r from-transparent via-white/10 to-transparent"
                  style={{
                    animation: 'shimmer 2s infinite',
                    width: `${percentage}%`,
                  }}
                />
              )}
            </div>

            {/* Stats row */}
            <div className="grid grid-cols-4 gap-4 mt-4">
              <MiniStat icon={<Clock className="h-3.5 w-3.5" />} label="Elapsed" value={formatTime(elapsed)} />
              <MiniStat icon={<TrendingUp className="h-3.5 w-3.5" />} label="ETA" value={getEta(graded, total, elapsed)} />
              <MiniStat icon={<Zap className="h-3.5 w-3.5" />} label="Speed" value={`${rate}/min`} />
              <MiniStat icon={<AlertCircle className="h-3.5 w-3.5" />} label="Errors" value={String(failed)} error={failed > 0} />
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Live results feed */}
      {completedStudents.length > 0 && (
        <Card className="border-[var(--border)]">
          <CardContent className="p-0">
            <div className="px-5 py-3 border-b border-[var(--border)] flex items-center justify-between">
              <div className="flex items-center gap-2">
                <Users className="h-4 w-4 text-[var(--text-muted)]" />
                <span className="text-sm font-semibold text-[var(--text-primary)]">
                  Live Results
                </span>
              </div>
              <span className="text-xs text-[var(--text-muted)]">
                {completedStudents.length} processed
              </span>
            </div>
            <div className="max-h-[280px] overflow-y-auto">
              {completedStudents.map((student, i) => (
                <StudentResultRow
                  key={`${student.name}-${student.timestamp}`}
                  student={student}
                  isLatest={i === 0}
                />
              ))}
            </div>
          </CardContent>
        </Card>
      )}

      {/* Shimmer animation */}
      <style jsx global>{`
        @keyframes shimmer {
          0% { transform: translateX(-100%); }
          100% { transform: translateX(200%); }
        }
      `}</style>
    </div>
  )
}

function ConnectionDot({ connected }: { connected: boolean }) {
  if (connected) {
    return (
      <span className="relative flex h-2 w-2">
        <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-emerald-400 opacity-75" />
        <span className="relative inline-flex h-2 w-2 rounded-full bg-emerald-500" />
      </span>
    )
  }
  return <span className="inline-flex h-2 w-2 rounded-full bg-rose-500" />
}

function Stat({ label, value, color, isText }: { label: string; value: string | number; color: string; isText?: boolean }) {
  return (
    <div className="text-center">
      <p className={cn('text-2xl font-bold', isText ? 'text-lg font-mono' : '', color)}>
        {value}
      </p>
      <p className="text-xs text-[var(--text-muted)]">{label}</p>
    </div>
  )
}

function MiniStat({ icon, label, value, error }: { icon: React.ReactNode; label: string; value: string; error?: boolean }) {
  return (
    <div className={cn(
      'flex items-center gap-2 rounded-lg px-3 py-2',
      'bg-[var(--bg-card)] border border-[var(--border)]',
    )}>
      <div className={cn('text-[var(--text-muted)]', error && 'text-rose-400')}>
        {icon}
      </div>
      <div>
        <p className={cn('text-xs text-[var(--text-muted)]', error && 'text-rose-400')}>
          {label}
        </p>
        <p className={cn(
          'text-sm font-semibold tabular-nums text-[var(--text-primary)]',
          error && 'text-rose-400',
        )}>
          {value}
        </p>
      </div>
    </div>
  )
}

function StudentResultRow({ student, isLatest }: { student: GradedStudent; isLatest: boolean }) {
  const isError = student.status === 'error'

  return (
    <div
      className={cn(
        'flex items-center gap-3 px-5 py-2.5 border-b border-[var(--border)] last:border-b-0 transition-colors',
        isLatest && !isError && 'bg-indigo-500/5',
        isLatest && isError && 'bg-rose-500/5',
      )}
    >
      {/* Status icon */}
      <div className="shrink-0">
        {isError ? (
          <XCircle className="h-4 w-4 text-rose-400" />
        ) : (
          <CheckCircle2 className="h-4 w-4 text-emerald-400" />
        )}
      </div>

      {/* Student name */}
      <span className="flex-1 text-sm text-[var(--text-primary)] truncate min-w-0">
        {student.name}
      </span>

      {/* Score + Grade */}
      {!isError && student.score !== null && (
        <div className="flex items-center gap-3 shrink-0">
          <span className="text-sm font-semibold tabular-nums text-[var(--text-primary)]">
            {student.score}
          </span>
          {student.grade && (
            <span className={cn('text-sm font-bold w-6 text-center', getGradeColor(student.grade))}>
              {student.grade}
            </span>
          )}
          {student.confidence && (
            <Badge
              variant={
                student.confidence === 'high' ? 'success' :
                student.confidence === 'medium' ? 'warning' : 'error'
              }
              className="text-[10px] px-1.5 py-0"
            >
              {student.confidence}
            </Badge>
          )}
        </div>
      )}

      {/* Error message */}
      {isError && (
        <span className="text-xs text-rose-400 truncate max-w-[200px]">
          {student.errorMessage}
        </span>
      )}
    </div>
  )
}
