export const GRADE_THRESHOLDS = [
  { min: 97, grade: 'A+' },
  { min: 93, grade: 'A' },
  { min: 90, grade: 'A-' },
  { min: 87, grade: 'B+' },
  { min: 83, grade: 'B' },
  { min: 80, grade: 'B-' },
  { min: 77, grade: 'C+' },
  { min: 73, grade: 'C' },
  { min: 70, grade: 'C-' },
  { min: 67, grade: 'D+' },
  { min: 60, grade: 'D' },
  { min: 0, grade: 'F' },
] as const

export const GRADE_COLORS: Record<string, string> = {
  'A+': 'var(--color-success)',
  A: 'var(--color-success)',
  'A-': 'var(--color-success)',
  'B+': 'var(--color-info)',
  B: 'var(--color-info)',
  'B-': 'var(--color-info)',
  'C+': 'var(--color-warning)',
  C: 'var(--color-warning)',
  'C-': 'var(--color-warning)',
  'D+': 'var(--color-error)',
  D: 'var(--color-error)',
  F: 'var(--color-error)',
}

export const CONFIDENCE_THRESHOLDS = {
  high: { min: 0.8, color: 'var(--color-success)', label: 'High' },
  medium: { min: 0.5, color: 'var(--color-warning)', label: 'Medium' },
  low: { min: 0, color: 'var(--color-error)', label: 'Low' },
} as const

export const POLLING_INTERVAL_MS = 3000
export const SSE_RETRY_DELAYS = [1000, 2000, 4000, 8000]
export const MAX_FILE_SIZE_MB = 50
export const SUPPORTED_FILE_TYPES = ['.zip', '.tar', '.tar.gz', '.7z', '.rar']
export const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000'
