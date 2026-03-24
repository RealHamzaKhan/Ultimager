'use client'

import { useQuery } from '@tanstack/react-query'
import { AppShell } from '@/components/layout/app-shell'
import { HeroStats } from '@/components/dashboard/hero-stats'
import { SessionGrid } from '@/components/dashboard/session-grid'
import { Button } from '@/components/ui/button'
import { fetchSessions, deleteSession } from '@/lib/api'
import type { Session } from '@/lib/types'
import Link from 'next/link'
import { Plus } from 'lucide-react'

export default function DashboardPage() {
  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ['sessions'],
    queryFn: fetchSessions,
  })

  const sessions: Session[] = data?.sessions || []

  const handleDelete = async (id: number) => {
    if (!confirm('Are you sure you want to delete this session?')) return
    await deleteSession(id)
    refetch()
  }

  return (
    <AppShell>
      <div className="max-w-7xl mx-auto space-y-8">
        {/* Header */}
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-2xl font-bold text-[var(--text-primary)]">Dashboard</h1>
            <p className="text-sm text-[var(--text-muted)] mt-1">
              Manage your grading sessions
            </p>
          </div>
          <Link href="/sessions/new">
            <Button>
              <Plus className="h-4 w-4" />
              New Session
            </Button>
          </Link>
        </div>

        {/* Stats */}
        <HeroStats sessions={sessions} isLoading={isLoading} isError={isError} />

        {/* Sessions */}
        <div>
          <h2 className="text-lg font-semibold text-[var(--text-primary)] mb-4">Sessions</h2>
          <SessionGrid sessions={sessions} isLoading={isLoading} onDelete={handleDelete} />
        </div>
      </div>
    </AppShell>
  )
}
