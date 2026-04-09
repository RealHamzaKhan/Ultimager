import { type ClassValue, clsx } from 'clsx'
import { twMerge } from 'tailwind-merge'
import { GRADE_THRESHOLDS, GRADE_COLORS, CONFIDENCE_THRESHOLDS } from './constants'
import type { SSEEvent } from './types'

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

export function scoreToGrade(score: number | null | undefined, maxScore = 100): string {
  if (score === null || score === undefined || isNaN(score)) return '?'
  const percentage = maxScore > 0 ? (score / maxScore) * 100 : 0
  for (const threshold of GRADE_THRESHOLDS) {
    if (percentage >= threshold.min) return threshold.grade
  }
  return 'F'
}

export function gradeToColor(grade: string): string {
  return GRADE_COLORS[grade] || 'var(--color-muted)'
}

export function confidenceToColor(confidence: string): string {
  const config = CONFIDENCE_THRESHOLDS[confidence as keyof typeof CONFIDENCE_THRESHOLDS]
  return config?.color || 'var(--color-muted)'
}

export function confidenceToLabel(confidence: string): string {
  const config = CONFIDENCE_THRESHOLDS[confidence as keyof typeof CONFIDENCE_THRESHOLDS]
  return config?.label || 'Unknown'
}

export function formatScore(score: number | null | undefined): string {
  if (score === null || score === undefined) return '—'
  const rounded = Math.round(score * 10) / 10
  return rounded === Math.floor(rounded) ? String(Math.floor(rounded)) : String(rounded)
}

export function formatPercent(value: number | null | undefined): string {
  if (value === null || value === undefined) return '—'
  return `${Math.round(value * 1000) / 10}%`
}

export function formatDuration(seconds: number): string {
  if (seconds < 60) return `${seconds}s`
  const hours = Math.floor(seconds / 3600)
  const mins = Math.floor((seconds % 3600) / 60)
  const secs = seconds % 60
  if (hours > 0) return `${hours}h ${mins}m ${secs}s`
  return `${mins}m ${secs}s`
}

export function formatRelativeTime(date: Date | string): string {
  const d = typeof date === 'string' ? new Date(date) : date
  const now = new Date()
  const diffMs = now.getTime() - d.getTime()
  const diffSec = Math.floor(diffMs / 1000)
  const diffMin = Math.floor(diffSec / 60)
  const diffHours = Math.floor(diffMin / 60)
  const diffDays = Math.floor(diffHours / 24)

  if (diffSec < 60) return 'just now'
  if (diffMin < 60) return `${diffMin} minute${diffMin !== 1 ? 's' : ''} ago`
  if (diffHours < 24) return `${diffHours} hour${diffHours !== 1 ? 's' : ''} ago`
  return `${diffDays} day${diffDays !== 1 ? 's' : ''} ago`
}

export function truncateText(text: string, maxLength: number): string {
  if (text.length <= maxLength) return text
  return text.slice(0, maxLength) + '...'
}

export function parseSSEEvent(data: string): SSEEvent | null {
  try {
    return JSON.parse(data) as SSEEvent
  } catch {
    return null
  }
}
