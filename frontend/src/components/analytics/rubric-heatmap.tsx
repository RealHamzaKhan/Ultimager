'use client'

import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card'
import { cn } from '@/lib/utils'
import type { RubricHeatmapRow } from '@/lib/types'

interface RubricHeatmapProps {
  rows: RubricHeatmapRow[]
}

function attainmentColor(attainment: number): string {
  if (attainment >= 0.8) return 'bg-emerald-500/20 text-emerald-400'
  if (attainment >= 0.6) return 'bg-amber-500/20 text-amber-400'
  return 'bg-rose-500/20 text-rose-400'
}

export function RubricHeatmap({ rows }: RubricHeatmapProps) {
  if (rows.length === 0) {
    return null
  }

  return (
    <Card data-testid="rubric-heatmap">
      <CardHeader>
        <CardTitle>Rubric Attainment</CardTitle>
      </CardHeader>
      <CardContent>
        <div className="overflow-x-auto">
          <table className="w-full text-sm">
            <thead>
              <tr className="border-b border-[var(--border)] text-left text-xs uppercase tracking-wide text-[var(--text-secondary)]">
                <th className="pb-2 pr-4 font-medium">Criterion</th>
                <th className="pb-2 pr-4 font-medium text-right">Max</th>
                <th className="pb-2 pr-4 font-medium text-right">Avg</th>
                <th className="pb-2 font-medium text-right">Attainment</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row, i) => (
                <tr
                  key={row.criterion}
                  data-testid={`heatmap-row-${i}`}
                  className="border-b border-[var(--border)] last:border-0"
                >
                  <td className="py-2.5 pr-4 font-medium text-[var(--text-primary)]">
                    {row.criterion}
                  </td>
                  <td className="py-2.5 pr-4 text-right text-[var(--text-secondary)]">
                    {row.max}
                  </td>
                  <td className="py-2.5 pr-4 text-right text-[var(--text-secondary)]">
                    {Math.round(row.average * 10) / 10}
                  </td>
                  <td className="py-2.5 text-right">
                    <span
                      className={cn(
                        'inline-block rounded-md px-2 py-0.5 text-xs font-semibold',
                        attainmentColor(row.attainment)
                      )}
                    >
                      {Math.round(row.attainment * 100)}%
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </CardContent>
    </Card>
  )
}
