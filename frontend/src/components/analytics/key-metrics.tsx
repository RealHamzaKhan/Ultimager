'use client'

import { Card, CardContent } from '@/components/ui/card'
import { cn, formatScore, formatPercent } from '@/lib/utils'
import type { AnalyticsData } from '@/lib/types'

interface KeyMetricsProps {
  data: AnalyticsData | null
  isLoading: boolean
}

function SkeletonCard() {
  return (
    <Card className="animate-pulse">
      <CardContent>
        <div className="h-3 w-20 rounded bg-[var(--bg-skeleton,#334155)] mb-3" />
        <div className="h-8 w-16 rounded bg-[var(--bg-skeleton,#334155)]" />
      </CardContent>
    </Card>
  )
}

interface MetricCardProps {
  label: string
  value: string
  testId: string
  accent?: string
}

function MetricCard({ label, value, testId, accent }: MetricCardProps) {
  return (
    <Card data-testid={testId}>
      <CardContent>
        <p className="text-xs font-medium uppercase tracking-wide text-[var(--text-secondary)]">
          {label}
        </p>
        <p
          className={cn(
            'mt-1 text-2xl font-bold',
            accent || 'text-[var(--text-primary)]'
          )}
        >
          {value}
        </p>
      </CardContent>
    </Card>
  )
}

export function KeyMetrics({ data, isLoading }: KeyMetricsProps) {
  if (isLoading) {
    return (
      <div data-testid="key-metrics" className="grid grid-cols-2 gap-4 sm:grid-cols-4">
        <SkeletonCard />
        <SkeletonCard />
        <SkeletonCard />
        <SkeletonCard />
      </div>
    )
  }

  return (
    <div data-testid="key-metrics" className="grid grid-cols-2 gap-4 sm:grid-cols-4">
      <MetricCard
        testId="metric-average"
        label="Average Score"
        value={formatScore(data?.average_score)}
      />
      <MetricCard
        testId="metric-median"
        label="Median Score"
        value={formatScore(data?.median_score)}
      />
      <MetricCard
        testId="metric-pass-rate"
        label="Pass Rate"
        value={formatPercent(data?.pass_rate)}
      />
      <MetricCard
        testId="metric-flagged"
        label="Flagged"
        value={data ? String(data.flagged_count) : '\u2014'}
        accent={data && data.flagged_count > 0 ? 'text-[var(--color-warning)]' : undefined}
      />
    </div>
  )
}
