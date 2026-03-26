import { useMutation, useQueryClient } from '@tanstack/react-query'
import {
  createSession,
  uploadSubmissions,
  startGrading,
  stopGrading,
  regradeStudent,
  overrideScore,
  flagStudent,
  deleteSession,
} from '@/lib/api'
import { SESSIONS_QUERY_KEY } from '@/hooks/use-sessions'
import { sessionQueryKey } from '@/hooks/use-session'
import type { Session, OverridePayload } from '@/lib/types'

export function useCreateSession() {
  const queryClient = useQueryClient()

  return useMutation<
    Session,
    Error,
    { title: string; description: string; rubric: string; max_score: number }
  >({
    mutationFn: (data) => createSession(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: SESSIONS_QUERY_KEY })
    },
  })
}

export function useUploadSubmissions() {
  const queryClient = useQueryClient()

  return useMutation<void, Error, { sessionId: number; file: File }>({
    mutationFn: ({ sessionId, file }) => uploadSubmissions(sessionId, file),
    onSuccess: (_data, { sessionId }) => {
      queryClient.invalidateQueries({
        queryKey: sessionQueryKey(sessionId),
      })
      queryClient.invalidateQueries({
        queryKey: ['students', sessionId],
      })
    },
  })
}

export function useStartGrading() {
  const queryClient = useQueryClient()

  return useMutation<void, Error, { sessionId: number }>({
    mutationFn: ({ sessionId }) => startGrading(sessionId),
    onSuccess: (_data, { sessionId }) => {
      queryClient.invalidateQueries({
        queryKey: sessionQueryKey(sessionId),
      })
    },
  })
}

export function useStopGrading() {
  const queryClient = useQueryClient()

  return useMutation<void, Error, { sessionId: number }>({
    mutationFn: ({ sessionId }) => stopGrading(sessionId),
    onSuccess: (_data, { sessionId }) => {
      queryClient.invalidateQueries({
        queryKey: sessionQueryKey(sessionId),
      })
    },
  })
}

export function useRegradeStudent() {
  const queryClient = useQueryClient()

  return useMutation<{ message: string; student_id: number }, Error, { sessionId: number; studentId: number; force?: boolean }>({
    mutationFn: ({ sessionId, studentId, force }) =>
      regradeStudent(sessionId, studentId, force),
    onSuccess: (_data, { sessionId }) => {
      queryClient.invalidateQueries({
        queryKey: ['students', sessionId],
      })
      queryClient.invalidateQueries({
        queryKey: sessionQueryKey(sessionId),
      })
    },
  })
}

export function useOverrideScore() {
  const queryClient = useQueryClient()

  return useMutation<
    void,
    Error,
    { sessionId: number; studentId: number; payload: OverridePayload }
  >({
    mutationFn: ({ sessionId, studentId, payload }) =>
      overrideScore(sessionId, studentId, payload),
    onSuccess: (_data, { sessionId }) => {
      queryClient.invalidateQueries({
        queryKey: ['students', sessionId],
      })
    },
  })
}

export function useFlagStudent() {
  const queryClient = useQueryClient()

  return useMutation<
    void,
    Error,
    { sessionId: number; studentId: number; reason: string }
  >({
    mutationFn: ({ sessionId, studentId, reason }) =>
      flagStudent(sessionId, studentId, reason),
    onSuccess: (_data, { sessionId }) => {
      queryClient.invalidateQueries({
        queryKey: ['students', sessionId],
      })
    },
  })
}

export function useDeleteSession() {
  const queryClient = useQueryClient()

  return useMutation<void, Error, { sessionId: number }>({
    mutationFn: ({ sessionId }) => deleteSession(sessionId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: SESSIONS_QUERY_KEY })
    },
  })
}
