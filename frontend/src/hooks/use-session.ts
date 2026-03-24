import { useQuery } from '@tanstack/react-query'
import { fetchSession } from '@/lib/api'
import { POLLING_INTERVAL_MS } from '@/lib/constants'
import type { Session } from '@/lib/types'

export function sessionQueryKey(id: number) {
  return ['session', id] as const
}

export function useSession(id: number) {
  const query = useQuery<Session, Error>({
    queryKey: sessionQueryKey(id),
    queryFn: () => fetchSession(id),
    enabled: id > 0,
    refetchInterval: (query) => {
      const data = query.state.data
      if (data?.status === 'grading') {
        return POLLING_INTERVAL_MS
      }
      return false
    },
  })

  return {
    session: query.data ?? null,
    isLoading: query.isLoading,
    isError: query.isError,
    error: query.error,
    refetch: query.refetch,
  }
}
