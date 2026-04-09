import { API_BASE_URL } from './constants'
import type {
  Session,
  SessionListResponse,
  Submission,
  OverridePayload,
  ApiError,
  StudentFile,
  AnalyticsData,
  IngestionReport,
  ExtractedQuestion,
} from './types'

class ApiErrorInstance extends Error {
  code: string
  status: number

  constructor(message: string, code: string, status: number) {
    super(message)
    this.name = 'ApiError'
    this.code = code
    this.status = status
  }
}

export { ApiErrorInstance as ApiError }

async function request<T>(
  path: string,
  options: RequestInit = {},
  timeout = 30000
): Promise<T> {
  const controller = new AbortController()
  const timer = setTimeout(() => controller.abort(), timeout)

  try {
    const res = await fetch(`${API_BASE_URL}${path}`, {
      ...options,
      signal: controller.signal,
      headers: {
        'Content-Type': 'application/json',
        ...options.headers,
      },
    })

    if (!res.ok) {
      let errorBody: Partial<ApiError> = {}
      try {
        errorBody = await res.json()
      } catch {
        // ignore parse error
      }
      throw new ApiErrorInstance(
        errorBody.error || res.statusText,
        errorBody.code || `HTTP_${res.status}`,
        res.status
      )
    }

    return res.json() as Promise<T>
  } catch (err) {
    if (err instanceof ApiErrorInstance) throw err
    if (err instanceof DOMException && err.name === 'AbortError') {
      throw new ApiErrorInstance('Request timed out', 'TIMEOUT', 0)
    }
    throw new ApiErrorInstance(
      err instanceof Error ? err.message : 'Network error',
      'NETWORK_ERROR',
      0
    )
  } finally {
    clearTimeout(timer)
  }
}

// ── Sessions ────────────────────────────────────────────────────

export async function fetchSessions(): Promise<SessionListResponse> {
  return request<SessionListResponse>('/api/sessions')
}

export async function fetchSession(id: number): Promise<Session> {
  return request<Session>(`/session/${id}/status`)
}

export async function createSession(data: {
  title: string
  description: string
  rubric: string
  max_score: number
  reference_solution?: string
  checkpoints?: string
}): Promise<Session> {
  const formData = new URLSearchParams()
  formData.set('title', data.title)
  formData.set('description', data.description)
  formData.set('rubric', data.rubric)
  formData.set('max_score', String(data.max_score))
  if (data.reference_solution) {
    formData.set('reference_solution', data.reference_solution)
  }
  if (data.checkpoints) {
    formData.set('checkpoints', data.checkpoints)
  }

  const res = await fetch(`${API_BASE_URL}/session/new`, {
    method: 'POST',
    body: formData,
    redirect: 'manual',
  })

  // The backend redirects on success (303)
  if (res.type === 'opaqueredirect' || (res.status >= 300 && res.status < 400)) {
    // Try reading Location header (requires CORS expose_headers)
    const location = res.headers.get('location') || ''
    const match = location.match(/\/session\/(\d+)/)
    if (match) {
      return fetchSession(Number(match[1]))
    }
    // If Location header is unavailable (CORS), fetch sessions list and return the latest
    const result = await fetchSessions()
    if (result.sessions.length > 0) {
      // Return the most recently created session (highest id)
      const sorted = [...result.sessions].sort((a, b) => b.id - a.id)
      return sorted[0]
    }
    // Still nothing — return a stub so the router can navigate
    return { id: 0, title: data.title } as Session
  }

  if (!res.ok) {
    throw new ApiErrorInstance('Failed to create session', 'CREATE_SESSION_ERROR', res.status)
  }

  return res.json() as Promise<Session>
}

export async function deleteSession(id: number): Promise<void> {
  await fetch(`${API_BASE_URL}/session/${id}/delete`, { method: 'POST' })
}

// ── Submissions ─────────────────────────────────────────────────

export async function fetchStudents(sessionId: number): Promise<Submission[]> {
  return request<Submission[]>(`/api/session/${sessionId}/students`)
}

export async function fetchStudent(
  sessionId: number,
  studentId: number
): Promise<Submission> {
  // Get student from the students list (which includes full ai_result data)
  const students = await request<Submission[]>(`/api/session/${sessionId}/students`)
  const student = students.find((s) => s.id === studentId)
  if (!student) {
    throw new ApiErrorInstance('Student not found', 'NOT_FOUND', 404)
  }
  return student
}

export async function fetchStudentFiles(
  sessionId: number,
  studentId: number
): Promise<StudentFile[]> {
  return request<StudentFile[]>(`/session/${sessionId}/student/${studentId}/files`)
}

export async function fetchIngestionReport(
  sessionId: number,
  studentId: number
): Promise<IngestionReport | null> {
  try {
    return await request<IngestionReport>(
      `/session/${sessionId}/student/${studentId}/ingestion-report`
    )
  } catch {
    return null
  }
}

export function getFileViewUrl(
  sessionId: number,
  studentId: number,
  filePath: string
): string {
  const encoded = encodeURIComponent(filePath)
  return `${API_BASE_URL}/session/${sessionId}/student/${studentId}/file/${encoded}`
}

// ── Grading ─────────────────────────────────────────────────────

export async function startGrading(sessionId: number): Promise<void> {
  await fetch(`${API_BASE_URL}/session/${sessionId}/grade`, { method: 'POST' })
}

export async function stopGrading(sessionId: number): Promise<void> {
  await fetch(`${API_BASE_URL}/session/${sessionId}/stop-grading`, { method: 'POST' })
}

export async function regradeAll(sessionId: number): Promise<void> {
  await fetch(`${API_BASE_URL}/session/${sessionId}/regrade-all`, { method: 'POST' })
}

export async function regradeStudent(
  sessionId: number,
  studentId: number,
  force = false
): Promise<{ message: string; student_id: number }> {
  const params = force ? '?force=true' : ''
  const res = await fetch(
    `${API_BASE_URL}/session/${sessionId}/student/${studentId}/regrade${params}`,
    { method: 'POST' }
  )
  if (!res.ok) {
    const body = await res.json().catch(() => ({ message: res.statusText }))
    throw new ApiErrorInstance(
      body.message || body.detail || 'Regrade failed',
      'REGRADE_ERROR',
      res.status
    )
  }
  return res.json()
}

// ── Overrides & Flags ───────────────────────────────────────────

export async function overrideScore(
  sessionId: number,
  studentId: number,
  payload: OverridePayload
): Promise<void> {
  await request(`/session/${sessionId}/student/${studentId}/override`, {
    method: 'POST',
    body: JSON.stringify(payload),
  })
}

export async function flagStudent(
  sessionId: number,
  studentId: number,
  reason: string
): Promise<void> {
  const formData = new URLSearchParams()
  formData.set('reason', reason)

  await fetch(`${API_BASE_URL}/session/${sessionId}/student/${studentId}/flag`, {
    method: 'POST',
    body: formData,
  })
}

export async function unflagStudent(
  sessionId: number,
  studentId: number
): Promise<void> {
  await fetch(`${API_BASE_URL}/session/${sessionId}/student/${studentId}/unflag`, {
    method: 'POST',
  })
}

// ── Exports ─────────────────────────────────────────────────────

export async function exportCSV(sessionId: number): Promise<string> {
  const res = await fetch(`${API_BASE_URL}/session/${sessionId}/export/csv`)
  if (!res.ok) {
    throw new ApiErrorInstance('Export failed', 'EXPORT_ERROR', res.status)
  }
  return res.text()
}

export async function exportJSON(sessionId: number): Promise<string> {
  const res = await fetch(`${API_BASE_URL}/session/${sessionId}/export/json`)
  if (!res.ok) {
    throw new ApiErrorInstance('Export failed', 'EXPORT_ERROR', res.status)
  }
  return res.text()
}

// ── Upload ──────────────────────────────────────────────────────

export async function uploadSubmissions(
  sessionId: number,
  file: File,
  onProgress?: (percent: number) => void
): Promise<void> {
  const formData = new FormData()
  formData.append('zip_file', file)

  // Simple upload without XHR progress for now
  const res = await fetch(`${API_BASE_URL}/session/${sessionId}/upload`, {
    method: 'POST',
    body: formData,
  })

  onProgress?.(100)

  if (!res.ok) {
    throw new ApiErrorInstance('Upload failed', 'UPLOAD_ERROR', res.status)
  }
}

// ── Upload Single Student ────────────────────────────────────────

export async function uploadStudent(
  sessionId: number,
  studentName: string,
  files: File[]
): Promise<{ message: string; student_id: number; status: string; file_count: number }> {
  const formData = new FormData()
  formData.append('student_name', studentName)
  for (const file of files) {
    formData.append('files', file)
  }

  const res = await fetch(`${API_BASE_URL}/session/${sessionId}/upload-student`, {
    method: 'POST',
    body: formData,
  })

  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: res.statusText }))
    throw new ApiErrorInstance(
      body.detail || body.message || 'Upload failed',
      'UPLOAD_STUDENT_ERROR',
      res.status
    )
  }

  return res.json()
}

// ── AI Rubric Generation ────────────────────────────────────────

export interface GenerateRubricResponse {
  success: boolean
  rubric_text: string
  rubric_display?: string
  criteria: { criterion: string; max: number; description: string; question_id?: string }[]
  questions?: ExtractedQuestion[]
  checkpoints?: Record<string, unknown[]>
  strictness: string
  max_score: number
  reasoning: string
  quality_warnings: string[]
}

export async function generateRubric(
  description: string,
  maxScore = 100,
  strictness: 'balanced' | 'strict' | 'lenient' = 'balanced',
  detailLevel: 'simple' | 'balanced' | 'detailed' = 'balanced'
): Promise<GenerateRubricResponse> {
  const formData = new URLSearchParams()
  formData.set('description', description)
  formData.set('max_score', String(maxScore))
  formData.set('strictness', strictness)
  formData.set('detail_level', detailLevel)

  const res = await fetch(`${API_BASE_URL}/api/generate-rubric`, {
    method: 'POST',
    body: formData,
  })

  if (!res.ok) {
    throw new ApiErrorInstance('Rubric generation failed', 'GENERATE_RUBRIC_ERROR', res.status)
  }

  return res.json() as Promise<GenerateRubricResponse>
}

// ── Health ──────────────────────────────────────────────────────

export async function healthCheck(): Promise<{ status: string }> {
  return request<{ status: string }>('/health')
}
