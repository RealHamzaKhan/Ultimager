'use client'

import { useState, type FormEvent } from 'react'
import { Card, CardContent } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import { useOverrideScore } from '@/hooks/use-mutations'
import { formatScore } from '@/lib/utils'
import {
  Award,
  Save,
  AlertTriangle,
  CheckCircle2,
  XCircle,
  Scale,
  Pencil,
  ArrowRight,
} from 'lucide-react'

interface OverrideFormProps {
  sessionId: number
  studentId: number
  currentScore: number | null
  maxScore: number
  isOverridden: boolean
  overrideScore: number | null
  overrideComments: string
  grade: string
}

export function OverrideForm({
  sessionId,
  studentId,
  currentScore,
  maxScore,
  isOverridden,
  overrideScore,
  overrideComments,
  grade,
}: OverrideFormProps) {
  const [score, setScore] = useState<string>(
    overrideScore != null ? String(overrideScore) : (currentScore != null ? String(currentScore) : '')
  )
  const [comments, setComments] = useState(overrideComments || '')
  const [markReviewed, setMarkReviewed] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const override = useOverrideScore()

  const handleSubmit = (e: FormEvent) => {
    e.preventDefault()
    setError(null)

    const numScore = Number(score)
    if (isNaN(numScore) || numScore < 0) {
      setError('Score must be a positive number')
      return
    }
    if (numScore > maxScore) {
      setError(`Score cannot exceed ${maxScore}`)
      return
    }

    override.mutate({
      sessionId,
      studentId,
      payload: {
        score: numScore,
        comments: comments || undefined,
        is_reviewed: markReviewed,
      },
    })
  }

  return (
    <div className="space-y-5">
      {/* Current Score Summary */}
      <Card>
        <CardContent className="p-5">
          <div className="flex items-center gap-3 mb-4">
            <Scale className="h-4 w-4 text-indigo-400" />
            <h3 className="text-sm font-semibold text-[var(--text-primary)]">Score Summary</h3>
          </div>
          <div className="grid grid-cols-3 gap-3">
            <div className="rounded-lg bg-[var(--bg-page)] border border-[var(--border)] p-3 text-center">
              <p className="text-[10px] text-[var(--text-muted)] uppercase tracking-wider mb-1">AI Score</p>
              <p className="text-xl font-bold tabular-nums text-[var(--text-primary)]">
                {currentScore != null ? formatScore(currentScore) : '\u2014'}
              </p>
              <p className="text-xs text-[var(--text-muted)]">/ {maxScore}</p>
            </div>
            {isOverridden && (
              <>
                <div className="flex items-center justify-center">
                  <ArrowRight className="h-5 w-5 text-[var(--text-muted)]" />
                </div>
                <div className="rounded-lg bg-indigo-500/5 border border-indigo-500/20 p-3 text-center">
                  <p className="text-[10px] text-indigo-400 uppercase tracking-wider mb-1">Override</p>
                  <p className="text-xl font-bold tabular-nums text-indigo-400">
                    {overrideScore != null ? formatScore(overrideScore) : '\u2014'}
                  </p>
                  <p className="text-xs text-indigo-400/60">/ {maxScore}</p>
                </div>
              </>
            )}
            {!isOverridden && (
              <>
                <div className="flex items-center justify-center">
                  <Pencil className="h-5 w-5 text-[var(--text-muted)] opacity-30" />
                </div>
                <div className="rounded-lg bg-[var(--bg-page)] border border-dashed border-[var(--border)] p-3 text-center">
                  <p className="text-[10px] text-[var(--text-muted)] uppercase tracking-wider mb-1">Override</p>
                  <p className="text-xl font-bold tabular-nums text-[var(--text-muted)]">&mdash;</p>
                  <p className="text-xs text-[var(--text-muted)]">Not set</p>
                </div>
              </>
            )}
          </div>
        </CardContent>
      </Card>

      {/* Override Form */}
      <Card>
        <CardContent className="p-5">
          <div className="flex items-center gap-3 mb-4">
            <Award className="h-4 w-4 text-indigo-400" />
            <h3 className="text-sm font-semibold text-[var(--text-primary)]">Manual Score Override</h3>
            {isOverridden && (
              <Badge variant="info" className="text-[10px]">Active</Badge>
            )}
          </div>

          <form onSubmit={handleSubmit} className="space-y-4">
            {/* Score Input */}
            <div>
              <label className="block text-xs font-medium text-[var(--text-muted)] mb-1.5">
                Override Score (0 - {maxScore})
              </label>
              <div className="flex items-center gap-2">
                <input
                  type="number"
                  min={0}
                  max={maxScore}
                  step="any"
                  value={score}
                  onChange={(e) => setScore(e.target.value)}
                  placeholder={`0 - ${maxScore}`}
                  className={cn(
                    'flex-1 rounded-lg border bg-[var(--bg-page)] px-3 py-2 text-sm text-[var(--text-primary)]',
                    'focus:outline-none focus:ring-2 focus:ring-indigo-500/30 focus:border-indigo-500/50',
                    'placeholder:text-[var(--text-muted)]/50',
                    'border-[var(--border)]'
                  )}
                />
                <span className="text-sm text-[var(--text-muted)]">/ {maxScore}</span>
              </div>
            </div>

            {/* Comments */}
            <div>
              <label className="block text-xs font-medium text-[var(--text-muted)] mb-1.5">
                Override Comments (optional)
              </label>
              <textarea
                value={comments}
                onChange={(e) => setComments(e.target.value)}
                placeholder="Reason for score override..."
                rows={3}
                className={cn(
                  'w-full rounded-lg border bg-[var(--bg-page)] px-3 py-2 text-sm text-[var(--text-primary)]',
                  'focus:outline-none focus:ring-2 focus:ring-indigo-500/30 focus:border-indigo-500/50',
                  'placeholder:text-[var(--text-muted)]/50 resize-none',
                  'border-[var(--border)]'
                )}
              />
            </div>

            {/* Mark Reviewed Checkbox */}
            <label className="flex items-center gap-2 cursor-pointer">
              <input
                type="checkbox"
                checked={markReviewed}
                onChange={(e) => setMarkReviewed(e.target.checked)}
                className="rounded border-[var(--border)] text-indigo-500 focus:ring-indigo-500/30"
              />
              <span className="text-sm text-[var(--text-secondary)]">
                Mark as reviewed by instructor
              </span>
            </label>

            {/* Error */}
            {(error || override.isError) && (
              <div className="flex items-center gap-2 text-sm text-rose-400">
                <XCircle className="h-4 w-4 shrink-0" />
                <span>{error || 'Failed to save override'}</span>
              </div>
            )}

            {/* Success */}
            {override.isSuccess && (
              <div className="flex items-center gap-2 text-sm text-emerald-400">
                <CheckCircle2 className="h-4 w-4 shrink-0" />
                <span>Override saved successfully</span>
              </div>
            )}

            <Button
              type="submit"
              variant="primary"
              disabled={override.isPending || !score}
              className="w-full gap-2"
            >
              <Save className="h-4 w-4" />
              {override.isPending ? 'Saving...' : 'Save Override'}
            </Button>
          </form>
        </CardContent>
      </Card>
    </div>
  )
}
