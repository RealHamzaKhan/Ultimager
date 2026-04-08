import React from 'react'
import { render, screen } from '@testing-library/react'
import { vi, describe, it, expect } from 'vitest'
import { KeyMetrics } from '@/components/analytics/key-metrics'
import { ScoreHistogram } from '@/components/analytics/score-histogram'
import { ExportControls } from '@/components/analytics/export-controls'
import type { AnalyticsData } from '@/lib/types'

const mockAnalytics: AnalyticsData = {
  total_students: 30,
  graded_count: 28,
  error_count: 2,
  average_score: 82.5,
  median_score: 85,
  pass_rate: 0.89,
  grade_distribution: { A: 5, B: 15, C: 6, D: 2 },
  score_distribution: [95, 88, 82, 75, 68, 55, 92, 87, 78, 65],
  flagged_count: 3,
}

describe('KeyMetrics', () => {
  it('shows skeleton when loading', () => {
    render(<KeyMetrics data={null} isLoading={true} />)
    expect(screen.getByTestId('key-metrics')).toBeInTheDocument()
    // Should not show actual metric values
    expect(screen.queryByTestId('metric-average')).not.toBeInTheDocument()
  })

  it('shows metrics when data provided', () => {
    render(<KeyMetrics data={mockAnalytics} isLoading={false} />)
    expect(screen.getByTestId('metric-average')).toBeInTheDocument()
    expect(screen.getByTestId('metric-median')).toBeInTheDocument()
    expect(screen.getByTestId('metric-pass-rate')).toBeInTheDocument()
    expect(screen.getByTestId('metric-flagged')).toBeInTheDocument()
  })

  it('shows formatted values', () => {
    render(<KeyMetrics data={mockAnalytics} isLoading={false} />)
    expect(screen.getByText('82.5')).toBeInTheDocument()
    expect(screen.getByText('85')).toBeInTheDocument()
    expect(screen.getByText('3')).toBeInTheDocument()
  })

  it('shows dashes for null values', () => {
    const nullData: AnalyticsData = {
      ...mockAnalytics,
      average_score: null,
      median_score: null,
      pass_rate: null,
    }
    render(<KeyMetrics data={nullData} isLoading={false} />)
    const dashes = screen.getAllByText('—')
    expect(dashes.length).toBeGreaterThanOrEqual(3)
  })
})

describe('ScoreHistogram', () => {
  it('renders histogram bars', () => {
    render(<ScoreHistogram scores={[95, 88, 82, 75, 68]} maxScore={100} />)
    expect(screen.getByTestId('score-histogram')).toBeInTheDocument()
    // Should render 10 bins
    expect(screen.getByTestId('histogram-bar-0')).toBeInTheDocument()
    expect(screen.getByTestId('histogram-bar-9')).toBeInTheDocument()
  })

  it('handles empty scores', () => {
    render(<ScoreHistogram scores={[]} maxScore={100} />)
    expect(screen.getByTestId('score-histogram')).toBeInTheDocument()
  })

  it('shows bin labels', () => {
    render(<ScoreHistogram scores={[50]} maxScore={100} />)
    expect(screen.getByText('0-10%')).toBeInTheDocument()
    expect(screen.getByText('90-100%')).toBeInTheDocument()
  })
})

describe('ExportControls', () => {
  it('renders CSV and JSON export buttons', () => {
    render(<ExportControls sessionId={42} />)
    expect(screen.getByTestId('export-controls')).toBeInTheDocument()
    expect(screen.getByTestId('export-csv-btn')).toBeInTheDocument()
    expect(screen.getByTestId('export-json-btn')).toBeInTheDocument()
  })

  it('has correct export links', () => {
    render(<ExportControls sessionId={42} />)
    const csvLink = screen.getByTestId('export-csv-btn').closest('a')
    expect(csvLink?.getAttribute('href')).toContain('/session/42/export/csv')
    const jsonLink = screen.getByTestId('export-json-btn').closest('a')
    expect(jsonLink?.getAttribute('href')).toContain('/session/42/export/json')
  })
})
