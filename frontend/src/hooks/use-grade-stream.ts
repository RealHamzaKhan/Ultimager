import { useEffect, useRef, useCallback, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { API_BASE_URL, SSE_RETRY_DELAYS } from '@/lib/constants'
import { sessionQueryKey } from '@/hooks/use-session'
import type { Submission } from '@/lib/types'

const MAX_RETRIES = 5

export interface GradedStudent {
  id: number
  name: string
  score: number | null
  grade: string
  confidence: string
  status: 'graded' | 'error'
  errorMessage?: string
  timestamp: number
}

export interface GradeStreamState {
  connected: boolean
  graded: number
  failed: number
  total: number
  currentStudent: string | null
  stage: string | null
  error: string | null
  isComplete: boolean
  isStopped: boolean
  completedStudents: GradedStudent[]
  /** Elapsed time in seconds since grading started */
  elapsed: number
}

function submissionToGradedStudent(s: Submission): GradedStudent {
  const score = s.override_score ?? s.ai_score ?? null
  return {
    id: s.id,
    name: s.student_identifier,
    score,
    grade: s.ai_letter_grade ?? '',
    confidence: s.ai_confidence ?? '',
    status: s.status === 'error' ? 'error' : 'graded',
    errorMessage: s.error_message ?? undefined,
    timestamp: s.graded_at ? new Date(s.graded_at).getTime() : Date.now(),
  }
}

export interface UseGradeStreamOptions {
  /** Total students from REST API — used as fallback until SSE sends `total` */
  sessionTotal?: number
  /** Already-graded students from REST API — seeds the live feed on late join */
  existingStudents?: Submission[]
}

function buildInitialState(opts: UseGradeStreamOptions = {}): GradeStreamState {
  const { sessionTotal = 0, existingStudents = [] } = opts

  // Seed the completed-students feed with students already graded before we connected
  const seeded: GradedStudent[] = existingStudents
    .filter((s) => s.status === 'graded' || s.status === 'error')
    .map(submissionToGradedStudent)
    // Sort newest-first so the list matches the live-feed order
    .sort((a, b) => b.timestamp - a.timestamp)
    .slice(0, 50)

  const graded = existingStudents.filter((s) => s.status === 'graded').length
  const failed = existingStudents.filter((s) => s.status === 'error').length

  return {
    connected: false,
    graded,
    failed,
    total: sessionTotal,
    currentStudent: null,
    stage: null,
    error: null,
    isComplete: false,
    isStopped: false,
    completedStudents: seeded,
    elapsed: 0,
  }
}

export function useGradeStream(
  sessionId: number,
  enabled = true,
  opts: UseGradeStreamOptions = {},
) {
  const [state, setState] = useState<GradeStreamState>(() => buildInitialState(opts))
  const eventSourceRef = useRef<EventSource | null>(null)
  const retriesRef = useRef(0)
  const startTimeRef = useRef<number>(Date.now())
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const queryClient = useQueryClient()

  // Re-seed state when existing students load in (handles the case where the
  // students query resolves after the hook is first mounted with an empty list)
  const prevExistingRef = useRef<Submission[]>(opts.existingStudents ?? [])
  useEffect(() => {
    const prev = prevExistingRef.current
    const next = opts.existingStudents ?? []
    if (next.length !== prev.length) {
      prevExistingRef.current = next
      // Only seed if we haven't received SSE state yet (stream not delivering data)
      setState((cur) => {
        if (cur.connected) return cur  // SSE is live, don't overwrite
        const seeded = next
          .filter((s) => s.status === 'graded' || s.status === 'error')
          .map(submissionToGradedStudent)
          .sort((a, b) => b.timestamp - a.timestamp)
          .slice(0, 50)
        return {
          ...cur,
          graded: Math.max(cur.graded, next.filter(s => s.status === 'graded').length),
          failed: Math.max(cur.failed, next.filter(s => s.status === 'error').length),
          total: cur.total > 0 ? cur.total : (opts.sessionTotal ?? cur.total),
          completedStudents: cur.completedStudents.length > 0 ? cur.completedStudents : seeded,
        }
      })
    }
  }, [opts.existingStudents, opts.sessionTotal])

  // Update total from session when SSE hasn't provided one yet
  useEffect(() => {
    if (!opts.sessionTotal) return
    setState((cur) => cur.total > 0 ? cur : { ...cur, total: opts.sessionTotal! })
  }, [opts.sessionTotal])

  // Elapsed time timer
  useEffect(() => {
    if (enabled && !state.isComplete && !state.isStopped) {
      startTimeRef.current = Date.now()
      timerRef.current = setInterval(() => {
        setState((prev) => ({
          ...prev,
          elapsed: Math.floor((Date.now() - startTimeRef.current) / 1000),
        }))
      }, 1000)
    }
    return () => {
      if (timerRef.current) clearInterval(timerRef.current)
    }
  }, [enabled, state.isComplete, state.isStopped])

  const disconnect = useCallback(() => {
    if (eventSourceRef.current) {
      eventSourceRef.current.close()
      eventSourceRef.current = null
    }
  }, [])

  const connect = useCallback(() => {
    if (!enabled || sessionId <= 0) return

    disconnect()

    const url = `${API_BASE_URL}/session/${sessionId}/grade-stream`
    const es = new EventSource(url)
    eventSourceRef.current = es

    es.onopen = () => {
      retriesRef.current = 0
      setState((prev) => ({ ...prev, connected: true, error: null }))
    }

    es.onmessage = (event: MessageEvent) => {
      let data: Record<string, unknown>
      try {
        data = JSON.parse(event.data as string)
      } catch {
        return
      }

      const eventType = data.type as string

      switch (eventType) {

        // ── Late-join snapshot: sent immediately when SSE client connects
        //    mid-grading to populate the current counters ──────────────────
        case 'snapshot': {
          setState((prev) => ({
            ...prev,
            graded: (data.graded_count as number) ?? prev.graded,
            failed: (data.failed_count as number) ?? prev.failed,
            total: (data.total as number) || prev.total,
            currentStudent: (data.current_student as string) || prev.currentStudent,
            stage: (data.stage as string) || prev.stage,
          }))
          break
        }

        // ── Initial event from backend when grading session kicks off ─────
        case 'parallel_start': {
          setState((prev) => ({
            ...prev,
            total: (data.total as number) || prev.total,
            stage: (data.message as string) || prev.stage,
          }))
          break
        }

        case 'progress': {
          setState((prev) => ({
            ...prev,
            graded: (data.graded_count as number) ?? prev.graded,
            failed: (data.failed_count as number) ?? prev.failed,
            total: (data.total as number) || prev.total,
            currentStudent: (data.student as string) ?? prev.currentStudent,
            stage: (data.stage as string) ?? prev.stage,
          }))
          break
        }

        case 'student_graded':
        case 'student_complete': {
          const student: GradedStudent = {
            id: (data.student_id as number) ?? 0,
            name: (data.student as string) ?? (data.student_identifier as string) ?? '',
            score: (data.score as number) ?? (data.ai_score as number) ?? null,
            grade: (data.grade as string) ?? (data.letter_grade as string) ?? '',
            confidence: (data.confidence as string) ?? '',
            status: 'graded',
            timestamp: Date.now(),
          }
          setState((prev) => ({
            ...prev,
            graded: (data.graded_count as number) ?? prev.graded + 1,
            failed: (data.failed_count as number) ?? prev.failed,
            total: (data.total as number) || prev.total,
            currentStudent: null,
            completedStudents: [student, ...prev.completedStudents].slice(0, 50),
          }))
          queryClient.invalidateQueries({ queryKey: ['students', sessionId] })
          queryClient.invalidateQueries({ queryKey: sessionQueryKey(sessionId) })
          break
        }

        case 'student_error': {
          const errStudent: GradedStudent = {
            id: (data.student_id as number) ?? 0,
            name: (data.student as string) ?? (data.student_identifier as string) ?? '',
            score: null,
            grade: '',
            confidence: '',
            status: 'error',
            errorMessage: (data.error as string) ?? 'Grading failed',
            timestamp: Date.now(),
          }
          setState((prev) => ({
            ...prev,
            failed: (data.failed_count as number) ?? prev.failed + 1,
            total: (data.total as number) || prev.total,
            completedStudents: [errStudent, ...prev.completedStudents].slice(0, 50),
          }))
          queryClient.invalidateQueries({ queryKey: ['students', sessionId] })
          queryClient.invalidateQueries({ queryKey: sessionQueryKey(sessionId) })
          break
        }

        case 'ingestion_complete': {
          setState((prev) => ({
            ...prev,
            stage: `Grading ${data.student_identifier as string}…`,
            currentStudent: (data.student_identifier as string) ?? prev.currentStudent,
          }))
          break
        }

        case 'complete': {
          if (timerRef.current) clearInterval(timerRef.current)
          setState((prev) => ({
            ...prev,
            isComplete: true,
            connected: false,
            graded: (data.graded_count as number) ?? prev.graded,
            failed: (data.failed_count as number) ?? prev.failed,
            total: (data.total as number) || prev.total,
            currentStudent: null,
            stage: 'Complete',
          }))
          queryClient.invalidateQueries({ queryKey: sessionQueryKey(sessionId) })
          queryClient.invalidateQueries({ queryKey: ['students', sessionId] })
          disconnect()
          break
        }

        case 'stopped': {
          if (timerRef.current) clearInterval(timerRef.current)
          setState((prev) => ({
            ...prev,
            isStopped: true,
            connected: false,
            currentStudent: null,
          }))
          queryClient.invalidateQueries({ queryKey: sessionQueryKey(sessionId) })
          disconnect()
          break
        }

        case 'error': {
          setState((prev) => ({
            ...prev,
            error: (data.message as string) ?? 'Unknown error',
          }))
          break
        }
      }
    }

    es.onerror = () => {
      es.close()
      eventSourceRef.current = null
      setState((prev) => ({ ...prev, connected: false }))

      if (retriesRef.current < MAX_RETRIES) {
        const delay =
          SSE_RETRY_DELAYS[retriesRef.current] ??
          SSE_RETRY_DELAYS[SSE_RETRY_DELAYS.length - 1]
        retriesRef.current += 1
        setTimeout(() => connect(), delay)
      } else {
        setState((prev) => ({
          ...prev,
          error: 'Stream connection lost. Refresh the page to reconnect.',
        }))
      }
    }
  }, [sessionId, enabled, disconnect, queryClient])

  useEffect(() => {
    connect()
    return () => disconnect()
  }, [connect, disconnect])

  return {
    ...state,
    disconnect,
    reconnect: connect,
  }
}
