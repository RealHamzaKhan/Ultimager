'use client'

import { useState, useCallback } from 'react'
import { useRouter } from 'next/navigation'
import { AppShell } from '@/components/layout/app-shell'
import { Card, CardHeader, CardTitle, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { createSession, generateRubric } from '@/lib/api'
import { cn } from '@/lib/utils'
import {
  RubricEditor,
  toCriteriaWithIds,
  criteriaToRubricText,
  type RubricCriteriaWithId,
} from '@/components/rubric-editor'
import type { ExtractedQuestion } from '@/lib/types'
import { Sparkles, Loader2, ChevronDown, FileText, List } from 'lucide-react'

export default function NewSessionPage() {
  const router = useRouter()
  const [title, setTitle] = useState('')
  const [description, setDescription] = useState('')
  const [rubric, setRubric] = useState('')           // JSON or plain text (sent to backend)
  const [rubricDisplay, setRubricDisplay] = useState('') // Human-readable (shown in textarea)
  const [maxScore, setMaxScore] = useState('100')
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [isGenerating, setIsGenerating] = useState(false)
  const [generationError, setGenerationError] = useState<string | null>(null)
  const [strictness, setStrictness] = useState<'balanced' | 'strict' | 'lenient'>('balanced')
  const [detailLevel, setDetailLevel] = useState<'simple' | 'balanced' | 'detailed'>('balanced')
  const [referenceSolution, setReferenceSolution] = useState('')
  const [editorMode, setEditorMode] = useState<'text' | 'structured'>('text')
  const [criteria, setCriteria] = useState<RubricCriteriaWithId[]>([])
  const [extractedQuestions, setExtractedQuestions] = useState<ExtractedQuestion[]>([])

  const handleCriteriaChange = useCallback((updated: RubricCriteriaWithId[]) => {
    setCriteria(updated)
    const text = criteriaToRubricText(updated)
    setRubric(text)
    setRubricDisplay(text)
  }, [])

  const validate = () => {
    const errs: Record<string, string> = {}
    if (!title.trim()) errs.title = 'Title is required'
    if (title.length > 200) errs.title = 'Title too long'
    if (!(rubricDisplay || rubric).trim()) errs.rubric = 'Rubric is required'
    const score = Number(maxScore)
    if (isNaN(score) || score <= 0) errs.maxScore = 'Must be a positive number'
    setErrors(errs)
    return Object.keys(errs).length === 0
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!validate()) return

    setIsSubmitting(true)
    try {
      const session = await createSession({
        title: title.trim(),
        description: description.trim(),
        rubric: rubric.trim(),
        max_score: Number(maxScore),
        ...(referenceSolution.trim() && { reference_solution: referenceSolution.trim() }),
      })
      router.push(session.id ? `/sessions/${session.id}` : '/')
    } catch {
      setErrors({ form: 'Failed to create session. Please try again.' })
    } finally {
      setIsSubmitting(false)
    }
  }

  const handleGenerateRubric = async () => {
    if (!description.trim()) return
    setIsGenerating(true)
    setGenerationError(null)
    try {
      const result = await generateRubric(description.trim(), Number(maxScore) || 100, strictness, detailLevel)
      if (result.success && result.rubric_text) {
        setRubric(result.rubric_text)
        setRubricDisplay(result.rubric_display || result.rubric_text)

        // Populate structured editor
        if (result.criteria && result.criteria.length > 0) {
          setCriteria(toCriteriaWithIds(result.criteria))
          setExtractedQuestions(result.questions || [])
          setEditorMode('structured')
        }

        if (result.quality_warnings?.includes('ai_generation_failed')) {
          setGenerationError('AI provider unavailable — a basic rubric was generated. You may want to refine it.')
        }
      } else {
        setGenerationError('AI could not generate a rubric. Please write one manually.')
      }
    } catch {
      setGenerationError('Failed to generate rubric. Check your connection and try again.')
    } finally {
      setIsGenerating(false)
    }
  }

  const switchToTextMode = () => {
    // Sync current criteria to text before switching
    if (criteria.length > 0) {
      const text = criteriaToRubricText(criteria)
      setRubric(text)
      setRubricDisplay(text)
    }
    setEditorMode('text')
  }

  const switchToStructuredMode = () => {
    // If we have criteria from AI generation, just switch back
    if (criteria.length > 0) {
      setEditorMode('structured')
    }
  }

  const presets = [10, 25, 50, 100]

  return (
    <AppShell>
      <div className="max-w-2xl mx-auto">
        <h1 className="text-2xl font-bold text-[var(--text-primary)] mb-6">
          Create New Session
        </h1>

        <form onSubmit={handleSubmit} className="space-y-6">
          <Card>
            <CardHeader>
              <CardTitle>Session Details</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <Input
                id="title"
                label="Title"
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                placeholder="e.g., Lab 8 - Binary Search Trees"
                error={errors.title}
              />
              <div className="space-y-1">
                <label htmlFor="description" className="block text-sm font-medium text-[var(--text-secondary)]">
                  Description
                </label>
                <textarea
                  id="description"
                  value={description}
                  onChange={(e) => setDescription(e.target.value)}
                  placeholder="Optional description of the assignment"
                  rows={3}
                  className="w-full rounded-md border border-[var(--border)] bg-[var(--bg-card)] px-3 py-2 text-sm text-[var(--text-primary)] placeholder:text-[var(--text-muted)] focus:outline-none focus:ring-2 focus:ring-indigo-500"
                />
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>Rubric</CardTitle>
            </CardHeader>
            <CardContent className="space-y-4">
              <div className="space-y-2">
                <div className="flex items-center justify-between">
                  <label htmlFor="rubric" className="block text-sm font-medium text-[var(--text-secondary)]">
                    Grading Criteria
                  </label>
                  <div className="flex items-center gap-2">
                    <select
                      data-testid="strictness-select"
                      value={strictness}
                      onChange={(e) => setStrictness(e.target.value as 'balanced' | 'strict' | 'lenient')}
                      className="rounded-md border border-[var(--border)] bg-[var(--bg-card)] px-2 py-1 text-xs text-[var(--text-secondary)] focus:outline-none focus:ring-2 focus:ring-indigo-500"
                    >
                      <option value="balanced">Balanced</option>
                      <option value="strict">Strict</option>
                      <option value="lenient">Lenient</option>
                    </select>
                    <select
                      data-testid="detail-level-select"
                      value={detailLevel}
                      onChange={(e) => setDetailLevel(e.target.value as 'simple' | 'balanced' | 'detailed')}
                      className="rounded-md border border-[var(--border)] bg-[var(--bg-card)] px-2 py-1 text-xs text-[var(--text-secondary)] focus:outline-none focus:ring-2 focus:ring-indigo-500"
                    >
                      <option value="simple">Simple</option>
                      <option value="balanced">Standard</option>
                      <option value="detailed">Detailed</option>
                    </select>
                    <Button
                      type="button"
                      variant="outline"
                      size="sm"
                      data-testid="generate-rubric-btn"
                      disabled={!description.trim() || isGenerating}
                      onClick={handleGenerateRubric}
                      className="gap-1.5"
                    >
                      {isGenerating ? (
                        <Loader2 className="h-3.5 w-3.5 animate-spin" />
                      ) : (
                        <Sparkles className="h-3.5 w-3.5" />
                      )}
                      {isGenerating ? 'Generating...' : 'Generate with AI'}
                    </Button>
                  </div>
                </div>
                {!description.trim() && (
                  <p className="text-xs text-[var(--text-muted)]">
                    Add a description above to enable AI rubric generation.
                  </p>
                )}
                {generationError && (
                  <p className="text-xs text-rose-500" role="alert">{generationError}</p>
                )}

                {/* Editor mode toggle */}
                {criteria.length > 0 && (
                  <div className="flex items-center gap-1 rounded-md border border-[var(--border)] p-0.5 w-fit">
                    <button
                      type="button"
                      onClick={switchToStructuredMode}
                      className={cn(
                        'flex items-center gap-1.5 rounded px-2.5 py-1 text-xs font-medium transition-colors',
                        editorMode === 'structured'
                          ? 'bg-indigo-500 text-white'
                          : 'text-[var(--text-muted)] hover:text-[var(--text-secondary)]'
                      )}
                    >
                      <List className="h-3 w-3" />
                      Structured
                    </button>
                    <button
                      type="button"
                      onClick={switchToTextMode}
                      className={cn(
                        'flex items-center gap-1.5 rounded px-2.5 py-1 text-xs font-medium transition-colors',
                        editorMode === 'text'
                          ? 'bg-indigo-500 text-white'
                          : 'text-[var(--text-muted)] hover:text-[var(--text-secondary)]'
                      )}
                    >
                      <FileText className="h-3 w-3" />
                      Text
                    </button>
                  </div>
                )}

                {editorMode === 'structured' && criteria.length > 0 ? (
                  <RubricEditor
                    criteria={criteria}
                    onChange={handleCriteriaChange}
                    maxScore={Number(maxScore) || 100}
                    questions={extractedQuestions}
                  />
                ) : (
                  <textarea
                    id="rubric"
                    data-testid="rubric-input"
                    value={rubricDisplay || rubric}
                    onChange={(e) => {
                      setRubricDisplay(e.target.value)
                      setRubric(e.target.value)
                    }}
                    placeholder={"1. Code Quality (5 points)\n   Check for clean code, proper naming, no dead code. Full: clean & readable. Partial (3/5): minor issues. Zero: unreadable.\n2. Documentation (3 points)\n   README with setup instructions, inline comments. Full: comprehensive. Partial: incomplete.\n3. Testing (2 points)\n   Unit tests covering main functions. Full: 80%+ coverage. Partial: some tests."}
                    rows={8}
                    className="w-full rounded-md border border-[var(--border)] bg-[var(--bg-card)] px-3 py-2 text-sm font-mono text-[var(--text-primary)] placeholder:text-[var(--text-muted)] focus:outline-none focus:ring-2 focus:ring-indigo-500"
                  />
                )}
                {errors.rubric && (
                  <p className="text-xs text-rose-500" role="alert">{errors.rubric}</p>
                )}
              </div>

              <div className="space-y-2">
                <label className="block text-sm font-medium text-[var(--text-secondary)]">
                  Max Score
                </label>
                <div className="flex items-center gap-2">
                  {presets.map((p) => (
                    <Button
                      key={p}
                      type="button"
                      variant={maxScore === String(p) ? 'primary' : 'outline'}
                      size="sm"
                      onClick={() => setMaxScore(String(p))}
                    >
                      {p}
                    </Button>
                  ))}
                  <Input
                    id="maxScore"
                    value={maxScore}
                    onChange={(e) => setMaxScore(e.target.value)}
                    className="w-24"
                    error={errors.maxScore}
                  />
                </div>
              </div>
            </CardContent>
          </Card>

          <Card>
            <CardContent className="pt-6">
              <details className="group">
                <summary className="flex cursor-pointer items-center gap-2 text-sm font-medium text-[var(--text-secondary)] select-none">
                  <ChevronDown className="h-4 w-4 transition-transform group-open:rotate-180" />
                  Reference Solution (Optional)
                </summary>
                <div className="mt-3 space-y-2">
                  <p className="text-xs text-[var(--text-muted)]">
                    Provide a model answer for more accurate grading. If left empty, the system grades against the rubric alone.
                  </p>
                  <textarea
                    id="referenceSolution"
                    data-testid="reference-solution-input"
                    value={referenceSolution}
                    onChange={(e) => setReferenceSolution(e.target.value)}
                    placeholder="Paste or type the ideal solution here..."
                    rows={6}
                    className="w-full rounded-md border border-[var(--border)] bg-[var(--bg-card)] px-3 py-2 text-sm font-mono text-[var(--text-primary)] placeholder:text-[var(--text-muted)] focus:outline-none focus:ring-2 focus:ring-indigo-500"
                  />
                </div>
              </details>
            </CardContent>
          </Card>

          {errors.form && (
            <p className="text-sm text-rose-500 text-center" role="alert">
              {errors.form}
            </p>
          )}

          <div className="flex justify-end gap-3">
            <Button type="button" variant="ghost" onClick={() => router.back()}>
              Cancel
            </Button>
            <Button type="submit" disabled={isSubmitting}>
              {isSubmitting ? 'Creating...' : 'Create Session'}
            </Button>
          </div>
        </form>
      </div>
    </AppShell>
  )
}
