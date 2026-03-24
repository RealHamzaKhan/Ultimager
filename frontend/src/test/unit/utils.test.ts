import { describe, it, expect } from 'vitest'
import {
  scoreToGrade,
  gradeToColor,
  confidenceToColor,
  confidenceToLabel,
  formatScore,
  formatPercent,
  formatDuration,
  formatRelativeTime,
  truncateText,
  parseSSEEvent,
  cn,
} from '@/lib/utils'

describe('scoreToGrade', () => {
  it('returns A for 90', () => expect(scoreToGrade(90)).toBe('A-'))
  it('returns B for 80', () => expect(scoreToGrade(80)).toBe('B-'))
  it('returns C for 70', () => expect(scoreToGrade(70)).toBe('C-'))
  it('returns D for 60', () => expect(scoreToGrade(60)).toBe('D'))
  it('returns F for 59', () => expect(scoreToGrade(59)).toBe('F'))
  it('returns F for 0', () => expect(scoreToGrade(0)).toBe('F'))
  it('returns A+ for 100', () => expect(scoreToGrade(100)).toBe('A+'))
  it('returns B for 89.9', () => expect(scoreToGrade(89.9)).toBe('B+'))
  it('returns ? for NaN', () => expect(scoreToGrade(NaN)).toBe('?'))
  it('returns ? for null', () => expect(scoreToGrade(null)).toBe('?'))
  it('returns ? for undefined', () => expect(scoreToGrade(undefined)).toBe('?'))
})

describe('gradeToColor', () => {
  it('A maps to success', () => expect(gradeToColor('A')).toBe('var(--color-success)'))
  it('B maps to info', () => expect(gradeToColor('B')).toBe('var(--color-info)'))
  it('C maps to warning', () => expect(gradeToColor('C')).toBe('var(--color-warning)'))
  it('F maps to error', () => expect(gradeToColor('F')).toBe('var(--color-error)'))
  it('unknown maps to muted', () => expect(gradeToColor('X')).toBe('var(--color-muted)'))
})

describe('confidenceToColor', () => {
  it('high returns success', () => expect(confidenceToColor('high')).toBe('var(--color-success)'))
  it('medium returns warning', () => expect(confidenceToColor('medium')).toBe('var(--color-warning)'))
  it('low returns error', () => expect(confidenceToColor('low')).toBe('var(--color-error)'))
})

describe('confidenceToLabel', () => {
  it('high returns High', () => expect(confidenceToLabel('high')).toBe('High'))
  it('medium returns Medium', () => expect(confidenceToLabel('medium')).toBe('Medium'))
  it('low returns Low', () => expect(confidenceToLabel('low')).toBe('Low'))
})

describe('formatScore', () => {
  it('"85.0" → "85"', () => expect(formatScore(85.0)).toBe('85'))
  it('"85.5" → "85.5"', () => expect(formatScore(85.5)).toBe('85.5'))
  it('0 → "0"', () => expect(formatScore(0)).toBe('0'))
  it('null → "—"', () => expect(formatScore(null)).toBe('—'))
})

describe('formatPercent', () => {
  it('0.856 → "85.6%"', () => expect(formatPercent(0.856)).toBe('85.6%'))
  it('null → "—"', () => expect(formatPercent(null)).toBe('—'))
})

describe('formatDuration', () => {
  it('3661 → "1h 1m 1s"', () => expect(formatDuration(3661)).toBe('1h 1m 1s'))
  it('65 → "1m 5s"', () => expect(formatDuration(65)).toBe('1m 5s'))
  it('30 → "30s"', () => expect(formatDuration(30)).toBe('30s'))
})

describe('formatRelativeTime', () => {
  it('recent date → "just now"', () => {
    const now = new Date()
    expect(formatRelativeTime(now)).toBe('just now')
  })
  it('10 min ago', () => {
    const d = new Date(Date.now() - 10 * 60 * 1000)
    expect(formatRelativeTime(d)).toBe('10 minutes ago')
  })
})

describe('truncateText', () => {
  it('short text unchanged', () => expect(truncateText('hello', 10)).toBe('hello'))
  it('long text truncated', () => expect(truncateText('hello world', 5)).toBe('hello...'))
})

describe('parseSSEEvent', () => {
  it('valid JSON → parsed object', () => {
    const result = parseSSEEvent('{"event":"progress","data":{}}')
    expect(result).toEqual({ event: 'progress', data: {} })
  })
  it('malformed → null', () => {
    expect(parseSSEEvent('not json')).toBeNull()
  })
})

describe('cn', () => {
  it('merges classes', () => {
    expect(cn('px-2', 'py-1')).toBe('px-2 py-1')
  })
  it('deduplicates conflicting classes', () => {
    const result = cn('text-red-500', 'text-blue-500')
    expect(result).toBe('text-blue-500')
  })
})
