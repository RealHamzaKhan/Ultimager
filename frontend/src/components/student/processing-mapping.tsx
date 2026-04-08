'use client'

import { Card, CardContent } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { cn } from '@/lib/utils'
import type { Submission, StudentFile, IngestionReport } from '@/lib/types'
import {
  FileText,
  Image,
  ChevronDown,
  ChevronRight,
  CheckCircle2,
  XCircle,
  AlertTriangle,
  ArrowRight,
  Eye,
  Cpu,
  Database,
  Zap,
  Target,
  Search,
  Layers,
  ShieldCheck,
  ScanEye,
} from 'lucide-react'
import { useState, useMemo } from 'react'

interface ProcessingMappingProps {
  submission: Submission
  ingestionReport?: IngestionReport
  files: StudentFile[]
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type R = Record<string, any>

export function ProcessingMapping({ submission, ingestionReport, files }: ProcessingMappingProps) {
  const aiResult = (submission.ai_result ?? {}) as R
  const transparency = (aiResult.transparency ?? {}) as R
  const rubricBreakdown = (aiResult.rubric_breakdown ?? submission.rubric_breakdown ?? []) as R[]
  const relevanceGate = (aiResult.relevance_gate ?? {}) as R
  const evidenceMap = (transparency.evidence_map ?? aiResult.evidence_map ?? []) as R[]
  const filesProcessed = (transparency.files_processed ?? []) as R[]
  const imagesInfo = (transparency.images_info ?? []) as R[]
  const imagesAnalyzed = (transparency.images_analyzed_info ?? []) as R[]
  const llmCall = (transparency.llm_call ?? {}) as R
  const visionPre = (transparency.vision_preanalysis ?? {}) as R
  const criterionEvidence = (transparency.criterion_evidence ?? {}) as R
  const multiPass = (transparency.multi_pass ?? null) as R | null
  const ocrClassification = (transparency.ocr_classification ?? null) as R | null
  const scoreVerification = (transparency.score_verification ?? null) as R | null
  const scoreVerificationDetails = (scoreVerification?.details ?? []) as R[]

  // Build enriched file list: merge transparency data with file metadata
  const IMAGE_TYPES = new Set(['image', 'png', 'jpg', 'jpeg', 'gif', 'bmp', 'webp'])
  type EnrichedFile = { filename: string; type: string; text_length: number; image_count: number; size?: number; _isImageFile: boolean; [k: string]: unknown }
  const enrichedFiles: EnrichedFile[] = useMemo(() => {
    if (filesProcessed.length > 0) {
      return filesProcessed.map(fp => {
        const isImageFile = IMAGE_TYPES.has((fp.type || '').toLowerCase()) ||
          /\.(jpe?g|png|gif|bmp|webp)$/i.test(fp.filename || '')
        return {
          ...fp,
          filename: fp.filename || 'unknown',
          type: fp.type || 'unknown',
          text_length: fp.text_length ?? 0,
          image_count: (fp.image_count ?? 0) || (isImageFile ? 1 : 0),
          _isImageFile: isImageFile,
        } as EnrichedFile
      })
    }
    return files.map(f => {
      const isImageFile = IMAGE_TYPES.has((f.type || '').toLowerCase()) ||
        /\.(jpe?g|png|gif|bmp|webp)$/i.test(f.filename || '')
      return {
        filename: f.filename,
        type: f.type || f.display_type || 'unknown',
        text_length: 0,
        image_count: isImageFile ? 1 : 0,
        size: f.size,
        _isImageFile: isImageFile,
      } as EnrichedFile
    })
  }, [filesProcessed, files])

  // Derive pipeline health indicators
  const health = useMemo(() => {
    const totalFiles = enrichedFiles.length
    // If transparency has images_available_total, use it. Otherwise infer from file types.
    const inferredImageCount = enrichedFiles.reduce((sum, f) => sum + (f.image_count || 0), 0)
    const totalImagesAvailable = transparency.images_available_total ?? inferredImageCount
    const totalImagesSelected = transparency.images_selected_total ?? 0
    const totalImagesSent = transparency.images_sent ?? imagesInfo.length ?? 0
    const textCharsSent = transparency.text_chars_sent ?? 0
    const hasVision = visionPre.enabled === true || imagesAnalyzed.length > 0
    const wasBlocked = relevanceGate.block_grading === true
    const llmCalled = !!llmCall.model
    const hasScore = typeof aiResult.total_score === 'number' && aiResult.total_score > 0
    // Detect stale transparency data: we have image files but transparency says 0
    const hasTransparency = Object.keys(transparency).length > 0
    const transparencyStale = !hasTransparency && totalFiles > 0

    return {
      totalFiles,
      totalImagesAvailable,
      totalImagesSelected,
      totalImagesSent,
      textCharsSent,
      hasVision,
      wasBlocked,
      llmCalled,
      hasScore,
      transparencyStale,
      inferredImageCount,
    }
  }, [enrichedFiles, transparency, visionPre, imagesAnalyzed, relevanceGate, llmCall, aiResult, imagesInfo])

  const wasBlocked = relevanceGate.block_grading === true

  return (
    <div className="space-y-5">
      {/* ══════════════════════════════════════════════════════════
          PIPELINE STATUS BANNER
         ══════════════════════════════════════════════════════════ */}
      <Card className={cn(
        'border',
        wasBlocked ? 'border-rose-500/30 bg-rose-500/5' : health.hasScore ? 'border-emerald-500/30 bg-emerald-500/5' : 'border-amber-500/30 bg-amber-500/5'
      )}>
        <CardContent className="p-4">
          <div className="flex items-center gap-3 mb-3">
            {wasBlocked ? (
              <XCircle className="h-5 w-5 text-rose-400" />
            ) : health.hasScore ? (
              <CheckCircle2 className="h-5 w-5 text-emerald-400" />
            ) : (
              <AlertTriangle className="h-5 w-5 text-amber-400" />
            )}
            <h3 className="text-sm font-bold text-[var(--text-primary)]">
              {wasBlocked
                ? 'Grading was BLOCKED by Relevance Gate'
                : health.hasScore
                  ? 'Pipeline Completed Successfully'
                  : 'Pipeline completed with warnings'}
            </h3>
          </div>

          {/* Pipeline flow visualization */}
          <div className="flex items-center gap-2 flex-wrap">
            <PipelineStep label="Files" value={health.totalFiles} ok={health.totalFiles > 0} />
            <ArrowRight className="h-3 w-3 text-[var(--text-muted)] shrink-0" />
            <PipelineStep label="Images Found" value={health.totalImagesAvailable} ok={true} />
            <ArrowRight className="h-3 w-3 text-[var(--text-muted)] shrink-0" />
            <PipelineStep
              label="OCR Classified"
              value={ocrClassification ? `${ocrClassification.visual_heavy ?? 0}V / ${ocrClassification.text_heavy ?? 0}T` : '—'}
              ok={!!ocrClassification}
            />
            <ArrowRight className="h-3 w-3 text-[var(--text-muted)] shrink-0" />
            <PipelineStep label="Selected" value={health.totalImagesSelected} ok={health.totalImagesSelected > 0 || health.textCharsSent > 0} />
            <ArrowRight className="h-3 w-3 text-[var(--text-muted)] shrink-0" />
            <PipelineStep label="Vision Analysis" value={health.hasVision ? 'Yes' : 'No'} ok={health.hasVision || health.totalImagesAvailable === 0} />
            <ArrowRight className="h-3 w-3 text-[var(--text-muted)] shrink-0" />
            <PipelineStep label="Sent to LLM" value={`${health.totalImagesSent} img + ${fmtK(health.textCharsSent)} text`} ok={health.llmCalled} />
            <ArrowRight className="h-3 w-3 text-[var(--text-muted)] shrink-0" />
            <PipelineStep
              label="Result"
              value={wasBlocked ? 'BLOCKED' : health.hasScore ? `${aiResult.total_score}/${aiResult.max_score}` : 'Error'}
              ok={health.hasScore}
            />
            <ArrowRight className="h-3 w-3 text-[var(--text-muted)] shrink-0" />
            <PipelineStep
              label="Verified"
              value={scoreVerification?.trace?.enabled ? '\u2713' : '—'}
              ok={!!scoreVerification?.trace?.enabled}
            />
          </div>

          {wasBlocked && (
            <div className="mt-3 text-xs text-rose-300 bg-rose-500/10 rounded px-3 py-2">
              <strong>Block Reason:</strong> {relevanceGate.reason || 'Unknown'}
              {relevanceGate.flags?.length > 0 && (
                <span className="ml-2">Flags: {(relevanceGate.flags as string[]).join(', ')}</span>
              )}
              <p className="mt-1 text-rose-300/70">
                This means the system decided not to grade this submission. If this is incorrect,
                click &quot;Regrade&quot; to re-process with the latest fixes.
              </p>
            </div>
          )}
        </CardContent>
      </Card>

      {/* ══════════════════════════════════════════════════════════
          SECTION 1: FILE EXTRACTION DETAILS
         ══════════════════════════════════════════════════════════ */}
      <Accordion title="1. File Extraction" icon={Database} subtitle={`${health.totalFiles} files → ${fmtK(health.textCharsSent || enrichedFiles.reduce((s, f) => s + (f.text_length || 0), 0))} chars text + ${health.inferredImageCount} images`} defaultOpen>
        {health.transparencyStale && (
          <div className="mb-3 flex items-center gap-2 text-xs text-amber-400 bg-amber-500/10 rounded-lg px-3 py-2">
            <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
            <span>
              <strong>Stale data:</strong> This submission was graded before pipeline transparency was added.
              File details below are inferred from metadata. <strong>Regrade</strong> this student for full transparency.
            </span>
          </div>
        )}
        <div className="space-y-1.5">
          {enrichedFiles.map((fp, i) => {
            const hasText = (fp.text_length ?? 0) > 0
            const hasImages = (fp.image_count ?? 0) > 0
            const isImageFile = fp._isImageFile === true
            // Status logic: image files are always "ok" (they contribute images),
            // non-image files need either text or images to be "ok"
            const fileOk = isImageFile || hasText || hasImages
            return (
              <div
                key={i}
                className="flex items-center justify-between rounded-lg bg-[var(--bg-secondary)] px-4 py-2.5"
              >
                <div className="flex items-center gap-3">
                  <div className={cn(
                    'w-8 h-8 rounded-md flex items-center justify-center',
                    isImageFile ? 'bg-purple-500/20' : fp.type === 'pdf' ? 'bg-rose-500/20' : 'bg-blue-500/20'
                  )}>
                    {isImageFile ? <Image className="h-4 w-4 text-purple-400" /> : <FileText className="h-4 w-4 text-blue-400" />}
                  </div>
                  <div>
                    <p className="text-sm font-medium text-[var(--text-primary)]">{fp.filename}</p>
                    <p className="text-[11px] text-[var(--text-muted)]">
                      Type: {fp.type}
                      {fp.size ? ` · ${fmtBytes(fp.size)}` : ''}
                    </p>
                  </div>
                </div>
                <div className="flex items-center gap-3">
                  {hasText && (
                    <Badge variant="default" className="text-[10px] gap-1">
                      <FileText className="h-2.5 w-2.5" /> {fmtK(fp.text_length)} chars
                    </Badge>
                  )}
                  {hasImages && (
                    <Badge variant="default" className="text-[10px] gap-1">
                      <Image className="h-2.5 w-2.5" /> {fp.image_count} {fp.image_count === 1 ? 'image' : 'images'}
                    </Badge>
                  )}
                  {!hasText && !hasImages && !isImageFile && (
                    <Badge variant="warning" className="text-[10px]">no content extracted</Badge>
                  )}
                  {fileOk ? (
                    <CheckCircle2 className="h-4 w-4 text-emerald-400" />
                  ) : (
                    <XCircle className="h-4 w-4 text-rose-400" />
                  )}
                </div>
              </div>
            )
          })}

          {/* Ingestion warnings/errors */}
          {ingestionReport?.warnings && ingestionReport.warnings.length > 0 && (
            <div className="mt-2 space-y-1">
              {ingestionReport.warnings.map((w, i) => (
                <div key={i} className="flex items-center gap-2 text-xs text-amber-400">
                  <AlertTriangle className="h-3 w-3 shrink-0" /> {w}
                </div>
              ))}
            </div>
          )}
          {ingestionReport?.errors && ingestionReport.errors.length > 0 && (
            <div className="mt-2 space-y-1">
              {ingestionReport.errors.map((e, i) => (
                <div key={i} className="flex items-center gap-2 text-xs text-rose-400">
                  <XCircle className="h-3 w-3 shrink-0" /> {e}
                </div>
              ))}
            </div>
          )}
          {ingestionReport?.content_truncated && (
            <div className="mt-2 flex items-center gap-2 text-xs text-amber-400">
              <AlertTriangle className="h-3 w-3" />
              Content was truncated due to size limits. Some text may not have been sent to the LLM.
            </div>
          )}
        </div>
      </Accordion>

      {/* ══════════════════════════════════════════════════════════
          SECTION 2: IMAGE ANALYSIS (Vision Pre-Analysis)
         ══════════════════════════════════════════════════════════ */}
      <Accordion
        title="2. Image Analysis"
        icon={Eye}
        subtitle={
          health.totalImagesAvailable === 0
            ? 'No images in submission'
            : health.transparencyStale
              ? `${health.inferredImageCount} images detected (regrade for full details)`
              : `${health.totalImagesSelected} of ${health.totalImagesAvailable} selected → ${imagesAnalyzed.length || health.totalImagesSelected} analyzed`
        }
        defaultOpen={health.totalImagesAvailable > 0}
      >
        {health.totalImagesAvailable === 0 ? (
          <p className="text-xs text-[var(--text-muted)]">
            This submission contained no images. Only text content was sent to the grading LLM.
          </p>
        ) : health.transparencyStale && imagesAnalyzed.length === 0 && imagesInfo.length === 0 ? (
          <div className="space-y-2">
            <div className="flex items-center gap-2 text-xs text-amber-400">
              <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
              <span>
                This submission contains <strong>{health.inferredImageCount} image(s)</strong> but was graded before detailed image tracking was added.
                <strong> Regrade</strong> to see full image analysis details.
              </span>
            </div>
            <div className="grid grid-cols-2 md:grid-cols-4 gap-2 text-xs">
              <StatBox label="Images Detected" value={health.inferredImageCount} />
              <StatBox label="Details Available" value="Regrade needed" />
            </div>
          </div>
        ) : (
          <div className="space-y-3">
            {/* Selection stats */}
            <div className="grid grid-cols-2 md:grid-cols-4 gap-2 text-xs">
              <StatBox label="Available" value={health.totalImagesAvailable} />
              <StatBox label="Pool Limit" value={transparency.selection_pool_limit ?? '∞'} />
              <StatBox label="Selected (deduplicated)" value={health.totalImagesSelected} />
              <StatBox label="Sent in Final Call" value={health.totalImagesSent} />
            </div>

            {/* OCR Classification Summary */}
            {ocrClassification && (
              <div className="rounded-lg border border-indigo-500/20 bg-indigo-500/5 p-3">
                <p className="font-semibold text-indigo-400 mb-2 flex items-center gap-2 text-xs">
                  <ScanEye className="h-3.5 w-3.5" /> OCR Classification
                  {ocrClassification.tesseract_available === false && (
                    <Badge variant="warning" className="text-[9px] ml-1">Tesseract unavailable</Badge>
                  )}
                </p>
                <div className="grid grid-cols-2 md:grid-cols-3 gap-2 text-xs">
                  <StatBox label="Total Images" value={ocrClassification.total_images} />
                  <StatBox label="Visual-Heavy" value={`${ocrClassification.visual_heavy ?? 0} (diagrams/graphs)`} />
                  <StatBox label="Text-Heavy" value={`${ocrClassification.text_heavy ?? 0} (handwritten notes)`} />
                </div>
                <div className="grid grid-cols-2 gap-2 text-xs mt-2">
                  <div className="rounded-md bg-emerald-500/10 border border-emerald-500/20 px-3 py-2 text-center">
                    <p className="text-[9px] text-[var(--text-muted)] uppercase tracking-wider">Final: Visual-Heavy</p>
                    <p className="text-xs font-bold text-emerald-400">{ocrClassification.final_images_visual_heavy ?? 0} sent</p>
                  </div>
                  <div className="rounded-md bg-blue-500/10 border border-blue-500/20 px-3 py-2 text-center">
                    <p className="text-[9px] text-[var(--text-muted)] uppercase tracking-wider">Final: Text-Heavy</p>
                    <p className="text-xs font-bold text-blue-400">{ocrClassification.final_images_text_heavy ?? 0} sent</p>
                  </div>
                </div>
                <p className="text-[10px] text-[var(--text-muted)] mt-2">
                  Visual-heavy images (diagrams, graphs) are prioritized for image slots. Text-heavy images (handwritten notes) are covered by transcriptions.
                </p>
              </div>
            )}

            {transparency.image_limit_applied && (
              <div className="text-xs text-amber-400 flex items-center gap-2">
                <AlertTriangle className="h-3 w-3" />
                Image limit was applied — not all selected images fit in the final LLM call.
                {transparency.provider_image_cap && <> (Provider cap: {transparency.provider_image_cap})</>}
              </div>
            )}

            {/* Per-image breakdown */}
            {(imagesAnalyzed.length > 0 || imagesInfo.length > 0) && (
              <div>
                <p className="text-xs font-semibold text-[var(--text-primary)] mb-2">
                  Image Details ({Math.max(imagesAnalyzed.length, imagesInfo.length)} images):
                </p>
                <div className="max-h-72 overflow-y-auto space-y-1.5 pr-1">
                  {(imagesAnalyzed.length > 0 ? imagesAnalyzed : imagesInfo).map((img, i) => (
                    <div
                      key={i}
                      className="flex items-center justify-between rounded-md bg-[var(--bg-secondary)] px-3 py-2"
                    >
                      <div className="flex items-center gap-2.5">
                        <div className="w-7 h-7 rounded bg-purple-500/20 flex items-center justify-center">
                          <Image className="h-3.5 w-3.5 text-purple-400" />
                        </div>
                        <div>
                          <p className="text-xs font-medium text-[var(--text-primary)]">
                            <span className="font-mono text-indigo-400">{img.image_id || `img_${i+1}`}</span>
                            {' '}{img.filename || 'unknown'}
                            {img.page != null && <span className="text-[var(--text-muted)]"> · page {img.page}</span>}
                          </p>
                          {img.description && (
                            <p className="text-[11px] text-[var(--text-muted)] truncate max-w-[350px]">{img.description}</p>
                          )}
                        </div>
                      </div>
                      <div className="flex items-center gap-2 shrink-0">
                        {img.sent_in_final === true && (
                          <Badge variant="success" className="text-[9px]">Sent to LLM</Badge>
                        )}
                        {img.sent_in_final === false && (
                          <Badge variant="warning" className="text-[9px]">Transcript Only</Badge>
                        )}
                        {img.substantive != null && (
                          img.substantive ? (
                            <Badge variant="default" className="text-[9px]">Has Content</Badge>
                          ) : (
                            <Badge variant="default" className="text-[9px]">Blank/Decorative</Badge>
                          )
                        )}
                        {img.size_bytes && (
                          <span className="text-[10px] text-[var(--text-muted)]">{fmtBytes(img.size_bytes)}</span>
                        )}
                        {img.confidence && (
                          <span className={cn(
                            'text-[10px]',
                            img.confidence === 'high' ? 'text-emerald-400' : img.confidence === 'medium' ? 'text-amber-400' : 'text-rose-400'
                          )}>{img.confidence}</span>
                        )}
                      </div>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {/* Vision pre-analysis notes */}
            {visionPre.consolidation?.skipped ? (
              <div className="flex items-center gap-2 text-xs text-emerald-400 bg-emerald-500/10 rounded-lg px-3 py-2">
                <CheckCircle2 className="h-3.5 w-3.5 shrink-0" />
                <span>
                  <strong>Full per-image transcripts</strong> were sent to the grader (consolidation was skipped).
                  Each image&apos;s transcript is included individually for maximum accuracy.
                </span>
              </div>
            ) : visionPre.notes_attached_to_grading && visionPre.notes_preview ? (
              <details className="text-xs">
                <summary className="cursor-pointer text-indigo-400 hover:underline font-medium">
                  Show vision transcription notes sent to grader
                </summary>
                <pre className="mt-2 bg-[var(--bg-secondary)] p-3 rounded-lg text-[11px] whitespace-pre-wrap max-h-48 overflow-y-auto text-[var(--text-muted)]">
                  {visionPre.notes_preview}
                </pre>
              </details>
            ) : visionPre.notes_attached_to_grading && !visionPre.notes_preview ? (
              <div className="flex items-center gap-2 text-xs text-[var(--text-muted)]">
                <Eye className="h-3.5 w-3.5 shrink-0" />
                <span>Vision transcription notes were attached to grading (preview not available).</span>
              </div>
            ) : null}
          </div>
        )}
      </Accordion>

      {/* ══════════════════════════════════════════════════════════
          SECTION 3: RUBRIC ↔ FILE EVIDENCE MAPPING (THE KEY SECTION)
         ══════════════════════════════════════════════════════════ */}
      <Accordion
        title="3. Rubric ↔ Evidence Mapping"
        icon={Target}
        subtitle={`${rubricBreakdown.length} criteria scored`}
        defaultOpen
      >
        {rubricBreakdown.length === 0 ? (
          <div className="text-xs text-[var(--text-muted)]">
            {wasBlocked
              ? 'No rubric evaluation was performed because the submission was blocked by the relevance gate.'
              : 'No rubric breakdown data available.'}
          </div>
        ) : (
          <div className="space-y-2">
            {rubricBreakdown.map((item, i) => (
              <CriterionMappingCard key={i} item={item} evidenceMap={evidenceMap} scoreVerificationDetails={scoreVerificationDetails} />
            ))}
          </div>
        )}
      </Accordion>

      {/* ══════════════════════════════════════════════════════════
          SECTION 3.5: SCORE VERIFICATION
         ══════════════════════════════════════════════════════════ */}
      {scoreVerification && (
        <Accordion
          title="3.5 Score Verification"
          icon={ShieldCheck}
          subtitle={
            scoreVerification.trace?.enabled
              ? `${scoreVerification.adjustments_applied ?? 0} adjustment(s) applied across ${scoreVerificationDetails.length} criteria`
              : 'Verification disabled'
          }
        >
          <div className="space-y-3 text-xs">
            <div className="grid grid-cols-2 md:grid-cols-3 gap-2">
              <StatBox label="Verification" value={scoreVerification.trace?.enabled ? 'Enabled' : 'Disabled'} />
              <StatBox label="Criteria Checked" value={scoreVerificationDetails.length} />
              <StatBox label="Adjustments Applied" value={scoreVerification.adjustments_applied ?? 0} />
            </div>

            {scoreVerificationDetails.length > 0 && (
              <div className="space-y-1.5">
                <p className="text-[10px] uppercase tracking-wider text-[var(--text-muted)] font-semibold">Per-Criterion Verification</p>
                {scoreVerificationDetails.map((detail, i) => {
                  const wasAdjusted = detail.applied === true
                  return (
                    <div
                      key={i}
                      className={cn(
                        'flex items-center justify-between rounded-md px-3 py-2',
                        wasAdjusted ? 'bg-amber-500/10 border border-amber-500/20' : 'bg-emerald-500/5 border border-emerald-500/15'
                      )}
                    >
                      <div className="flex items-center gap-2">
                        {wasAdjusted ? (
                          <AlertTriangle className="h-3.5 w-3.5 text-amber-400 shrink-0" />
                        ) : (
                          <CheckCircle2 className="h-3.5 w-3.5 text-emerald-400 shrink-0" />
                        )}
                        <span className="font-medium text-[var(--text-primary)]">{detail.criterion}</span>
                      </div>
                      <div className="flex items-center gap-3 shrink-0">
                        {wasAdjusted ? (
                          <span className="text-amber-400 font-mono font-bold">
                            {detail.original} &rarr; {detail.verified}
                          </span>
                        ) : (
                          <span className="text-emerald-400 font-mono font-bold">{detail.verified}</span>
                        )}
                        <Badge
                          variant={detail.confidence === 'high' ? 'success' : detail.confidence === 'medium' ? 'warning' : 'error'}
                          className="text-[9px]"
                        >
                          {detail.confidence}
                        </Badge>
                      </div>
                    </div>
                  )
                })}
                {scoreVerificationDetails.some((d) => d.reason) && (
                  <details className="mt-2">
                    <summary className="cursor-pointer text-indigo-400 hover:underline font-medium text-xs">
                      Show verification reasons
                    </summary>
                    <div className="mt-2 space-y-1.5">
                      {scoreVerificationDetails.filter((d) => d.reason).map((d, i) => (
                        <div key={i} className="rounded-md bg-[var(--bg-secondary)] px-3 py-2 text-[11px]">
                          <span className="font-medium text-[var(--text-primary)]">{d.criterion}:</span>{' '}
                          <span className="text-[var(--text-muted)]">{d.reason}</span>
                        </div>
                      ))}
                    </div>
                  </details>
                )}
              </div>
            )}
          </div>
        </Accordion>
      )}

      {/* ══════════════════════════════════════════════════════════
          SECTION 4: LLM GRADING CALL
         ══════════════════════════════════════════════════════════ */}
      <Accordion
        title="4. LLM Grading Call"
        icon={Cpu}
        subtitle={llmCall.model ? `${llmCall.model} via ${llmCall.provider}` : 'No LLM call recorded'}
      >
        {!llmCall.model ? (
          <p className="text-xs text-[var(--text-muted)]">
            {wasBlocked ? 'No LLM call was made because the submission was blocked.' : 'No call metadata available.'}
          </p>
        ) : (
          <div className="space-y-3 text-xs">
            <div className="grid grid-cols-2 md:grid-cols-3 gap-2">
              <StatBox label="Provider" value={llmCall.provider} />
              <StatBox label="Model" value={llmCall.model} />
              <StatBox label="Temperature" value={llmCall.temperature ?? '0.0'} />
              <StatBox label="Max Tokens" value={llmCall.max_tokens} />
              <StatBox label="Seed" value={llmCall.seed ?? 42} />
              <StatBox label="Fallback Used" value={llmCall.fallback_used ? 'Yes ⚠' : 'No'} />
            </div>

            {llmCall.usage && (
              <div className="rounded-lg bg-[var(--bg-secondary)] p-3">
                <p className="font-semibold text-[var(--text-primary)] mb-2">Token Usage</p>
                <div className="grid grid-cols-3 gap-2">
                  <StatBox label="Prompt" value={fmt(llmCall.usage.prompt_tokens)} />
                  <StatBox label="Completion" value={fmt(llmCall.usage.completion_tokens)} />
                  <StatBox label="Total" value={fmt(llmCall.usage.total_tokens)} />
                </div>
              </div>
            )}

            {llmCall.json_repaired && (
              <div className="flex items-center gap-2 text-amber-400">
                <AlertTriangle className="h-3 w-3" />
                JSON response was malformed and had to be repaired (attempt {llmCall.json_repair_attempt})
              </div>
            )}

            {llmCall.fallback_used && llmCall.consistency_note && (
              <div className="flex items-center gap-2 text-amber-400">
                <AlertTriangle className="h-3 w-3" />
                {llmCall.consistency_note}
              </div>
            )}

            {multiPass && (
              <div className="rounded-lg border border-indigo-500/20 bg-indigo-500/5 p-3">
                <p className="font-semibold text-indigo-400 mb-2 flex items-center gap-2">
                  <Layers className="h-3.5 w-3.5" /> Multi-Pass Grading
                </p>
                <div className="grid grid-cols-2 md:grid-cols-4 gap-2 mb-2">
                  <StatBox label="Windows" value={multiPass.total_windows} />
                  <StatBox label="Window Size" value={fmtK(multiPass.window_size)} />
                  <StatBox label="Overlap" value={fmtK(multiPass.overlap)} />
                  <div className="rounded-md bg-[var(--bg-secondary)] px-3 py-2 text-center group relative">
                    <p className="text-[9px] text-[var(--text-muted)] uppercase tracking-wider">Aggregation</p>
                    <p className="text-xs font-bold text-[var(--text-primary)]">
                      {multiPass.aggregation === 'evidence_weighted' ? (
                        <span className="text-indigo-400 cursor-help" title="Scores are weighted by the strength and quantity of supporting evidence from each pass, rather than simply taking the maximum.">
                          evidence_weighted
                        </span>
                      ) : (
                        multiPass.aggregation || 'max_per_criterion'
                      )}
                    </p>
                  </div>
                </div>
                {(multiPass.disagreements ?? 0) > 0 && (
                  <div className="flex items-center gap-2 text-xs text-amber-400 bg-amber-500/10 rounded-lg px-3 py-1.5 mb-2">
                    <AlertTriangle className="h-3 w-3 shrink-0" />
                    <span>
                      <strong>{multiPass.disagreements}</strong> cross-pass disagreement(s) flagged during aggregation.
                    </span>
                  </div>
                )}
                {multiPass.passes && (multiPass.passes as R[]).map((pass, pi) => (
                  <div
                    key={pi}
                    className={cn(
                      'rounded px-3 py-1.5 mt-1',
                      pass.error ? 'bg-rose-500/10' : 'bg-[var(--bg-secondary)]'
                    )}
                  >
                    <span className="font-medium text-[var(--text-primary)]">
                      Window {pass.pass_id}/{multiPass.total_windows}:
                    </span>
                    {pass.error ? (
                      <span className="ml-2 text-rose-400">{pass.error}</span>
                    ) : (
                      <span className="ml-2 text-[var(--text-muted)]">
                        {fmtK(pass.text_chars)} chars, {pass.images_sent} images
                      </span>
                    )}
                  </div>
                ))}
              </div>
            )}

            {transparency.prompt_preview && (
              <details className="text-xs">
                <summary className="cursor-pointer text-indigo-400 hover:underline font-medium">
                  Show prompt preview (first 500 chars)
                </summary>
                <pre className="mt-2 bg-[var(--bg-secondary)] p-3 rounded-lg text-[11px] whitespace-pre-wrap max-h-48 overflow-y-auto text-[var(--text-muted)]">
                  {transparency.prompt_preview}
                </pre>
              </details>
            )}
          </div>
        )}
      </Accordion>

      {/* ══════════════════════════════════════════════════════════
          SECTION 5: INTEGRITY
         ══════════════════════════════════════════════════════════ */}
      {aiResult.grading_hash && (
        <div className="flex items-center justify-center gap-3 text-[11px] text-[var(--text-muted)] py-2">
          <Search className="h-3.5 w-3.5" />
          <span>Grading Hash: <code className="font-mono bg-[var(--bg-secondary)] px-1.5 py-0.5 rounded">{aiResult.grading_hash}</code></span>
          {llmCall.timestamp_utc && (
            <span>· Graded: {new Date(llmCall.timestamp_utc).toLocaleString()}</span>
          )}
        </div>
      )}
    </div>
  )
}

// ══════════════════════════════════════════════════════════════════
// CRITERION MAPPING CARD — The main evidence view per rubric item
// ══════════════════════════════════════════════════════════════════

function CriterionMappingCard({ item, evidenceMap, scoreVerificationDetails }: { item: R; evidenceMap: R[]; scoreVerificationDetails: R[] }) {
  const [expanded, setExpanded] = useState(false)
  const score = item.score ?? 0
  const max = item.max ?? 0
  const pct = max > 0 ? Math.round((score / max) * 100) : 0
  const citations = (item.citations ?? []) as R[]

  // Find verification detail for this criterion
  const verificationDetail = scoreVerificationDetails.find(
    (d) => d.criterion?.toLowerCase() === item.criterion?.toLowerCase()
  )
  const wasVerified = !!verificationDetail
  const wasAdjusted = verificationDetail?.applied === true

  // Classify citations
  const imageCitations = citations.filter(c => c.type === 'image' || c.image_id)
  const textCitations = citations.filter(c =>
    c.type === 'text' || c.snippet_id || (!c.image_id && (c.source || c.file || c.description))
  )
  const totalEvidence = imageCitations.length + textCitations.length

  // Match image citations to evidence map entries for richer detail
  const imageEvidence = imageCitations.map(c => {
    const ev = evidenceMap.find(e => e.image_id === c.image_id)
    return { citation: c, evidence: ev }
  })

  return (
    <div className="rounded-lg border border-[var(--border)] overflow-hidden">
      <button
        onClick={() => setExpanded(!expanded)}
        className="w-full flex items-center gap-3 px-4 py-3 hover:bg-[var(--border)]/20 transition-colors"
      >
        {/* Score badge */}
        <div className={cn(
          'w-12 h-8 rounded-md flex items-center justify-center text-xs font-bold',
          pct >= 80 ? 'bg-emerald-500/20 text-emerald-400' :
          pct >= 50 ? 'bg-amber-500/20 text-amber-400' :
          'bg-rose-500/20 text-rose-400'
        )}>
          {score}/{max}
        </div>

        {/* Criterion name */}
        <div className="flex-1 text-left">
          <p className="text-sm font-medium text-[var(--text-primary)]">{item.criterion}</p>
        </div>

        {/* Evidence indicators + verification badge */}
        <div className="flex items-center gap-2 shrink-0">
          {wasVerified && (
            <Badge
              variant={wasAdjusted ? 'warning' : 'success'}
              className="text-[9px] gap-0.5"
            >
              <ShieldCheck className="h-2.5 w-2.5" />
              {wasAdjusted ? `${verificationDetail.original}→${verificationDetail.verified}` : 'Verified'}
            </Badge>
          )}
          {totalEvidence === 0 ? (
            <span className="text-[10px] text-[var(--text-muted)]">no citations</span>
          ) : (
            <>
              {imageCitations.length > 0 && (
                <Badge variant="default" className="text-[9px] gap-0.5">
                  <Image className="h-2.5 w-2.5" />{imageCitations.length}
                </Badge>
              )}
              {textCitations.length > 0 && (
                <Badge variant="default" className="text-[9px] gap-0.5">
                  <FileText className="h-2.5 w-2.5" />{textCitations.length}
                </Badge>
              )}
            </>
          )}
          {expanded
            ? <ChevronDown className="h-3.5 w-3.5 text-[var(--text-muted)]" />
            : <ChevronRight className="h-3.5 w-3.5 text-[var(--text-muted)]" />}
        </div>
      </button>

      {expanded && (
        <div className="px-4 pb-4 pt-1 border-t border-[var(--border)] space-y-3">
          {/* Justification */}
          <div>
            <Label>AI Justification</Label>
            <p className="text-xs text-[var(--text-primary)] leading-relaxed">
              {item.justification || 'No justification was provided by the model.'}
            </p>
          </div>

          {/* Image Evidence */}
          {imageEvidence.length > 0 && (
            <div>
              <Label>Image Evidence ({imageEvidence.length} references)</Label>
              <div className="space-y-1.5">
                {imageEvidence.map(({ citation: c, evidence: ev }, i) => (
                  <div key={i} className="rounded-md bg-[var(--bg-secondary)] px-3 py-2">
                    <div className="flex items-center gap-2 text-xs">
                      <Image className="h-3 w-3 text-purple-400 shrink-0" />
                      <span className="font-mono text-indigo-400">{c.image_id || `img_${i+1}`}</span>
                      {(ev?.filename || c.file) && (
                        <span className="text-[var(--text-muted)]">{ev?.filename || c.file}</span>
                      )}
                      {(ev?.page ?? c.page) != null && (
                        <span className="text-[var(--text-muted)]">p{ev?.page ?? c.page}</span>
                      )}
                      {ev?.sent_in_final === true && <Badge variant="success" className="text-[9px]">sent to LLM</Badge>}
                      {ev?.sent_in_final === false && <Badge variant="warning" className="text-[9px]">transcript only</Badge>}
                      {ev?.substantive && <Badge variant="default" className="text-[9px]">substantive</Badge>}
                    </div>
                    {ev?.summary && (
                      <p className="text-[11px] text-[var(--text-muted)] mt-1 pl-5">{ev.summary}</p>
                    )}
                    {ev?.transcription && (
                      <p className="text-[11px] text-[var(--text-muted)] mt-0.5 pl-5 italic">&ldquo;{ev.transcription}&rdquo;</p>
                    )}
                    {c.description && !ev?.summary && (
                      <p className="text-[11px] text-[var(--text-muted)] mt-1 pl-5">{c.description}</p>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Text Evidence */}
          {textCitations.length > 0 && (
            <div>
              <Label>Text Evidence ({textCitations.length} references)</Label>
              <div className="space-y-1.5">
                {textCitations.map((c, i) => (
                  <div key={i} className="rounded-md bg-[var(--bg-secondary)] px-3 py-2 text-xs">
                    <div className="flex items-center gap-2">
                      <FileText className="h-3 w-3 text-blue-400 shrink-0" />
                      {c.file && <span className="text-[var(--text-primary)]">{c.file}</span>}
                      {c.source && <span className="text-[var(--text-muted)]">({c.source})</span>}
                      {c.page && <span className="text-[var(--text-muted)]">p{c.page}</span>}
                    </div>
                    {c.description && (
                      <p className="text-[11px] text-[var(--text-muted)] mt-1 pl-5">{c.description}</p>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* No evidence at all */}
          {totalEvidence === 0 && (
            <div className="text-xs text-[var(--text-muted)] italic flex items-center gap-2">
              <AlertTriangle className="h-3 w-3 text-amber-400" />
              No file/image citations were attached to this criterion. The score is based on text-level assessment only.
            </div>
          )}
        </div>
      )}
    </div>
  )
}

// ══════════════════════════════════════════════════════════════════
// HELPER COMPONENTS
// ══════════════════════════════════════════════════════════════════

function Accordion({
  title,
  icon: Icon,
  subtitle,
  children,
  defaultOpen = false,
}: {
  title: string
  icon: React.ElementType
  subtitle?: string
  children: React.ReactNode
  defaultOpen?: boolean
}) {
  const [open, setOpen] = useState(defaultOpen)
  return (
    <Card>
      <button
        onClick={() => setOpen(!open)}
        className="w-full flex items-center justify-between px-5 py-3.5 hover:bg-[var(--border)]/20 transition-colors"
      >
        <div className="flex items-center gap-2.5">
          <Icon className="h-4 w-4 text-indigo-400" />
          <span className="text-sm font-bold text-[var(--text-primary)]">{title}</span>
          {subtitle && (
            <span className="text-[11px] text-[var(--text-muted)] hidden md:inline">— {subtitle}</span>
          )}
        </div>
        {open ? <ChevronDown className="h-4 w-4 text-[var(--text-muted)]" /> : <ChevronRight className="h-4 w-4 text-[var(--text-muted)]" />}
      </button>
      {open && <CardContent className="px-5 pb-5 pt-0">{children}</CardContent>}
    </Card>
  )
}

function PipelineStep({ label, value, ok }: { label: string; value: string | number; ok: boolean }) {
  return (
    <div className={cn(
      'rounded-md px-2.5 py-1.5 text-center min-w-[70px] border',
      ok ? 'bg-emerald-500/10 border-emerald-500/20' : 'bg-rose-500/10 border-rose-500/20'
    )}>
      <p className="text-[9px] uppercase tracking-wider text-[var(--text-muted)]">{label}</p>
      <p className={cn('text-xs font-bold', ok ? 'text-emerald-400' : 'text-rose-400')}>
        {value}
      </p>
    </div>
  )
}

function StatBox({ label, value }: { label: string; value: string | number | undefined }) {
  return (
    <div className="rounded-md bg-[var(--bg-secondary)] px-3 py-2 text-center">
      <p className="text-[9px] text-[var(--text-muted)] uppercase tracking-wider">{label}</p>
      <p className="text-xs font-bold text-[var(--text-primary)]">{value ?? '—'}</p>
    </div>
  )
}

function Label({ children }: { children: React.ReactNode }) {
  return (
    <p className="text-[10px] uppercase tracking-wider text-[var(--text-muted)] font-semibold mb-1.5">{children}</p>
  )
}

function fmt(n: number | string | undefined): string {
  if (n == null) return '—'
  const num = typeof n === 'string' ? parseInt(n, 10) : n
  if (isNaN(num)) return String(n)
  return num.toLocaleString()
}

function fmtK(n: number | string | undefined): string {
  if (n == null) return '—'
  const num = typeof n === 'string' ? parseInt(n, 10) : n
  if (isNaN(num)) return String(n)
  if (num >= 1000) return `${(num / 1000).toFixed(1)}K`
  return String(num)
}

function fmtBytes(bytes: number | undefined): string {
  if (!bytes) return ''
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}
