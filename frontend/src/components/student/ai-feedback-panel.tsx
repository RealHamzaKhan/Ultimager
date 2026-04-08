'use client'

import { Card, CardContent } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { cn } from '@/lib/utils'
import type { Submission, CriterionScore, CheckpointResult } from '@/lib/types'
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
  const aiResult = submission.ai_result
  const isCheckpointGrading = aiResult?.grading_method === 'checkpoint' || aiResult?.grading_method === 'multi_agent'
  const isMultiAgent = aiResult?.grading_method === 'multi_agent'
  const checkpointStats = aiResult?.checkpoint_stats
  const verificationRate = aiResult?.verification_rate

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

      {/* Submission-level Flags — only show when there are serious flags */}
      {aiResult?.flags && aiResult.flags.length > 0 && (
        <Card className="border-amber-500/20">
          <CardContent className="p-5">
            <div className="flex items-start gap-3">
              <div className="shrink-0 rounded-lg bg-amber-500/10 p-2">
                <AlertTriangle className="h-4 w-4 text-amber-400" />
              </div>
              <div className="flex-1">
                <h3 className="text-sm font-semibold text-amber-400 mb-2">
                  Items Needing Review ({aiResult.flags.length})
                </h3>
                <ul className="space-y-1">
                  {aiResult.flags.map((flag: string, i: number) => (
                    <li key={i} className="text-xs text-amber-300/80 flex items-start gap-1.5">
                      <span className="shrink-0 mt-0.5">•</span>
                      <span>{flag}</span>
                    </li>
                  ))}
                </ul>
              </div>
            </div>
          </CardContent>
        </Card>
      )}

      {/* System-level transparency banners */}
      {submission.routing_fallback_used && (
        <div className="flex items-start gap-2 rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-sm text-amber-400">
          <AlertTriangle className="h-4 w-4 shrink-0 mt-0.5" />
          <span>
            <strong>File routing partial failure</strong> — some files may not have been matched
            to the correct criteria. Scores may be less accurate. Manual review recommended.
          </span>
        </div>
      )}
      {submission.judge_truncated && (
        <div className="flex items-start gap-2 rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-sm text-amber-400">
          <AlertTriangle className="h-4 w-4 shrink-0 mt-0.5" />
          <span>
            <strong>Large submission truncated</strong> — content exceeded the AI context limit (28K chars).
            Some submitted work was not seen by the AI grader. Manual review of large submissions recommended.
          </span>
        </div>
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
                {isCheckpointGrading && (
                  <Badge variant="default" className="text-[10px] bg-indigo-500/10 text-indigo-400 border-indigo-500/20">
                    Checkpoint Grading
                  </Badge>
                )}
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

            {/* Checkpoint Stats Banner (shown when checkpoint grading was used) */}
            {isCheckpointGrading && checkpointStats && (

              <div className="px-5 py-2.5 border-b border-[var(--border)] bg-indigo-500/5 flex items-center gap-4 text-xs flex-wrap">
                {/* Grading method badge */}
                <Badge variant="outline" className="text-[10px] px-1.5 py-0 h-4 text-indigo-400 border-indigo-400/40">
                  {isMultiAgent ? 'Multi-Agent' : 'Checkpoint'} Grading
                </Badge>

                <div className="flex items-center gap-1.5">
                  <CheckCircle2 className="h-3.5 w-3.5 text-emerald-400" />
                  <span className="text-[var(--text-secondary)]">
                    <span className="font-semibold text-emerald-400">{checkpointStats.verified ?? checkpointStats.verified}</span>
                    /{checkpointStats.total} verified
                  </span>
                </div>

                {/* Partial credit stat (multi-agent only) */}
                {isMultiAgent && (checkpointStats.partial_credit ?? 0) > 0 && (
                  <div className="flex items-center gap-1.5">
                    <AlertTriangle className="h-3.5 w-3.5 text-blue-400" />
                    <span className="text-blue-400">
                      {checkpointStats.partial_credit} partial credit
                    </span>
                  </div>
                )}

                {/* Verification rate */}
                {(() => {
                  const vr = checkpointStats.verification_rate ?? (typeof verificationRate === 'number' ? verificationRate * 100 : null)
                  if (vr == null) return null
                  return (
                    <span className={cn('font-semibold', vr >= 80 ? 'text-emerald-400' : vr >= 60 ? 'text-amber-400' : 'text-rose-400')}>
                      {Math.round(vr)}% verified
                    </span>
                  )
                })()}

                {/* Retried */}
                {(checkpointStats.hallucinated_and_retried ?? checkpointStats.retried ?? 0) > 0 && (
                  <div className="flex items-center gap-1.5">
                    <AlertTriangle className="h-3.5 w-3.5 text-amber-400" />
                    <span className="text-amber-400">
                      {checkpointStats.hallucinated_and_retried ?? checkpointStats.retried} retried
                    </span>
                  </div>
                )}

                {/* Flagged */}
                {(checkpointStats.flagged_criteria ?? checkpointStats.flagged ?? 0) > 0 && (
                  <div className="flex items-center gap-1.5">
                    <AlertTriangle className="h-3.5 w-3.5 text-rose-400" />
                    <span className="text-rose-400">
                      {checkpointStats.flagged_criteria ?? checkpointStats.flagged} flagged
                    </span>
                  </div>
                )}
              </div>
            )}

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
                        criterion.not_evaluated ? 'bg-amber-500/10 text-amber-400'
                        : pct >= 0.8 ? 'bg-emerald-500/10 text-emerald-400'
                        : pct >= 0.5 ? 'bg-amber-500/10 text-amber-400'
                        : 'bg-rose-500/10 text-rose-400'
                      )}>
                        {criterion.score}/{criterion.max}
                        {criterion.score_capped && (
                          <span
                            className="text-[10px] text-blue-400 ml-1 font-normal"
                            title="Checkpoint points exceeded criterion max — score was capped at max"
                          >
                            (capped)
                          </span>
                        )}
                      </div>

                      {/* Criterion name + flag */}
                      <span className="flex-1 text-sm font-medium text-[var(--text-primary)] truncate flex items-center gap-1.5">
                        {criterion.criterion}
                        {criterion.flagged && (
                          <AlertTriangle className="h-3.5 w-3.5 text-amber-400 shrink-0" />
                        )}
                        {criterion.not_evaluated && (
                          <AlertTriangle className="h-3.5 w-3.5 text-amber-400 shrink-0" aria-label="Not evaluated by AI" />
                        )}
                      </span>

                      {/* Progress bar (mini) */}
                      <div className="w-24 shrink-0 hidden sm:block">
                        <div className="h-1.5 rounded-full bg-[var(--border)] overflow-hidden">
                          <div
                            className={cn(
                              'h-full rounded-full transition-all',
                              criterion.not_evaluated ? 'bg-amber-400/40'
                              : pct >= 0.8 ? 'bg-emerald-500'
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

                    {/* Expanded: checkpoints + justification */}
                    {isExpanded && (
                      <div className="px-5 pb-3 pl-12 space-y-2">
                        {/* Not-evaluated banner */}
                        {criterion.not_evaluated && (
                          <div className="flex items-center gap-1.5 text-[11px] text-amber-400 py-1">
                            <AlertTriangle className="h-3 w-3 shrink-0" />
                            <span>Not evaluated by AI — manual grading required for this criterion</span>
                          </div>
                        )}

                        {/* Checkpoints (if available) */}
                        {criterion.checkpoints && criterion.checkpoints.length > 0 && (
                          <div className="rounded-lg bg-[var(--bg-card)] border border-[var(--border)] p-3">
                            <p className="text-xs font-medium text-[var(--text-muted)] mb-2 uppercase tracking-wider">
                              Checkpoints
                            </p>
                            <div className="space-y-1.5">
                              {criterion.checkpoints.map((cp) => {
                                // Score percent determines display (supports partial credit)
                                const scorePct = cp.score_percent ?? (cp.pass ? 100 : 0)
                                const isPartial = scorePct > 0 && scorePct < 100
                                const isFull = scorePct === 100
                                const isNone = scorePct === 0

                                // Confidence / verification tier for border color
                                const tier = cp.evidence_tier || (
                                  isFull && cp.verified ? 'green' :
                                  isFull && !cp.verified ? 'yellow' :
                                  isPartial ? 'partial' :
                                  cp.needs_review ? 'orange' : 'none'
                                )
                                const tierColors: Record<string, { icon: string; text: string; bg: string; border: string }> = {
                                  green:   { icon: 'text-emerald-400', text: 'text-emerald-400', bg: 'bg-emerald-500/5',  border: 'border-emerald-500/20' },
                                  yellow:  { icon: 'text-amber-400',   text: 'text-amber-400',   bg: 'bg-amber-500/5',    border: 'border-amber-500/20' },
                                  partial: { icon: 'text-blue-400',    text: 'text-blue-400',    bg: 'bg-blue-500/5',     border: 'border-blue-500/20' },
                                  orange:  { icon: 'text-orange-400',  text: 'text-orange-400',  bg: 'bg-orange-500/5',   border: 'border-orange-500/20' },
                                  none:    { icon: 'text-rose-400',    text: 'text-[var(--text-secondary)]', bg: '', border: 'border-transparent' },
                                }
                                const tc = tierColors[tier] || tierColors.none

                                return (
                                <div key={cp.id || cp.description} className={cn(
                                  "rounded-lg border p-2.5 text-xs space-y-1.5",
                                  tc.bg, tc.border
                                )}>
                                  {/* Header: score badge + description */}
                                  <div className="flex items-start gap-2">
                                    <div className="shrink-0 mt-0.5">
                                      {isFull  ? <CheckCircle2 className={cn("h-3.5 w-3.5", tc.icon)} /> :
                                       isPartial ? <AlertTriangle className={cn("h-3.5 w-3.5", tc.icon)} /> :
                                                   <XCircle className="h-3.5 w-3.5 text-rose-400" />}
                                    </div>
                                    <div className="flex-1 min-w-0 space-y-0.5">
                                      <div className="flex items-center gap-1.5 flex-wrap">
                                        <span className={cn('font-semibold tabular-nums', tc.text)}>
                                          {cp.points_awarded ?? cp.points_awarded}/{cp.points} pts
                                        </span>
                                        {isPartial && (
                                          <Badge variant="outline" className="text-[10px] px-1 py-0 h-4 text-blue-400 border-blue-400/40">
                                            {scorePct}% partial
                                          </Badge>
                                        )}
                                        {cp.confidence === 'low' && (
                                          <Badge variant="outline" className="text-[10px] px-1 py-0 h-4 text-orange-400 border-orange-400/40">
                                            low confidence
                                          </Badge>
                                        )}
                                        {cp.needs_review && (
                                          <Badge variant="outline" className="text-[10px] px-1 py-0 h-4 text-amber-400 border-amber-400/40">
                                            review
                                          </Badge>
                                        )}
                                      </div>
                                      <p className="text-[var(--text-secondary)] font-medium">{cp.description}</p>
                                    </div>
                                  </div>

                                  {/* Professor reasoning — always shown, prominent */}
                                  {cp.reasoning && (
                                    <div className="pl-5 space-y-1">
                                      <p className="text-[10px] font-medium text-[var(--text-muted)] uppercase tracking-wider">
                                        Professor&apos;s reasoning
                                      </p>
                                      <p className="text-[var(--text-secondary)] leading-relaxed">
                                        {cp.reasoning}
                                      </p>
                                    </div>
                                  )}

                                  {/* Evidence quote */}
                                  {cp.evidence_quote && cp.evidence_quote !== 'No relevant content found.' && (
                                    <div className="pl-5">
                                      <p className="text-[var(--text-muted)] italic text-[11px]" title={cp.evidence_quote}>
                                        &ldquo;{cp.evidence_quote.slice(0, 180)}{cp.evidence_quote.length > 180 ? '…' : ''}&rdquo;
                                        {cp.source_file && cp.source_file !== 'unknown' && (
                                          <span className="not-italic ml-1 opacity-50">— {cp.source_file}</span>
                                        )}
                                      </p>
                                    </div>
                                  )}

                                  {/* Unverified evidence with awarded marks */}
                                  {!cp.verified && (cp.points_awarded ?? 0) > 0 && (
                                    <div className="pl-5 flex items-center gap-1.5 text-[10px] text-amber-400">
                                      <AlertTriangle className="h-3 w-3 shrink-0" />
                                      <span>
                                        Evidence unverified — {cp.points_awarded}/{cp.points} pts awarded on AI judgment alone
                                      </span>
                                    </div>
                                  )}
                                </div>
                                )
                              })}
                            </div>
                          </div>
                        )}

                        {/* Flag reasons — only show for serious concerns */}
                        {criterion.flag_reasons && criterion.flag_reasons.filter((r: string) =>
                          r.includes('could not be verified') || r.includes('not evaluated')
                        ).length > 0 && (
                          <div className="rounded-lg bg-amber-500/5 border border-amber-500/20 p-3">
                            <div className="flex items-center gap-1.5 mb-1.5">
                              <AlertTriangle className="h-3.5 w-3.5 text-amber-400" />
                              <p className="text-xs font-medium text-amber-400 uppercase tracking-wider">
                                Needs Review
                              </p>
                            </div>
                            <ul className="space-y-0.5">
                              {criterion.flag_reasons.filter((r: string) =>
                                r.includes('could not be verified') || r.includes('not evaluated')
                              ).map((reason: string, ri: number) => (
                                <li key={ri} className="text-xs text-amber-300/80">• {reason}</li>
                              ))}
                            </ul>
                          </div>
                        )}

                        {/* Justification (shown as fallback when no checkpoints) */}
                        {criterion.justification && (!criterion.checkpoints || criterion.checkpoints.length === 0) && (
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
                        )}
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
