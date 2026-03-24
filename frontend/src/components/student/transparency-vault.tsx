'use client'

import { useState } from 'react'
import { Card, CardContent } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { cn } from '@/lib/utils'
import type { Submission, IngestionReport } from '@/lib/types'
import {
  Shield,
  Brain,
  Hash,
  BarChart3,
  FileText,
  Image as ImageIcon,
  AlertTriangle,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Clock,
  Cpu,
  Database,
  Eye,
  Layers,
  Zap,
} from 'lucide-react'

interface TransparencyVaultProps {
  submission: Submission
  ingestionReport?: IngestionReport
}

export function TransparencyVault({ submission, ingestionReport }: TransparencyVaultProps) {
  const [expandedSections, setExpandedSections] = useState<Set<string>>(new Set(['model', 'tokens']))
  const aiResult = submission.ai_result

  const toggleSection = (section: string) => {
    setExpandedSections((prev) => {
      const next = new Set(prev)
      if (next.has(section)) next.delete(section)
      else next.add(section)
      return next
    })
  }

  const transparency = aiResult?.transparency
  const llmCall = transparency?.llm_call

  const hasData = !!(
    llmCall ||
    aiResult?.grading_hash ||
    aiResult?.confidence_reasoning ||
    transparency ||
    ingestionReport
  )

  if (!hasData) {
    return (
      <Card>
        <CardContent className="py-12 text-center">
          <Shield className="h-10 w-10 text-[var(--text-muted)] mx-auto mb-3 opacity-40" />
          <p className="text-[var(--text-muted)] text-sm">
            No transparency data available for this submission.
          </p>
          <p className="text-[var(--text-muted)] text-xs mt-1">
            Transparency data is generated during the grading process.
          </p>
        </CardContent>
      </Card>
    )
  }

  const sections = [
    {
      id: 'model',
      title: 'Model Information',
      icon: Brain,
      badge: llmCall?.model ? llmCall.model.split('/').pop() : null,
      content: llmCall ? (
        <div className="grid grid-cols-2 gap-3">
          <InfoRow label="Model" value={llmCall.model} />
          <InfoRow label="Provider" value={llmCall.provider} />
          <InfoRow
            label="Fallback Used"
            value={
              llmCall.fallback_used ? (
                <Badge variant="warning" className="text-[10px]">Yes</Badge>
              ) : (
                <Badge variant="success" className="text-[10px]">No</Badge>
              )
            }
          />
          {llmCall.consistency_alert != null && (
            <InfoRow
              label="Consistency"
              value={
                llmCall.consistency_alert ? (
                  <Badge variant="error" className="text-[10px]">Alert</Badge>
                ) : (
                  <Badge variant="success" className="text-[10px]">OK</Badge>
                )
              }
            />
          )}
        </div>
      ) : null,
    },
    {
      id: 'tokens',
      title: 'Token Usage',
      icon: Cpu,
      badge: llmCall?.usage
        ? `${(llmCall.usage.total_tokens ?? 0).toLocaleString()} total`
        : null,
      content: llmCall?.usage ? (
        <div className="space-y-3">
          <div className="grid grid-cols-3 gap-3">
            <TokenStat label="Prompt" value={llmCall.usage.prompt_tokens} />
            <TokenStat label="Completion" value={llmCall.usage.completion_tokens} />
            <TokenStat label="Total" value={llmCall.usage.total_tokens} accent />
          </div>
          {/* Token distribution bar */}
          {llmCall.usage.total_tokens > 0 && (
            <div className="space-y-1">
              <div className="flex h-2 rounded-full overflow-hidden bg-[var(--border)]">
                <div
                  className="bg-indigo-500 h-full"
                  style={{
                    width: `${(llmCall.usage.prompt_tokens / llmCall.usage.total_tokens) * 100}%`,
                  }}
                />
                <div
                  className="bg-violet-500 h-full"
                  style={{
                    width: `${(llmCall.usage.completion_tokens / llmCall.usage.total_tokens) * 100}%`,
                  }}
                />
              </div>
              <div className="flex justify-between text-[10px] text-[var(--text-muted)]">
                <span className="flex items-center gap-1">
                  <span className="inline-block h-2 w-2 rounded-full bg-indigo-500" />
                  Prompt ({Math.round((llmCall.usage.prompt_tokens / llmCall.usage.total_tokens) * 100)}%)
                </span>
                <span className="flex items-center gap-1">
                  <span className="inline-block h-2 w-2 rounded-full bg-violet-500" />
                  Completion ({Math.round((llmCall.usage.completion_tokens / llmCall.usage.total_tokens) * 100)}%)
                </span>
              </div>
            </div>
          )}
        </div>
      ) : null,
    },
    {
      id: 'input',
      title: 'Input Statistics',
      icon: Database,
      badge: transparency
        ? `${(transparency.text_chars_sent ?? 0).toLocaleString()} chars`
        : null,
      content: transparency ? (
        <div className="grid grid-cols-2 gap-3">
          <div className="rounded-lg bg-[var(--bg-page)] border border-[var(--border)] p-3 text-center">
            <FileText className="h-5 w-5 text-indigo-400 mx-auto mb-1" />
            <p className="text-lg font-bold tabular-nums text-[var(--text-primary)]">
              {(transparency.text_chars_sent ?? 0).toLocaleString()}
            </p>
            <p className="text-[10px] text-[var(--text-muted)] uppercase tracking-wider">Characters Sent</p>
          </div>
          <div className="rounded-lg bg-[var(--bg-page)] border border-[var(--border)] p-3 text-center">
            <ImageIcon className="h-5 w-5 text-violet-400 mx-auto mb-1" />
            <p className="text-lg font-bold tabular-nums text-[var(--text-primary)]">
              {transparency.images_sent ?? 0}
            </p>
            <p className="text-[10px] text-[var(--text-muted)] uppercase tracking-wider">Images Sent</p>
          </div>
        </div>
      ) : null,
    },
    {
      id: 'ocr',
      title: 'OCR Image Classification',
      icon: Eye,
      badge: (() => {
        const ocr = (aiResult as any)?.transparency?.ocr_classification
        if (!ocr) return null
        return `${ocr.visual_heavy ?? 0} visual / ${ocr.text_heavy ?? 0} text`
      })(),
      content: (() => {
        const ocr = (aiResult as any)?.transparency?.ocr_classification
        if (!ocr) return null
        return (
          <div className="space-y-3">
            <div className="flex items-center gap-2">
              <span className="text-xs text-[var(--text-muted)]">Tesseract Available:</span>
              <Badge variant={ocr.tesseract_available ? 'success' : 'warning'} className="text-[10px]">
                {ocr.tesseract_available ? 'Yes' : 'No'}
              </Badge>
            </div>
            <div className="grid grid-cols-3 gap-3">
              <div className="rounded-lg bg-[var(--bg-page)] border border-[var(--border)] p-2.5 text-center">
                <p className="text-lg font-bold tabular-nums text-[var(--text-primary)]">
                  {ocr.total_images ?? 0}
                </p>
                <p className="text-[10px] text-[var(--text-muted)] uppercase tracking-wider">Total Images</p>
              </div>
              <div className="rounded-lg bg-violet-500/5 border border-violet-500/20 p-2.5 text-center">
                <p className="text-lg font-bold tabular-nums text-violet-400">
                  {ocr.visual_heavy ?? 0}
                </p>
                <p className="text-[10px] text-violet-400/80 uppercase tracking-wider">Visual-Heavy</p>
              </div>
              <div className="rounded-lg bg-indigo-500/5 border border-indigo-500/20 p-2.5 text-center">
                <p className="text-lg font-bold tabular-nums text-indigo-400">
                  {ocr.text_heavy ?? 0}
                </p>
                <p className="text-[10px] text-indigo-400/80 uppercase tracking-wider">Text-Heavy</p>
              </div>
            </div>
            <div className="grid grid-cols-2 gap-3">
              <div className="rounded-lg bg-[var(--bg-page)] border border-[var(--border)] p-2.5 text-center">
                <ImageIcon className="h-4 w-4 text-violet-400 mx-auto mb-1" />
                <p className="text-sm font-bold tabular-nums text-[var(--text-primary)]">
                  {ocr.final_images_visual_heavy ?? 0}
                </p>
                <p className="text-[10px] text-[var(--text-muted)]">Final Visual Images</p>
              </div>
              <div className="rounded-lg bg-[var(--bg-page)] border border-[var(--border)] p-2.5 text-center">
                <FileText className="h-4 w-4 text-indigo-400 mx-auto mb-1" />
                <p className="text-sm font-bold tabular-nums text-[var(--text-primary)]">
                  {ocr.final_images_text_heavy ?? 0}
                </p>
                <p className="text-[10px] text-[var(--text-muted)]">Final Text Images</p>
              </div>
            </div>
            <p className="text-xs text-[var(--text-muted)] leading-relaxed">
              Visual-heavy images (diagrams, graphs) are prioritized for grading call image slots. Text-heavy images (handwritten notes) are covered by their transcriptions.
            </p>
          </div>
        )
      })(),
    },
    {
      id: 'confidence',
      title: 'Confidence Analysis',
      icon: BarChart3,
      badge: submission.ai_confidence || aiResult?.confidence,
      content: aiResult?.confidence_reasoning || submission.confidence_reasoning ? (
        <div className="space-y-3">
          <div className="flex items-center gap-2">
            <span className="text-sm text-[var(--text-muted)]">Level:</span>
            <Badge
              variant={
                (submission.ai_confidence || aiResult?.confidence) === 'high' ? 'success'
                : (submission.ai_confidence || aiResult?.confidence) === 'medium' ? 'warning'
                : 'error'
              }
            >
              {(submission.ai_confidence || aiResult?.confidence || 'unknown').toUpperCase()}
            </Badge>
          </div>
          <p className="text-sm text-[var(--text-secondary)] leading-relaxed">
            {aiResult?.confidence_reasoning || submission.confidence_reasoning}
          </p>
        </div>
      ) : null,
    },
    {
      id: 'verification',
      title: 'Score Verification',
      icon: Shield,
      badge: (() => {
        const sv = (aiResult as any)?.transparency?.score_verification
        if (!sv) return null
        const count = sv.adjustments_applied ?? 0
        return count > 0 ? `${count} adjustments` : 'All confirmed'
      })(),
      content: (() => {
        const sv = (aiResult as any)?.transparency?.score_verification
        if (!sv) return null
        return (
          <div className="space-y-3">
            <div className="flex items-center gap-3">
              <div className="flex items-center gap-2">
                <span className="text-xs text-[var(--text-muted)]">Verification:</span>
                <Badge variant={sv.enabled ? 'success' : 'warning'} className="text-[10px]">
                  {sv.enabled ? 'Enabled' : 'Disabled'}
                </Badge>
              </div>
              <div className="flex items-center gap-2">
                <span className="text-xs text-[var(--text-muted)]">Adjustments:</span>
                <Badge
                  variant={(sv.adjustments_applied ?? 0) > 0 ? 'warning' : 'success'}
                  className="text-[10px]"
                >
                  {sv.adjustments_applied ?? 0}
                </Badge>
              </div>
            </div>
            {sv.details && sv.details.length > 0 && (
              <div className="space-y-2">
                {sv.details.map((detail: any, i: number) => {
                  const adjusted = detail.original_score !== detail.verified_score
                  return (
                    <div
                      key={i}
                      className={cn(
                        'rounded-lg border p-3',
                        adjusted
                          ? 'bg-amber-500/5 border-amber-500/20'
                          : 'bg-[var(--bg-page)] border-[var(--border)]'
                      )}
                    >
                      <div className="flex items-center justify-between">
                        <span className="text-sm font-medium text-[var(--text-primary)]">
                          {detail.criterion}
                        </span>
                        <div className="flex items-center gap-2">
                          <Badge
                            variant={
                              detail.confidence === 'high' ? 'success'
                              : detail.confidence === 'medium' ? 'warning'
                              : 'error'
                            }
                            className="text-[10px]"
                          >
                            {(detail.confidence ?? 'unknown').toUpperCase()}
                          </Badge>
                          {adjusted ? (
                            <AlertTriangle className="h-3.5 w-3.5 text-amber-400" />
                          ) : (
                            <CheckCircle2 className="h-3.5 w-3.5 text-emerald-400" />
                          )}
                        </div>
                      </div>
                      <div className="flex items-center gap-3 mt-1.5 text-xs text-[var(--text-muted)]">
                        <span>Original: <strong className="text-[var(--text-primary)]">{detail.original_score}</strong></span>
                        <span>Verified: <strong className={adjusted ? 'text-amber-400' : 'text-[var(--text-primary)]'}>{detail.verified_score}</strong></span>
                      </div>
                      {adjusted && detail.reason && (
                        <p className="text-xs text-amber-400/80 mt-1.5">
                          {detail.reason}
                        </p>
                      )}
                    </div>
                  )
                })}
              </div>
            )}
          </div>
        )
      })(),
    },
    {
      id: 'hash',
      title: 'Grading Hash',
      icon: Hash,
      content: aiResult?.grading_hash ? (
        <div className="space-y-2">
          <p className="text-xs text-[var(--text-muted)]">
            Deterministic hash of input data for reproducibility verification.
          </p>
          <div className="rounded-lg bg-[var(--bg-page)] border border-[var(--border)] p-3">
            <code className="text-xs font-mono text-indigo-400 break-all select-all">
              {aiResult.grading_hash}
            </code>
          </div>
        </div>
      ) : null,
    },
    {
      id: 'ingestion',
      title: 'File Ingestion Report',
      icon: Layers,
      badge: ingestionReport
        ? `${ingestionReport.summary?.parsed ?? ingestionReport.files_parsed ?? 0} parsed`
        : null,
      content: ingestionReport ? (
        <div className="space-y-3">
          <div className="grid grid-cols-3 gap-3">
            <div className="rounded-lg bg-[var(--bg-page)] border border-[var(--border)] p-2.5 text-center">
              <p className="text-lg font-bold tabular-nums text-[var(--text-primary)]">
                {ingestionReport.summary?.received ?? ingestionReport.files_received ?? 0}
              </p>
              <p className="text-[10px] text-[var(--text-muted)]">Received</p>
            </div>
            <div className="rounded-lg bg-emerald-500/5 border border-emerald-500/20 p-2.5 text-center">
              <p className="text-lg font-bold tabular-nums text-emerald-400">
                {ingestionReport.summary?.parsed ?? ingestionReport.files_parsed ?? 0}
              </p>
              <p className="text-[10px] text-emerald-400/80">Parsed</p>
            </div>
            <div className={cn(
              'rounded-lg border p-2.5 text-center',
              (ingestionReport.summary?.failed ?? ingestionReport.files_failed ?? 0) > 0
                ? 'bg-rose-500/5 border-rose-500/20'
                : 'bg-[var(--bg-page)] border-[var(--border)]'
            )}>
              <p className={cn(
                'text-lg font-bold tabular-nums',
                (ingestionReport.summary?.failed ?? ingestionReport.files_failed ?? 0) > 0
                  ? 'text-rose-400'
                  : 'text-[var(--text-primary)]'
              )}>
                {ingestionReport.summary?.failed ?? ingestionReport.files_failed ?? 0}
              </p>
              <p className="text-[10px] text-[var(--text-muted)]">Failed</p>
            </div>
          </div>

          {ingestionReport.total_text_chars != null && (
            <div className="flex items-center gap-4 text-xs text-[var(--text-muted)]">
              <span>Text: {ingestionReport.total_text_chars.toLocaleString()} chars</span>
              {ingestionReport.total_images != null && (
                <span>Images: {ingestionReport.total_images}</span>
              )}
              {ingestionReport.content_truncated && (
                <Badge variant="warning" className="text-[10px]">Content Truncated</Badge>
              )}
            </div>
          )}

          {/* Warnings */}
          {ingestionReport.warnings && ingestionReport.warnings.length > 0 && (
            <div className="space-y-1">
              <p className="text-xs font-medium text-amber-400">Warnings</p>
              {ingestionReport.warnings.map((w, i) => (
                <div key={i} className="flex items-start gap-2 text-xs text-amber-400/80">
                  <AlertTriangle className="h-3 w-3 shrink-0 mt-0.5" />
                  <span>{w}</span>
                </div>
              ))}
            </div>
          )}

          {/* Errors */}
          {ingestionReport.errors && ingestionReport.errors.length > 0 && (
            <div className="space-y-1">
              <p className="text-xs font-medium text-rose-400">Errors</p>
              {ingestionReport.errors.map((e, i) => (
                <div key={i} className="flex items-start gap-2 text-xs text-rose-400/80">
                  <AlertTriangle className="h-3 w-3 shrink-0 mt-0.5" />
                  <span>{e}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      ) : null,
    },
  ]

  return (
    <div className="space-y-3">
      {sections.map((section) => {
        if (!section.content) return null
        const isExpanded = expandedSections.has(section.id)
        const Icon = section.icon

        return (
          <Card key={section.id}>
            <button
              className="w-full flex items-center gap-3 px-5 py-3 text-left hover:bg-[var(--border)]/20 transition-colors"
              onClick={() => toggleSection(section.id)}
            >
              <Icon className="h-4 w-4 text-indigo-400 shrink-0" />
              <span className="text-sm font-semibold text-[var(--text-primary)] flex-1">
                {section.title}
              </span>
              {section.badge && (
                <Badge variant="default" className="text-[10px]">
                  {section.badge}
                </Badge>
              )}
              {isExpanded ? (
                <ChevronDown className="h-4 w-4 text-[var(--text-muted)]" />
              ) : (
                <ChevronRight className="h-4 w-4 text-[var(--text-muted)]" />
              )}
            </button>
            {isExpanded && (
              <CardContent className="pt-0 pb-4 px-5">
                {section.content}
              </CardContent>
            )}
          </Card>
        )
      })}
    </div>
  )
}

function InfoRow({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="rounded-lg bg-[var(--bg-page)] border border-[var(--border)] px-3 py-2">
      <p className="text-[10px] text-[var(--text-muted)] uppercase tracking-wider">{label}</p>
      <p className="text-sm font-medium text-[var(--text-primary)] mt-0.5 truncate">
        {typeof value === 'string' ? value : value}
      </p>
    </div>
  )
}

function TokenStat({ label, value, accent }: { label: string; value: number; accent?: boolean }) {
  return (
    <div className={cn(
      'rounded-lg border p-2.5 text-center',
      accent ? 'bg-indigo-500/5 border-indigo-500/20' : 'bg-[var(--bg-page)] border-[var(--border)]'
    )}>
      <p className={cn(
        'text-lg font-bold tabular-nums',
        accent ? 'text-indigo-400' : 'text-[var(--text-primary)]'
      )}>
        {(value ?? 0).toLocaleString()}
      </p>
      <p className="text-[10px] text-[var(--text-muted)] uppercase tracking-wider">{label}</p>
    </div>
  )
}
