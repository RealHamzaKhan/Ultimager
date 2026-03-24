import { describe, it, expect, beforeEach } from 'vitest'
import { useUIStore } from '@/stores/ui-store'

describe('useUIStore', () => {
  beforeEach(() => {
    useUIStore.setState({
      sidebarOpen: true,
      theme: 'dark',
      activeSessionId: null,
      selectedStudents: new Set(),
      gradingFilter: 'all',
      tableSortColumn: null,
      tableSortDirection: null,
    })
  })

  it('initial state is correct', () => {
    const state = useUIStore.getState()
    expect(state.sidebarOpen).toBe(true)
    expect(state.theme).toBe('dark')
    expect(state.activeSessionId).toBeNull()
    expect(state.selectedStudents.size).toBe(0)
  })

  it('setSidebarOpen toggles correctly', () => {
    useUIStore.getState().setSidebarOpen(false)
    expect(useUIStore.getState().sidebarOpen).toBe(false)
    useUIStore.getState().setSidebarOpen(true)
    expect(useUIStore.getState().sidebarOpen).toBe(true)
  })

  it('toggleSidebar works', () => {
    useUIStore.getState().toggleSidebar()
    expect(useUIStore.getState().sidebarOpen).toBe(false)
    useUIStore.getState().toggleSidebar()
    expect(useUIStore.getState().sidebarOpen).toBe(true)
  })

  it('setTheme updates theme', () => {
    useUIStore.getState().setTheme('light')
    expect(useUIStore.getState().theme).toBe('light')
  })

  it('setActiveSession sets id', () => {
    useUIStore.getState().setActiveSession(42)
    expect(useUIStore.getState().activeSessionId).toBe(42)
  })

  it('toggleStudentSelection adds/removes', () => {
    useUIStore.getState().toggleStudentSelection(1)
    expect(useUIStore.getState().selectedStudents.has(1)).toBe(true)
    useUIStore.getState().toggleStudentSelection(1)
    expect(useUIStore.getState().selectedStudents.has(1)).toBe(false)
  })

  it('clearStudentSelection empties Set', () => {
    useUIStore.getState().toggleStudentSelection(1)
    useUIStore.getState().toggleStudentSelection(2)
    useUIStore.getState().clearStudentSelection()
    expect(useUIStore.getState().selectedStudents.size).toBe(0)
  })

  it('selectAllStudents sets all ids', () => {
    useUIStore.getState().selectAllStudents([1, 2, 3])
    expect(useUIStore.getState().selectedStudents.size).toBe(3)
  })

  it('setGradingFilter updates filter', () => {
    useUIStore.getState().setGradingFilter('flagged')
    expect(useUIStore.getState().gradingFilter).toBe('flagged')
  })

  it('setTableSort cycles correctly', () => {
    const { setTableSort } = useUIStore.getState()
    setTableSort('score')
    expect(useUIStore.getState().tableSortColumn).toBe('score')
    expect(useUIStore.getState().tableSortDirection).toBe('asc')

    setTableSort('score')
    expect(useUIStore.getState().tableSortDirection).toBe('desc')

    setTableSort('score')
    expect(useUIStore.getState().tableSortColumn).toBeNull()
    expect(useUIStore.getState().tableSortDirection).toBeNull()
  })

  it('setTableSort new column resets to asc', () => {
    useUIStore.getState().setTableSort('score')
    useUIStore.getState().setTableSort('name')
    expect(useUIStore.getState().tableSortColumn).toBe('name')
    expect(useUIStore.getState().tableSortDirection).toBe('asc')
  })
})
