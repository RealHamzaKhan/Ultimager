// ── Status Enums ────────────────────────────────────────────────

export type SessionStatus = 'pending' | 'uploading' | 'grading' | 'complete' | 'completed' | 'completed_with_errors' | 'error' | 'stopped' | 'paused'
export type SubmissionStatus = 'pending' | 'grading' | 'graded' | 'error' | 'skipped'
export type Confidence = 'high' | 'medium' | 'low'

// ── Core Entities ───────────────────────────────────────────────

export interface Session {
  id: number
  title: string
  description: string
  rubric: string
  max_score: number
  status: SessionStatus
  total_students: number
  graded_count: number
  error_count: number
  created_at: string
  started_at: string | null
  completed_at: string | null
}

export interface RubricCriteria {
  criterion: string
  max: number
  description?: string
  question_id?: string
}

export interface ExtractedQuestion {
  id: string
  label: string
  description: string
  marks: number | null
  marks_explicit: boolean
  parts?: ExtractedQuestion[]
}

export interface CheckpointResult {
  id: string
  description: string
  points: number
  pass: boolean
  verified: boolean
  evidence_quote: string
  source_file: string
  reasoning: string
  points_awarded: number
  flagged: boolean
  // Multi-agent partial credit fields
  score_percent?: number              // 0 | 25 | 50 | 75 | 100
  confidence?: 'high' | 'medium' | 'low'
  needs_review?: boolean
  flags?: string[]
  retry_count?: number
  verification_method?: string
  model_used?: string
  // Transparency flags
  judge_truncated?: boolean
  // Legacy fields
  evidence_tier?: 'green' | 'yellow' | 'orange' | 'red' | 'visual' | 'none'
  evidence_similarity?: number
}

export interface CriterionScore {
  criterion: string
  score: number
  max: number
  justification: string
  citations?: Citation[]
  checkpoints?: CheckpointResult[]
  flagged?: boolean
  flag_reasons?: string[]
  not_evaluated?: boolean     // True when no AI checkpoints were generated for this criterion
  score_capped?: boolean      // True when checkpoint points summed above criterion max
}

export interface Citation {
  file: string
  page?: number
  description?: string
}

export interface AIResult {
  rubric_breakdown: CriterionScore[]
  total_score: number
  max_score?: number
  percentage?: number
  letter_grade?: string
  overall_feedback: string
  strengths: string[]
  weaknesses: string[]
  critical_errors?: string[]
  suggestions_for_improvement: string
  confidence: Confidence
  confidence_reasoning: string
  grading_hash?: string
  transparency?: TransparencyData
  relevance?: number
  relevance_gate?: string
  images_processed?: number
  text_chars_processed?: number
  visual_content_analysis?: Record<string, unknown> | null
  question_mapping?: QuestionMapping[]
  file_analysis?: FileAnalysis[]
  flags?: string[]
  grading_method?: string
  verification_rate?: number
  checkpoint_stats?: {
    total: number
    verified: number
    hallucinated_and_retried?: number  // legacy
    flagged_criteria?: number           // legacy
    // Multi-agent fields
    retried?: number
    flagged?: number
    full_credit?: number
    partial_credit?: number
    no_credit?: number
    verification_rate?: number
  }
}

export interface QuestionMapping {
  question: string
  answer_location?: string
  file?: string
  page?: number
  score?: number
  feedback?: string
}

export interface FileAnalysis {
  filename: string
  type: string
  summary?: string
  relevance?: string
  key_findings?: string[]
}

export interface TransparencyData {
  llm_call: LLMCallInfo
  text_chars_sent: number
  images_sent: number
}

export interface LLMCallInfo {
  model: string
  provider: string
  usage: TokenUsage
  fallback_used: boolean
  consistency_alert?: boolean
}

export interface TokenUsage {
  prompt_tokens: number
  completion_tokens: number
  total_tokens: number
}

export interface Submission {
  id: number
  session_id: number
  student_identifier: string
  status: SubmissionStatus
  file_count: number
  ai_score: number | null
  ai_letter_grade: string
  ai_confidence: string
  final_score: number | null
  is_overridden: boolean
  override_score: number | null
  override_comments: string
  is_reviewed: boolean
  tests_passed: number
  tests_total: number
  graded_at: string | null
  error_message: string
  files: StudentFile[]
  ai_result: AIResult | null
  ai_feedback: string
  rubric_breakdown: CriterionScore[]
  strengths: string[]
  weaknesses: string[]
  critical_errors?: string[]
  suggestions_for_improvement: string
  confidence_reasoning?: string
  is_flagged: boolean
  flag_reason: string
  flagged_by?: string
  flagged_at?: string | null
  ingestion_report?: IngestionReport | null
  relevance_flags?: string[] | null
  judge_truncated?: boolean          // True when ≥1 checkpoint had content truncated to 28K chars
  routing_fallback_used?: boolean    // True when routing API failed for ≥1 batch
}

export interface StudentFile {
  filename: string
  relative_path?: string
  type: string
  display_type?: string
  size?: number
  extension?: string
  exists?: boolean
  view_url?: string | null
  content?: string
}

export interface IngestionReport {
  summary?: {
    received: number
    parsed: number
    failed: number
  }
  files_received?: number
  files_parsed?: number
  files_failed?: number
  warnings: string[]
  errors: string[]
  total_text_chars?: number
  total_images?: number
  content_truncated?: boolean
  timestamp?: string
}

// ── SSE Events ──────────────────────────────────────────────────

export type SSEEventType =
  | 'progress'
  | 'student_complete'
  | 'student_error'
  | 'complete'
  | 'stopped'
  | 'error'
  | 'heartbeat'

export interface SSEEvent {
  event: SSEEventType
  data: Record<string, unknown>
}

export interface GradingProgressEvent {
  graded: number
  total: number
  current_student?: string
  stage?: string
}

// ── API Types ───────────────────────────────────────────────────

export interface ApiError {
  error: string
  code: string
  detail?: Record<string, unknown>
}

export interface SessionListResponse {
  count: number
  sessions: Session[]
}

export interface OverridePayload {
  score: number
  comments?: string
  is_reviewed?: boolean
}

export interface FlagPayload {
  reason: string
}

// ── Analytics Types ─────────────────────────────────────────────

export interface AnalyticsData {
  total_students: number
  graded_count: number
  error_count: number
  average_score: number | null
  median_score: number | null
  pass_rate: number | null
  grade_distribution: Record<string, number>
  score_distribution: number[]
  flagged_count: number
}

export interface ScoreDistribution {
  bin: string
  count: number
}

export interface RubricHeatmapRow {
  criterion: string
  max: number
  average: number
  attainment: number
  students: { name: string; score: number }[]
}
