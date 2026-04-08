import { create } from 'zustand'

type Theme = 'light' | 'dark'
type SortDirection = 'asc' | 'desc' | null

interface UIState {
  // Sidebar
  sidebarOpen: boolean
  setSidebarOpen: (open: boolean) => void
  toggleSidebar: () => void

  // Theme
  theme: Theme
  setTheme: (theme: Theme) => void

  // Active session
  activeSessionId: number | null
  setActiveSession: (id: number | null) => void

  // Student selection
  selectedStudents: Set<number>
  toggleStudentSelection: (id: number) => void
  clearStudentSelection: () => void
  selectAllStudents: (ids: number[]) => void

  // Grading filter
  gradingFilter: string
  setGradingFilter: (filter: string) => void

  // Table sort
  tableSortColumn: string | null
  tableSortDirection: SortDirection
  setTableSort: (column: string) => void
}

export const useUIStore = create<UIState>((set) => ({
  sidebarOpen: true,
  setSidebarOpen: (open) => set({ sidebarOpen: open }),
  toggleSidebar: () => set((s) => ({ sidebarOpen: !s.sidebarOpen })),

  theme: 'dark',
  setTheme: (theme) => {
    if (typeof document !== 'undefined') {
      document.documentElement.setAttribute('data-theme', theme)
    }
    set({ theme })
  },

  activeSessionId: null,
  setActiveSession: (id) => set({ activeSessionId: id }),

  selectedStudents: new Set(),
  toggleStudentSelection: (id) =>
    set((s) => {
      const next = new Set(s.selectedStudents)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return { selectedStudents: next }
    }),
  clearStudentSelection: () => set({ selectedStudents: new Set() }),
  selectAllStudents: (ids) => set({ selectedStudents: new Set(ids) }),

  gradingFilter: 'all',
  setGradingFilter: (filter) => set({ gradingFilter: filter }),

  tableSortColumn: null,
  tableSortDirection: null,
  setTableSort: (column) =>
    set((s) => {
      if (s.tableSortColumn !== column) {
        return { tableSortColumn: column, tableSortDirection: 'asc' }
      }
      if (s.tableSortDirection === 'asc') {
        return { tableSortDirection: 'desc' }
      }
      if (s.tableSortDirection === 'desc') {
        return { tableSortColumn: null, tableSortDirection: null }
      }
      return { tableSortColumn: column, tableSortDirection: 'asc' }
    }),
}))
