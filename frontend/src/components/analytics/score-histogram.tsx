'use client'

import { useMemo } from 'react'
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card'

interface ScoreHistogramProps {
  scores: number[]
  maxScore: number
}

const BIN_LABELS = [
  '0-10%',
  '10-20%',
  '20-30%',
  '30-40%',
  '40-50%',
  '50-60%',
  '60-70%',
  '70-80%',
  '80-90%',
  '90-100%',
]

export function ScoreHistogram({ scores, maxScore }: ScoreHistogramProps) {
  const bins = useMemo(() => {
    const counts = new Array(10).fill(0) as number[]

    for (const score of scores) {
      const pct = maxScore > 0 ? (score / maxScore) * 100 : 0
      // Clamp index to 0-9
      const idx = Math.min(Math.floor(pct / 10), 9)
      counts[idx]++
    }

    return counts
  }, [scores, maxScore])

  const maxCount = Math.max(...bins, 1)

  return (
    <Card data-testid="score-histogram">
      <CardHeader>
        <CardTitle>Score Distribution</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="flex items-end gap-1.5" style={{ height: '12rem' }}>
          {bins.map((count, i) => {
            const heightPct = (count / maxCount) * 100
            return (
              <div
                key={i}
                data-testid={`histogram-bar-${i}`}
                className="flex flex-1 flex-col items-center gap-1"
                style={{ height: '100%' }}
              >
                {/* Count label */}
                <span className="text-[10px] font-medium text-[var(--text-secondary)]">
                  {count > 0 ? count : ''}
                </span>

                {/* Spacer to push bar to bottom */}
                <div className="flex-1" />

                {/* Bar */}
                <div
                  className="w-full rounded-t bg-indigo-500 transition-all duration-300"
                  style={{
                    height: `${heightPct}%`,
                    minHeight: count > 0 ? '4px' : '0px',
                  }}
                />

                {/* Bin label */}
                <span className="mt-1 text-[9px] text-[var(--text-secondary)] whitespace-nowrap">
                  {BIN_LABELS[i]}
                </span>
              </div>
            )
          })}
        </div>
      </CardContent>
    </Card>
  )
}
