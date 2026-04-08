'use client'

import { useState } from 'react'
import { SessionCard } from './session-card'
import { Input } from '@/components/ui/input'
import { Button } from '@/components/ui/button'
import type { Session, SessionStatus } from '@/lib/types'
import { Search } from 'lucide-react'

interface SessionGridProps {
  sessions: Session[]
  isLoading?: boolean
  onDelete?: (id: number) => void
}

const statusFilters: { label: string; value: SessionStatus | 'all' }[] = [
  { label: 'All', value: 'all' },
  { label: 'Active', value: 'grading' },
  { label: 'Complete', value: 'complete' },
  { label: 'Error', value: 'error' },
]

export function SessionGrid({ sessions, isLoading, onDelete }: SessionGridProps) {
  const [search, setSearch] = useState('')
  const [statusFilter, setStatusFilter] = useState<SessionStatus | 'all'>('all')

  const filtered = sessions.filter((s) => {
    if (statusFilter !== 'all' && s.status !== statusFilter) return false
    if (search && !s.title.toLowerCase().includes(search.toLowerCase())) return false
    return true
  })

  if (isLoading) {
    return (
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4" data-testid="session-grid-skeleton">
        {[1, 2, 3].map((i) => (
          <div key={i} className="rounded-xl border border-[var(--border)] bg-[var(--bg-card)] p-6 animate-pulse">
            <div className="h-5 w-32 bg-slate-200 dark:bg-slate-700 rounded mb-3" />
            <div className="h-4 w-48 bg-slate-200 dark:bg-slate-700 rounded" />
          </div>
        ))}
      </div>
    )
  }

  return (
    <div>
      {/* Filters */}
      <div className="flex flex-col sm:flex-row gap-3 mb-6">
        <div className="relative flex-1 max-w-xs">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-[var(--text-muted)]" />
          <input
            type="text"
            placeholder="Search sessions..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            className="w-full rounded-md border border-[var(--border)] bg-[var(--bg-card)] pl-9 pr-3 py-2 text-sm text-[var(--text-primary)] placeholder:text-[var(--text-muted)] focus:outline-none focus:ring-2 focus:ring-indigo-500"
            aria-label="Search sessions"
          />
        </div>
        <div className="flex gap-1">
          {statusFilters.map((f) => (
            <Button
              key={f.value}
              variant={statusFilter === f.value ? 'primary' : 'ghost'}
              size="sm"
              onClick={() => setStatusFilter(f.value)}
            >
              {f.label}
            </Button>
          ))}
        </div>
      </div>

      {/* Grid */}
      {filtered.length === 0 ? (
        <div className="text-center py-12 text-[var(--text-muted)]" data-testid="empty-state">
          {search
            ? `No sessions match "${search}"`
            : sessions.length === 0
              ? 'No sessions yet. Create one to get started!'
              : 'No sessions match the selected filter.'}
        </div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {filtered.map((session) => (
            <SessionCard key={session.id} session={session} onDelete={onDelete} />
          ))}
        </div>
      )}
    </div>
  )
}
