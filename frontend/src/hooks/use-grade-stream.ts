import { useEffect, useRef, useCallback, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { API_BASE_URL, SSE_RETRY_DELAYS } from '@/lib/constants'
import { sessionQueryKey } from '@/hooks/use-session'

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

const initialState: GradeStreamState = {
  connected: false,
  graded: 0,
  failed: 0,
  total: 0,
  currentStudent: null,
  stage: null,
  error: null,
  isComplete: false,
  isStopped: false,
  completedStudents: [],
  elapsed: 0,
}

export function useGradeStream(sessionId: number, enabled = true) {
  const [state, setState] = useState<GradeStreamState>(initialState)
  const eventSourceRef = useRef<EventSource | null>(null)
  const retriesRef = useRef(0)
  const startTimeRef = useRef<number>(Date.now())
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null)
  const queryClient = useQueryClient()

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
        case 'progress': {
          setState((prev) => ({
            ...prev,
            graded: (data.graded_count as number) ?? prev.graded,
            failed: (data.failed_count as number) ?? prev.failed,
            total: (data.total as number) ?? prev.total,
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
            completedStudents: [student, ...prev.completedStudents].slice(0, 50),
          }))
          queryClient.invalidateQueries({ queryKey: ['students', sessionId] })
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
            completedStudents: [errStudent, ...prev.completedStudents].slice(0, 50),
          }))
          queryClient.invalidateQueries({ queryKey: ['students', sessionId] })
          break
        }

        case 'ingestion_complete': {
          setState((prev) => ({
            ...prev,
            stage: `Grading ${data.student_identifier as string}...`,
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
            total: (data.total as number) ?? prev.total,
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
