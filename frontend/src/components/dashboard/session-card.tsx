'use client'

import Link from 'next/link'
import { Card } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { formatRelativeTime } from '@/lib/utils'
import type { Session, SessionStatus } from '@/lib/types'
import { Trash2 } from 'lucide-react'

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

interface SessionCardProps {
  session: Session
  onDelete?: (id: number) => void
}

export function SessionCard({ session, onDelete }: SessionCardProps) {
  const progress = session.total_students > 0
    ? Math.round((session.graded_count / session.total_students) * 100)
    : 0

  return (
    <Card className="relative">
      <div className="flex items-start justify-between mb-3">
        <div className="flex-1 min-w-0">
          <h3 className="text-base font-semibold text-[var(--text-primary)] truncate">
            {session.title}
          </h3>
          <p className="text-sm text-[var(--text-muted)] truncate mt-0.5">
            {session.description || 'No description'}
          </p>
        </div>
        <Badge
          variant={statusVariant[session.status]}
          pulse={session.status === 'grading'}
        >
          {session.status}
        </Badge>
      </div>

      {/* Progress bar */}
      {session.total_students > 0 && (
        <div className="mb-3">
          <div className="flex justify-between text-xs text-[var(--text-muted)] mb-1">
            <span>{session.graded_count}/{session.total_students} graded</span>
            <span>{progress}%</span>
          </div>
          <div className="h-1.5 rounded-full bg-slate-200 dark:bg-slate-700">
            <div
              className="h-full rounded-full bg-indigo-500 transition-all"
              style={{ width: `${progress}%` }}
            />
          </div>
        </div>
      )}

      {/* Error count */}
      {session.error_count > 0 && (
        <Badge variant="error" className="mb-3">
          {session.error_count} error{session.error_count !== 1 ? 's' : ''}
        </Badge>
      )}

      {/* Footer */}
      <div className="flex items-center justify-between pt-3 border-t border-[var(--border)]">
        <span className="text-xs text-[var(--text-muted)]">
          {formatRelativeTime(session.created_at)}
        </span>
        <div className="flex items-center gap-2">
          {onDelete && (
            <Button
              variant="ghost"
              size="sm"
              onClick={(e) => {
                e.stopPropagation()
                onDelete(session.id)
              }}
              aria-label="Delete session"
            >
              <Trash2 className="h-3.5 w-3.5 text-rose-500" />
            </Button>
          )}
          <Link href={`/sessions/${session.id}`}>
            <Button variant="outline" size="sm">
              View
            </Button>
          </Link>
        </div>
      </div>
    </Card>
  )
}
