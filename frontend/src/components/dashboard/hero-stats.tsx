'use client'

import type { Session } from '@/lib/types'
import { BarChart3, Users, AlertTriangle, Zap } from 'lucide-react'

interface HeroStatsProps {
  sessions: Session[]
  isLoading?: boolean
  isError?: boolean
}

export function HeroStats({ sessions, isLoading, isError }: HeroStatsProps) {
  if (isError) {
    return <div style={{ textAlign: 'center', color: '#ef4444', padding: '20px 0', fontSize: 13 }}>Unable to load stats</div>
  }

  if (isLoading) {
    return (
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 10 }} data-testid="hero-stats-skeleton">
        {[...Array(4)].map((_, i) => (
          <div key={i} style={{ height: 80, background: 'var(--bg-card)', border: '1px solid var(--border)', borderRadius: 10 }} />
        ))}
      </div>
    )
  }

  const totalSessions = sessions.length
  const activeSessions = sessions.filter(s => s.status === 'grading').length
  const totalStudents = sessions.reduce((acc, s) => acc + s.total_students, 0)
  const totalErrors = sessions.reduce((acc, s) => acc + s.error_count, 0)

  const stats = [
    { label: 'Sessions', value: totalSessions, Icon: BarChart3, color: '#818cf8' },
    { label: 'Active', value: activeSessions, Icon: Zap, color: activeSessions > 0 ? '#22c55e' : '#555' },
    { label: 'Students', value: totalStudents, Icon: Users, color: '#38bdf8' },
    { label: 'Errors', value: totalErrors, Icon: AlertTriangle, color: totalErrors > 0 ? '#ef4444' : '#555' },
  ]

  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 10 }} data-testid="hero-stats">
      {stats.map(({ label, value, Icon, color }) => (
        <div key={label} style={{
          background: 'var(--bg-card)',
          border: '1px solid var(--border)',
          borderRadius: 10,
          padding: '14px 16px',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          gap: 10,
        }}>
          <div>
            <div style={{ fontSize: 10, color: '#666', textTransform: 'uppercase', letterSpacing: '0.08em', marginBottom: 4 }}>
              {label}
            </div>
            <div style={{
              fontSize: 26, fontWeight: 700, color,
              fontFamily: 'var(--font-mono), monospace',
              letterSpacing: '-0.04em', lineHeight: 1,
            }}>
              {value}
            </div>
          </div>
          <Icon style={{ width: 22, height: 22, color, opacity: 0.5, flexShrink: 0 }} />
        </div>
      ))}
    </div>
  )
}
