import { useQuery } from '@tanstack/react-query'
import { fetchStudents } from '@/lib/api'
import type { Submission } from '@/lib/types'

export interface StudentFilters {
  status?: string
  search?: string
  sortBy?: string
  sortDir?: 'asc' | 'desc'
}

export function studentsQueryKey(sessionId: number, filters?: StudentFilters) {
  return ['students', sessionId, filters ?? {}] as const
}

export function useStudents(sessionId: number, filters?: StudentFilters) {
  const query = useQuery<Submission[], Error>({
    queryKey: studentsQueryKey(sessionId, filters),
    queryFn: () => fetchStudents(sessionId),
    enabled: sessionId > 0,
    select: (data) => applyFilters(data, filters),
  })

  return {
    students: query.data ?? [],
    isLoading: query.isLoading,
    isError: query.isError,
    error: query.error,
    refetch: query.refetch,
  }
}

function applyFilters(
  students: Submission[],
  filters?: StudentFilters
): Submission[] {
  let result = [...students]

  if (filters?.status && filters.status !== 'all') {
    result = result.filter((s) => s.status === filters.status)
  }

  if (filters?.search) {
    const term = filters.search.toLowerCase()
    result = result.filter((s) =>
      s.student_identifier.toLowerCase().includes(term)
    )
  }

  if (filters?.sortBy) {
    const dir = filters.sortDir === 'desc' ? -1 : 1
    result.sort((a, b) => {
      const aVal = a[filters.sortBy as keyof Submission]
      const bVal = b[filters.sortBy as keyof Submission]
      if (aVal == null && bVal == null) return 0
      if (aVal == null) return 1
      if (bVal == null) return -1
      if (aVal < bVal) return -1 * dir
      if (aVal > bVal) return 1 * dir
      return 0
    })
  }

  return result
}
