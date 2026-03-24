'use client'

import { Card } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { formatScore } from '@/lib/utils'
import type { Session } from '@/lib/types'
import { BarChart3, Users, AlertTriangle, CheckCircle } from 'lucide-react'

interface HeroStatsProps {
  sessions: Session[]
  isLoading?: boolean
  isError?: boolean
}

export function HeroStats({ sessions, isLoading, isError }: HeroStatsProps) {
  if (isError) {
    return <div className="text-center py-8 text-rose-500">Unable to load stats</div>
  }

  if (isLoading) {
    return (
      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4" data-testid="hero-stats-skeleton">
        {[1, 2, 3, 4].map((i) => (
          <Card key={i} className="animate-pulse">
            <div className="h-4 w-20 bg-slate-200 dark:bg-slate-700 rounded mb-2" />
            <div className="h-8 w-16 bg-slate-200 dark:bg-slate-700 rounded" />
          </Card>
        ))}
      </div>
    )
  }

  const totalSessions = sessions.length
  const activeSessions = sessions.filter((s) => s.status === 'grading').length
  const totalStudents = sessions.reduce((acc, s) => acc + s.total_students, 0)
  const totalErrors = sessions.reduce((acc, s) => acc + s.error_count, 0)

  const stats = [
    { label: 'Total Sessions', value: String(totalSessions), icon: BarChart3, color: 'text-indigo-500' },
    {
      label: 'Active Sessions',
      value: String(activeSessions),
      icon: CheckCircle,
      color: 'text-emerald-500',
      pulse: activeSessions > 0,
    },
    { label: 'Total Students', value: String(totalStudents), icon: Users, color: 'text-sky-500' },
    { label: 'Errors', value: String(totalErrors), icon: AlertTriangle, color: totalErrors > 0 ? 'text-rose-500' : 'text-slate-500' },
  ]

  return (
    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4" data-testid="hero-stats">
      {stats.map((stat) => (
        <Card key={stat.label}>
          <div className="flex items-center justify-between">
            <div>
              <p className="text-sm text-[var(--text-muted)]">{stat.label}</p>
              <p className={`text-2xl font-bold ${stat.color}`}>
                {stat.value}
              </p>
            </div>
            <stat.icon className={`h-8 w-8 ${stat.color} opacity-60 ${stat.pulse ? 'animate-pulse' : ''}`} />
          </div>
          {stat.pulse && (
            <Badge variant="success" pulse className="mt-2">
              Live
            </Badge>
          )}
        </Card>
      ))}
    </div>
  )
}
