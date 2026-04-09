'use client'

import { useState, useCallback } from 'react'
import { Button } from '@/components/ui/button'
import { Badge } from '@/components/ui/badge'
import { cn } from '@/lib/utils'
import type { RubricCriteria, ExtractedQuestion } from '@/lib/types'
import { Plus, Trash2, ChevronDown, GripVertical } from 'lucide-react'

export interface RubricCriteriaWithId extends RubricCriteria {
  _id: string
}

interface RubricEditorProps {
  criteria: RubricCriteriaWithId[]
  onChange: (criteria: RubricCriteriaWithId[]) => void
  maxScore: number
  questions?: ExtractedQuestion[]
}

let _idCounter = 0
export function makeId(): string {
  return `rc_${Date.now()}_${++_idCounter}`
}

export function toCriteriaWithIds(
  criteria: RubricCriteria[]
): RubricCriteriaWithId[] {
  return criteria.map((c) => ({ ...c, _id: makeId() }))
}

export function criteriaToRubricText(criteria: RubricCriteriaWithId[]): string {
  return criteria.map((c) => `${c.criterion}: ${c.max}`).join('\n')
}

function getLeafQuestions(questions: ExtractedQuestion[]): ExtractedQuestion[] {
  const leaves: ExtractedQuestion[] = []
  for (const q of questions) {
    if (q.parts && q.parts.length > 0) {
      leaves.push(...getLeafQuestions(q.parts))
    } else {
      leaves.push(q)
    }
  }
  return leaves
}

export function RubricEditor({ criteria, onChange, maxScore, questions }: RubricEditorProps) {
  const [expandedIds, setExpandedIds] = useState<Set<string>>(new Set())

  const totalPoints = criteria.reduce((sum, c) => sum + (c.max || 0), 0)
  const pointsDiff = totalPoints - maxScore
  const pointsStatus: 'success' | 'warning' | 'error' =
    pointsDiff === 0 ? 'success' : Math.abs(pointsDiff) <= maxScore * 0.05 ? 'warning' : 'error'

  const updateCriterion = useCallback(
    (id: string, updates: Partial<RubricCriteriaWithId>) => {
      onChange(criteria.map((c) => (c._id === id ? { ...c, ...updates } : c)))
    },
    [criteria, onChange]
  )

  const removeCriterion = useCallback(
    (id: string) => {
      onChange(criteria.filter((c) => c._id !== id))
    },
    [criteria, onChange]
  )

  const addCriterion = useCallback(
    (questionId?: string) => {
      const newCriterion: RubricCriteriaWithId = {
        _id: makeId(),
        criterion: '',
        max: 0,
        description: '',
        question_id: questionId,
      }
      onChange([...criteria, newCriterion])
    },
    [criteria, onChange]
  )

  const toggleExpand = (id: string) => {
    setExpandedIds((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  // Group criteria by question_id if questions are provided
  const hasQuestions = questions && questions.length > 0
  const leafQuestions = hasQuestions ? getLeafQuestions(questions) : []

  const groupedCriteria: { label: string; questionId: string | null; items: RubricCriteriaWithId[]; marks: number | null }[] = []

  if (hasQuestions && leafQuestions.length > 0) {
    const usedIds = new Set<string>()

    for (const q of leafQuestions) {
      const items = criteria.filter((c) => c.question_id === q.id)
      items.forEach((c) => usedIds.add(c._id))
      groupedCriteria.push({
        label: `${q.label} - ${q.description}`,
        questionId: q.id,
        items,
        marks: q.marks,
      })
    }

    // Ungrouped criteria
    const ungrouped = criteria.filter((c) => !usedIds.has(c._id))
    if (ungrouped.length > 0) {
      groupedCriteria.push({ label: 'Other Criteria', questionId: null, items: ungrouped, marks: null })
    }
  } else {
    groupedCriteria.push({ label: '', questionId: null, items: criteria, marks: null })
  }

  return (
    <div className="space-y-3">
      {/* Points summary bar */}
      <div className="flex items-center justify-between rounded-lg border border-[var(--border)] bg-[var(--bg-card)] px-4 py-2.5">
        <span className="text-sm font-medium text-[var(--text-secondary)]">
          Total Points
        </span>
        <div className="flex items-center gap-2">
          <span className={cn(
            'text-lg font-bold tabular-nums',
            pointsStatus === 'success' && 'text-emerald-600 dark:text-emerald-400',
            pointsStatus === 'warning' && 'text-amber-600 dark:text-amber-400',
            pointsStatus === 'error' && 'text-rose-600 dark:text-rose-400',
          )}>
            {totalPoints}
          </span>
          <span className="text-sm text-[var(--text-muted)]">/ {maxScore}</span>
          <Badge variant={pointsStatus}>
            {pointsDiff === 0
              ? 'Exact'
              : pointsDiff > 0
                ? `+${pointsDiff} over`
                : `${Math.abs(pointsDiff)} under`}
          </Badge>
        </div>
      </div>

      {/* Criteria groups */}
      {groupedCriteria.map((group, gi) => (
        <div key={`${group.questionId ?? 'ungrouped'}-${gi}`} className="space-y-2">
          {group.label && (
            <div className="flex items-center justify-between">
              <h4 className="text-xs font-semibold uppercase tracking-wide text-[var(--text-muted)]">
                {group.label}
              </h4>
              {group.marks !== null && (
                <Badge variant="info">{group.marks} marks</Badge>
              )}
            </div>
          )}

          {group.items.map((c) => {
            const isExpanded = expandedIds.has(c._id)
            return (
              <div
                key={c._id}
                className="rounded-lg border border-[var(--border)] bg-[var(--bg-card)] transition-shadow hover:shadow-[var(--shadow-sm)]"
              >
                {/* Compact row */}
                <div className="flex items-center gap-2 px-3 py-2">
                  <GripVertical className="h-3.5 w-3.5 shrink-0 text-[var(--text-muted)]" />

                  <input
                    type="text"
                    value={c.criterion}
                    onChange={(e) => updateCriterion(c._id, { criterion: e.target.value })}
                    placeholder="Criterion name"
                    className="flex-1 min-w-0 rounded border-0 bg-transparent px-1 py-0.5 text-sm font-medium text-[var(--text-primary)] placeholder:text-[var(--text-muted)] focus:outline-none focus:ring-1 focus:ring-indigo-500 focus:ring-offset-0"
                  />

                  <div className="flex items-center gap-1 shrink-0">
                    <input
                      type="number"
                      value={c.max || ''}
                      onChange={(e) =>
                        updateCriterion(c._id, {
                          max: Math.max(0, parseFloat(e.target.value) || 0),
                        })
                      }
                      placeholder="0"
                      min={0}
                      step="any"
                      className="w-16 rounded border border-[var(--border)] bg-[var(--bg-card)] px-2 py-0.5 text-sm text-center tabular-nums text-[var(--text-primary)] focus:outline-none focus:ring-1 focus:ring-indigo-500"
                    />
                    <span className="text-xs text-[var(--text-muted)]">pts</span>
                  </div>

                  <button
                    type="button"
                    onClick={() => toggleExpand(c._id)}
                    className="shrink-0 rounded p-1 text-[var(--text-muted)] hover:bg-[var(--bg-card-hover)] hover:text-[var(--text-secondary)] transition-colors"
                    title={isExpanded ? 'Collapse' : 'Expand details'}
                  >
                    <ChevronDown
                      className={cn(
                        'h-3.5 w-3.5 transition-transform',
                        isExpanded && 'rotate-180'
                      )}
                    />
                  </button>

                  <button
                    type="button"
                    onClick={() => removeCriterion(c._id)}
                    className="shrink-0 rounded p-1 text-[var(--text-muted)] hover:bg-rose-50 hover:text-rose-500 dark:hover:bg-rose-900/20 transition-colors"
                    title="Remove criterion"
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </button>
                </div>

                {/* Expanded description */}
                {isExpanded && (
                  <div className="border-t border-[var(--border)] px-3 py-2">
                    <label className="block text-xs font-medium text-[var(--text-muted)] mb-1">
                      Grading Description
                    </label>
                    <textarea
                      value={c.description || ''}
                      onChange={(e) =>
                        updateCriterion(c._id, { description: e.target.value })
                      }
                      placeholder="Describe what earns full, partial, and zero credit..."
                      rows={3}
                      className="w-full rounded border border-[var(--border)] bg-[var(--bg-card)] px-2 py-1.5 text-sm text-[var(--text-primary)] placeholder:text-[var(--text-muted)] focus:outline-none focus:ring-1 focus:ring-indigo-500 resize-y"
                    />
                  </div>
                )}
              </div>
            )
          })}

          {/* Add criterion button per group */}
          {hasQuestions && group.questionId && (
            <button
              type="button"
              onClick={() => addCriterion(group.questionId ?? undefined)}
              className="flex items-center gap-1.5 rounded-md px-2 py-1 text-xs text-[var(--text-muted)] hover:text-indigo-500 hover:bg-indigo-50 dark:hover:bg-indigo-900/20 transition-colors"
            >
              <Plus className="h-3 w-3" />
              Add criterion
            </button>
          )}
        </div>
      ))}

      {/* Global add button */}
      <Button
        type="button"
        variant="outline"
        size="sm"
        onClick={() => addCriterion()}
        className="w-full gap-1.5"
      >
        <Plus className="h-3.5 w-3.5" />
        Add Criterion
      </Button>
    </div>
  )
}
