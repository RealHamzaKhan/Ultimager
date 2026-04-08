'use client'

import Link from 'next/link'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { formatRelativeTime } from '@/lib/utils'
import type { Session, SessionStatus } from '@/lib/types'
import { Trash2, Users, CheckCircle2, AlertTriangle } from 'lucide-react'

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

function statusDot(status: SessionStatus): string {
  if (status === 'complete' || status === 'completed') return '#22c55e'
  if (status === 'grading') return '#6366f1'
  if (status === 'error' || status === 'completed_with_errors') return '#ef4444'
  return '#333'
}

export function SessionCard({ session, onDelete }: SessionCardProps) {
  const progress = session.total_students > 0
    ? Math.round((session.graded_count / session.total_students) * 100)
    : 0

  const dot = statusDot(session.status)

  return (
    <div style={{
      background: 'var(--bg-card)',
      border: '1px solid var(--border)',
      borderRadius: 12,
      padding: '16px',
      display: 'flex',
      flexDirection: 'column',
      gap: 12,
      cursor: 'pointer',
      transition: 'border-color 0.15s, background 0.15s',
      position: 'relative',
    }}
      onMouseEnter={e => {
        (e.currentTarget as HTMLDivElement).style.borderColor = '#2a2a3a'
        ;(e.currentTarget as HTMLDivElement).style.background = '#0e0e1a'
      }}
      onMouseLeave={e => {
        (e.currentTarget as HTMLDivElement).style.borderColor = 'var(--border)'
        ;(e.currentTarget as HTMLDivElement).style.background = 'var(--bg-card)'
      }}
    >
      {/* Header */}
      <div style={{ display: 'flex', alignItems: 'flex-start', justifyContent: 'space-between', gap: 8 }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 7, marginBottom: 3 }}>
            <div style={{
              width: 7, height: 7, borderRadius: '50%', flexShrink: 0,
              background: dot,
              boxShadow: `0 0 5px ${dot}`,
            }} />
            <h3 style={{
              fontSize: 14, fontWeight: 600, color: '#fff',
              overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
            }}>
              {session.title}
            </h3>
          </div>
          <p style={{
            fontSize: 11, color: '#444',
            overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
          }}>
            {session.description || 'No description'}
          </p>
        </div>
        <Badge variant={statusVariant[session.status]} pulse={session.status === 'grading'}>
          {session.status}
        </Badge>
      </div>

      {/* Stats row */}
      <div style={{ display: 'flex', gap: 12 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
          <Users style={{ width: 12, height: 12, color: '#444' }} />
          <span style={{ fontSize: 11, color: '#555' }}>{session.total_students}</span>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
          <CheckCircle2 style={{ width: 12, height: 12, color: '#22c55e' }} />
          <span style={{ fontSize: 11, color: '#555' }}>{session.graded_count} graded</span>
        </div>
        {session.error_count > 0 && (
          <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
            <AlertTriangle style={{ width: 12, height: 12, color: '#ef4444' }} />
            <span style={{ fontSize: 11, color: '#ef4444' }}>{session.error_count} errors</span>
          </div>
        )}
      </div>

      {/* Progress bar */}
      {session.total_students > 0 && (
        <div>
          <div style={{ height: 3, background: 'var(--border)', borderRadius: 99, overflow: 'hidden' }}>
            <div style={{
              height: '100%', borderRadius: 99,
              background: progress === 100 ? '#22c55e' : '#6366f1',
              width: `${progress}%`,
              transition: 'width 0.5s ease',
            }} />
          </div>
          <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 4 }}>
            <span style={{ fontSize: 10, color: '#333' }}>{formatRelativeTime(session.created_at)}</span>
            <span style={{ fontSize: 10, color: '#333', fontFamily: 'var(--font-mono), monospace' }}>{progress}%</span>
          </div>
        </div>
      )}

      {/* Actions */}
      <div style={{
        display: 'flex', alignItems: 'center', justifyContent: 'flex-end',
        gap: 6, paddingTop: 4, borderTop: '1px solid var(--border)',
      }}>
        {onDelete && (
          <Button
            variant="ghost" size="sm"
            onClick={(e) => { e.stopPropagation(); e.preventDefault(); onDelete(session.id) }}
            aria-label="Delete session"
          >
            <Trash2 style={{ width: 13, height: 13, color: '#ef4444' }} />
          </Button>
        )}
        <Link href={`/sessions/${session.id}`}>
          <button style={{
            background: '#141420',
            border: '1px solid var(--border-muted)',
            borderRadius: 6,
            padding: '5px 12px',
            fontSize: 11,
            color: '#888',
            cursor: 'pointer',
            fontWeight: 500,
          }}>
            Open →
          </button>
        </Link>
      </div>
    </div>
  )
}
