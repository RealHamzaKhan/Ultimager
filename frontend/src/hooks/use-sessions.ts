import { useQuery } from '@tanstack/react-query'
import { fetchSessions } from '@/lib/api'
import type { SessionListResponse } from '@/lib/types'

export const SESSIONS_QUERY_KEY = ['sessions'] as const

export function useSessions() {
  const query = useQuery<SessionListResponse, Error>({
    queryKey: SESSIONS_QUERY_KEY,
    queryFn: fetchSessions,
  })

  return {
    sessions: query.data?.sessions ?? [],
    isLoading: query.isLoading,
    isError: query.isError,
    error: query.error,
    refetch: query.refetch,
  }
}
