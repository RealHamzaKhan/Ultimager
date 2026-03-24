'use client'

import { Card, CardContent } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { cn } from '@/lib/utils'
import type { Submission, CriterionScore } from '@/lib/types'
import { useState } from 'react'
import {
  CheckCircle2,
  XCircle,
  AlertTriangle,
  ChevronDown,
  ChevronRight,
  Sparkles,
  ThumbsUp,
  ThumbsDown,
  Lightbulb,
  MessageSquare,
  Target,
  Zap,
} from 'lucide-react'

interface AIFeedbackPanelProps {
  submission: Submission
  rubricBreakdown: CriterionScore[]
  strengths: string[]
  weaknesses: string[]
  feedback: string
  suggestions: string
  criticalErrors: string[]
  maxScore: number
}

export function AIFeedbackPanel({
  submission,
  rubricBreakdown,
  strengths,
  weaknesses,
  feedback,
  suggestions,
  criticalErrors,
  maxScore,
}: AIFeedbackPanelProps) {
  const [expandedCriteria, setExpandedCriteria] = useState<Set<number>>(new Set())

  if (submission.status !== 'graded' && submission.status !== 'error') {
    return (
      <Card>
        <CardContent className="py-12 text-center">
          <Sparkles className="h-10 w-10 text-[var(--text-muted)] mx-auto mb-3 opacity-40" />
          <p className="text-[var(--text-muted)] text-sm">
            AI analysis will appear here after grading is complete.
          </p>
        </CardContent>
      </Card>
    )
  }

  if (!rubricBreakdown.length && !feedback) {
    return (
      <Card>
        <CardContent className="py-12 text-center">
          <AlertTriangle className="h-10 w-10 text-amber-400 mx-auto mb-3 opacity-60" />
          <p className="text-[var(--text-muted)] text-sm">
            No AI analysis data available for this student.
          </p>
        </CardContent>
      </Card>
    )
  }

  const totalEarned = rubricBreakdown.reduce((sum, c) => sum + c.score, 0)
  const totalPossible = rubricBreakdown.reduce((sum, c) => sum + c.max, 0)

  const toggleCriterion = (index: number) => {
    setExpandedCriteria((prev) => {
      const next = new Set(prev)
      if (next.has(index)) next.delete(index)
      else next.add(index)
      return next
    })
  }

  return (
    <div className="space-y-5">
      {/* Overall Feedback */}
      {feedback && (
        <Card>
          <CardContent className="p-5">
            <div className="flex items-start gap-3">
              <div className="shrink-0 rounded-lg bg-indigo-500/10 p-2">
                <MessageSquare className="h-4 w-4 text-indigo-400" />
              </div>
              <div className="flex-1 min-w-0">
                <h3 className="text-sm font-semibold text-[var(--text-primary)] mb-2">
                  Overall Assessment
                </h3>
                <p className="text-sm text-[var(--text-secondary)] leading-relaxed whitespace-pre-wrap">
                  {feedback}
                </p>
              </div>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Rubric Breakdown */}
      {rubricBreakdown.length > 0 && (
        <Card>
          <CardContent className="p-0">
            <div className="px-5 py-3 border-b border-[var(--border)] flex items-center justify-between">
              <div className="flex items-center gap-2">
                <Target className="h-4 w-4 text-indigo-400" />
                <h3 className="text-sm font-semibold text-[var(--text-primary)]">
                  Rubric Breakdown
                </h3>
              </div>
              <div className="flex items-center gap-2">
                <span className="text-xs text-[var(--text-muted)]">
                  {totalEarned} / {totalPossible} pts
                </span>
                <Badge
                  variant={
                    totalPossible > 0 && (totalEarned / totalPossible) >= 0.8 ? 'success'
                    : totalPossible > 0 && (totalEarned / totalPossible) >= 0.5 ? 'warning'
                    : 'error'
                  }
                  className="text-[10px]"
                >
                  {totalPossible > 0 ? Math.round((totalEarned / totalPossible) * 100) : 0}%
                </Badge>
              </div>
            </div>

            {/* Score Summary Bar */}
            <div className="px-5 py-3 border-b border-[var(--border)]">
              <div className="flex gap-1 h-3 rounded-full overflow-hidden bg-[var(--border)]">
                {rubricBreakdown.map((criterion, i) => {
                  const pct = criterion.max > 0 ? (criterion.score / criterion.max) : 0
                  const width = totalPossible > 0 ? (criterion.max / totalPossible) * 100 : 0
                  return (
                    <div
                      key={i}
                      className={cn(
                        'h-full transition-all rounded-sm',
                        pct >= 0.8 ? 'bg-emerald-500'
                        : pct >= 0.5 ? 'bg-amber-500'
                        : 'bg-rose-500'
                      )}
                      style={{ width: `${width * pct}%` }}
                      title={`${criterion.criterion}: ${criterion.score}/${criterion.max}`}
                    />
                  )
                })}
              </div>
            </div>

            {/* Individual Criteria */}
            <div className="divide-y divide-[var(--border)]">
              {rubricBreakdown.map((criterion, i) => {
                const pct = criterion.max > 0 ? criterion.score / criterion.max : 0
                const isExpanded = expandedCriteria.has(i)
                return (
                  <div key={i} className="group">
                    <button
                      className="w-full flex items-center gap-3 px-5 py-3 text-left hover:bg-[var(--border)]/30 transition-colors"
                      onClick={() => toggleCriterion(i)}
                    >
                      {/* Expand icon */}
                      <div className="shrink-0 text-[var(--text-muted)]">
                        {isExpanded ? (
                          <ChevronDown className="h-4 w-4" />
                        ) : (
                          <ChevronRight className="h-4 w-4" />
                        )}
                      </div>

                      {/* Score badge */}
                      <div className={cn(
                        'shrink-0 rounded-lg px-2.5 py-1 text-xs font-bold tabular-nums',
                        pct >= 0.8 ? 'bg-emerald-500/10 text-emerald-400'
                        : pct >= 0.5 ? 'bg-amber-500/10 text-amber-400'
                        : 'bg-rose-500/10 text-rose-400'
                      )}>
                        {criterion.score}/{criterion.max}
                      </div>

                      {/* Criterion name */}
                      <span className="flex-1 text-sm font-medium text-[var(--text-primary)] truncate">
                        {criterion.criterion}
                      </span>

                      {/* Progress bar (mini) */}
                      <div className="w-24 shrink-0 hidden sm:block">
                        <div className="h-1.5 rounded-full bg-[var(--border)] overflow-hidden">
                          <div
                            className={cn(
                              'h-full rounded-full transition-all',
                              pct >= 0.8 ? 'bg-emerald-500'
                              : pct >= 0.5 ? 'bg-amber-500'
                              : 'bg-rose-500'
                            )}
                            style={{ width: `${pct * 100}%` }}
                          />
                        </div>
                      </div>

                      {/* Percentage */}
                      <span className={cn(
                        'shrink-0 text-xs font-semibold tabular-nums w-10 text-right',
                        pct >= 0.8 ? 'text-emerald-400'
                        : pct >= 0.5 ? 'text-amber-400'
                        : 'text-rose-400'
                      )}>
                        {Math.round(pct * 100)}%
                      </span>
                    </button>

                    {/* Expanded justification */}
                    {isExpanded && criterion.justification && (
                      <div className="px-5 pb-3 pl-12">
                        <div className="rounded-lg bg-[var(--bg-card)] border border-[var(--border)] p-3">
                          <p className="text-xs font-medium text-[var(--text-muted)] mb-1 uppercase tracking-wider">
                            Justification
                          </p>
                          <p className="text-sm text-[var(--text-secondary)] leading-relaxed whitespace-pre-wrap">
                            {criterion.justification}
                          </p>
                          {criterion.citations && criterion.citations.length > 0 && (
                            <div className="mt-2 flex flex-wrap gap-1">
                              {criterion.citations.map((cite, ci) => (
                                <Badge key={ci} variant="default" className="text-[10px]">
                                  {cite.file}{cite.page ? ` p.${cite.page}` : ''}
                                </Badge>
                              ))}
                            </div>
                          )}
                        </div>
                      </div>
                    )}
                  </div>
                )
              })}
            </div>
          </CardContent>
        </Card>
      )}

      {/* Critical Errors */}
      {criticalErrors.length > 0 && (
        <Card className="border-rose-500/20">
          <CardContent className="p-5">
            <div className="flex items-start gap-3">
              <div className="shrink-0 rounded-lg bg-rose-500/10 p-2">
                <Zap className="h-4 w-4 text-rose-400" />
              </div>
              <div className="flex-1">
                <h3 className="text-sm font-semibold text-rose-400 mb-2">
                  Critical Issues ({criticalErrors.length})
                </h3>
                <ul className="space-y-1.5">
                  {criticalErrors.map((err, i) => (
                    <li key={i} className="flex items-start gap-2 text-sm text-rose-400/90">
                      <XCircle className="h-3.5 w-3.5 shrink-0 mt-0.5" />
                      <span>{err}</span>
                    </li>
                  ))}
                </ul>
              </div>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Strengths & Weaknesses */}
      {(strengths.length > 0 || weaknesses.length > 0) && (
        <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
          {/* Strengths */}
          {strengths.length > 0 && (
            <Card className="border-emerald-500/10">
              <CardContent className="p-5">
                <div className="flex items-center gap-2 mb-3">
                  <div className="rounded-lg bg-emerald-500/10 p-1.5">
                    <ThumbsUp className="h-3.5 w-3.5 text-emerald-400" />
                  </div>
                  <h3 className="text-sm font-semibold text-emerald-400">
                    Strengths ({strengths.length})
                  </h3>
                </div>
                <ul className="space-y-2">
                  {strengths.map((s, i) => (
                    <li key={i} className="flex items-start gap-2">
                      <CheckCircle2 className="h-3.5 w-3.5 shrink-0 text-emerald-400/60 mt-0.5" />
                      <span className="text-sm text-[var(--text-secondary)] leading-relaxed">{s}</span>
                    </li>
                  ))}
                </ul>
              </CardContent>
            </Card>
          )}

          {/* Weaknesses */}
          {weaknesses.length > 0 && (
            <Card className="border-rose-500/10">
              <CardContent className="p-5">
                <div className="flex items-center gap-2 mb-3">
                  <div className="rounded-lg bg-rose-500/10 p-1.5">
                    <ThumbsDown className="h-3.5 w-3.5 text-rose-400" />
                  </div>
                  <h3 className="text-sm font-semibold text-rose-400">
                    Areas for Improvement ({weaknesses.length})
                  </h3>
                </div>
                <ul className="space-y-2">
                  {weaknesses.map((w, i) => (
                    <li key={i} className="flex items-start gap-2">
                      <AlertTriangle className="h-3.5 w-3.5 shrink-0 text-rose-400/60 mt-0.5" />
                      <span className="text-sm text-[var(--text-secondary)] leading-relaxed">{w}</span>
                    </li>
                  ))}
                </ul>
              </CardContent>
            </Card>
          )}
        </div>
      )}

      {/* Suggestions for Improvement */}
      {suggestions && (
        <Card>
          <CardContent className="p-5">
            <div className="flex items-start gap-3">
              <div className="shrink-0 rounded-lg bg-violet-500/10 p-2">
                <Lightbulb className="h-4 w-4 text-violet-400" />
              </div>
              <div className="flex-1">
                <h3 className="text-sm font-semibold text-[var(--text-primary)] mb-2">
                  Suggestions for Improvement
                </h3>
                <p className="text-sm text-[var(--text-secondary)] leading-relaxed whitespace-pre-wrap">
                  {suggestions}
                </p>
              </div>
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  )
}
