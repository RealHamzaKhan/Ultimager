'use client'

import { Card, CardContent } from '@/components/ui/card'
import { Button } from '@/components/ui/button'
import { API_BASE_URL } from '@/lib/constants'

interface ExportControlsProps {
  sessionId: number
}

export function ExportControls({ sessionId }: ExportControlsProps) {
  const csvUrl = `${API_BASE_URL}/session/${sessionId}/export/csv`
  const jsonUrl = `${API_BASE_URL}/session/${sessionId}/export/json`

  return (
    <Card data-testid="export-controls">
      <CardContent>
        <div className="flex items-center gap-3">
          <a href={csvUrl} download>
            <Button variant="outline" size="sm" data-testid="export-csv-btn">
              Export CSV
            </Button>
          </a>
          <a href={jsonUrl} download>
            <Button variant="outline" size="sm" data-testid="export-json-btn">
              Export JSON
            </Button>
          </a>
        </div>
      </CardContent>
    </Card>
  )
}
