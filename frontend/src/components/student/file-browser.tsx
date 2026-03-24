'use client'

import { useState, useEffect, useCallback } from 'react'
import { Card, CardContent } from '@/components/ui/card'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import { API_BASE_URL } from '@/lib/constants'
import type { StudentFile } from '@/lib/types'
import {
  FileText,
  FileCode,
  Image as ImageIcon,
  FileSpreadsheet,
  File,
  Eye,
  Download,
  ChevronRight,
  Folder,
  Code,
  Binary,
  Loader2,
  AlertCircle,
  Maximize2,
  Minimize2,
  X,
} from 'lucide-react'

const DISPLAY_TYPE_ICONS: Record<string, typeof FileText> = {
  code: FileCode,
  text: FileText,
  pdf: FileText,
  image: ImageIcon,
  notebook: FileSpreadsheet,
  docx: FileText,
  binary: Binary,
}

const LANG_MAP: Record<string, string> = {
  '.py': 'python',
  '.js': 'javascript',
  '.ts': 'typescript',
  '.java': 'java',
  '.cpp': 'c++',
  '.c': 'c',
  '.go': 'go',
  '.rb': 'ruby',
  '.php': 'php',
  '.swift': 'swift',
  '.rs': 'rust',
  '.html': 'html',
  '.css': 'css',
  '.sql': 'sql',
  '.sh': 'bash',
  '.json': 'json',
  '.xml': 'xml',
  '.md': 'markdown',
  '.csv': 'csv',
}

function formatFileSize(bytes: number | undefined): string {
  if (!bytes || bytes === 0) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB']
  const i = Math.floor(Math.log(bytes) / Math.log(1024))
  return `${(bytes / Math.pow(1024, i)).toFixed(i > 0 ? 1 : 0)} ${units[i]}`
}

interface FileBrowserProps {
  sessionId: number
  studentId: number
  files: StudentFile[]
}

export function FileBrowser({ sessionId, studentId, files }: FileBrowserProps) {
  const [selectedIndex, setSelectedIndex] = useState(0)
  const [fileContent, setFileContent] = useState<string | null>(null)
  const [isLoadingContent, setIsLoadingContent] = useState(false)
  const [contentError, setContentError] = useState<string | null>(null)
  const [isFullscreen, setIsFullscreen] = useState(false)

  const selectedFile = files[selectedIndex] ?? null
  const displayType = selectedFile?.display_type || selectedFile?.type || 'binary'

  const fetchFileContent = useCallback(async (file: StudentFile) => {
    if (!file.view_url) {
      setFileContent(null)
      setContentError('File not available for preview')
      return
    }

    setIsLoadingContent(true)
    setContentError(null)
    setFileContent(null)

    try {
      const url = `${API_BASE_URL}${file.view_url}`
      const dt = file.display_type || file.type || ''

      // For images and PDFs, we just use the URL directly
      if (dt === 'image' || dt === 'pdf') {
        setFileContent(url)
        return
      }

      // For text/code, fetch the content
      const res = await fetch(url)
      if (!res.ok) throw new Error(`Failed to load file (${res.status})`)
      const text = await res.text()
      setFileContent(text)
    } catch (err) {
      setContentError(err instanceof Error ? err.message : 'Failed to load file')
    } finally {
      setIsLoadingContent(false)
    }
  }, [])

  useEffect(() => {
    if (selectedFile) {
      fetchFileContent(selectedFile)
    }
  }, [selectedFile, fetchFileContent])

  if (!files.length) {
    return (
      <Card>
        <CardContent className="py-12 text-center">
          <File className="h-10 w-10 text-[var(--text-muted)] mx-auto mb-3 opacity-40" />
          <p className="text-[var(--text-muted)] text-sm">No files submitted.</p>
        </CardContent>
      </Card>
    )
  }

  return (
    <div className={cn(
      'flex flex-col md:flex-row gap-0 rounded-xl border border-[var(--border)] overflow-hidden bg-[var(--bg-card)]',
      isFullscreen && 'fixed inset-4 z-50 rounded-xl shadow-2xl'
    )}>
      {/* Fullscreen overlay */}
      {isFullscreen && (
        <div className="fixed inset-0 bg-black/60 z-40" onClick={() => setIsFullscreen(false)} />
      )}

      {/* File Sidebar */}
      <div className={cn(
        'md:w-64 shrink-0 border-b md:border-b-0 md:border-r border-[var(--border)] bg-[var(--bg-page)]',
        isFullscreen && 'relative z-50'
      )}>
        <div className="px-4 py-3 border-b border-[var(--border)] flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Folder className="h-4 w-4 text-[var(--text-muted)]" />
            <span className="text-xs font-semibold text-[var(--text-primary)] uppercase tracking-wider">
              Files ({files.length})
            </span>
          </div>
        </div>
        <div className="max-h-[500px] overflow-y-auto">
          {files.map((file, i) => {
            const dt = file.display_type || file.type || 'binary'
            const Icon = DISPLAY_TYPE_ICONS[dt] || File
            const isSelected = i === selectedIndex
            const ext = file.extension || ''
            const lang = LANG_MAP[ext] || ''

            return (
              <button
                key={`${file.filename}-${i}`}
                className={cn(
                  'w-full flex items-center gap-2.5 px-4 py-2.5 text-left transition-colors border-l-2',
                  isSelected
                    ? 'bg-indigo-500/10 border-indigo-400 text-[var(--text-primary)]'
                    : 'border-transparent text-[var(--text-muted)] hover:bg-[var(--border)]/30 hover:text-[var(--text-primary)]'
                )}
                onClick={() => setSelectedIndex(i)}
              >
                <Icon className={cn('h-4 w-4 shrink-0', isSelected ? 'text-indigo-400' : '')} />
                <div className="flex-1 min-w-0">
                  <p className="text-sm font-medium truncate">{file.filename}</p>
                  <div className="flex items-center gap-2 mt-0.5">
                    {lang && (
                      <span className="text-[10px] text-[var(--text-muted)] uppercase">{lang}</span>
                    )}
                    {file.size != null && (
                      <span className="text-[10px] text-[var(--text-muted)]">
                        {formatFileSize(file.size)}
                      </span>
                    )}
                  </div>
                </div>
                {!file.exists && (
                  <Badge variant="error" className="text-[9px] px-1 py-0">Missing</Badge>
                )}
              </button>
            )
          })}
        </div>
      </div>

      {/* Preview Panel */}
      <div className={cn('flex-1 min-w-0 flex flex-col', isFullscreen && 'relative z-50')}>
        {/* Preview Header */}
        {selectedFile && (
          <div className="px-4 py-2.5 border-b border-[var(--border)] flex items-center justify-between bg-[var(--bg-page)]">
            <div className="flex items-center gap-2 min-w-0">
              <Eye className="h-3.5 w-3.5 text-[var(--text-muted)] shrink-0" />
              <span className="text-sm font-medium text-[var(--text-primary)] truncate">
                {selectedFile.relative_path || selectedFile.filename}
              </span>
              <Badge variant="default" className="text-[10px] shrink-0">
                {displayType}
              </Badge>
            </div>
            <div className="flex items-center gap-1.5">
              {selectedFile.view_url && (
                <a
                  href={`${API_BASE_URL}${selectedFile.view_url}`}
                  target="_blank"
                  rel="noopener noreferrer"
                >
                  <Button variant="ghost" size="sm" className="h-7 w-7 p-0">
                    <Download className="h-3.5 w-3.5" />
                  </Button>
                </a>
              )}
              <Button
                variant="ghost"
                size="sm"
                className="h-7 w-7 p-0"
                onClick={() => setIsFullscreen(!isFullscreen)}
              >
                {isFullscreen ? (
                  <Minimize2 className="h-3.5 w-3.5" />
                ) : (
                  <Maximize2 className="h-3.5 w-3.5" />
                )}
              </Button>
              {isFullscreen && (
                <Button
                  variant="ghost"
                  size="sm"
                  className="h-7 w-7 p-0"
                  onClick={() => setIsFullscreen(false)}
                >
                  <X className="h-3.5 w-3.5" />
                </Button>
              )}
            </div>
          </div>
        )}

        {/* Preview Content */}
        <div className={cn(
          'flex-1 overflow-auto',
          isFullscreen ? 'h-[calc(100vh-8rem)]' : 'h-[500px]',
        )}>
          {isLoadingContent && (
            <div className="flex items-center justify-center h-full">
              <Loader2 className="h-6 w-6 animate-spin text-indigo-400" />
            </div>
          )}

          {contentError && (
            <div className="flex flex-col items-center justify-center h-full gap-2">
              <AlertCircle className="h-8 w-8 text-[var(--text-muted)] opacity-40" />
              <p className="text-sm text-[var(--text-muted)]">{contentError}</p>
            </div>
          )}

          {!isLoadingContent && !contentError && fileContent && selectedFile && (
            <FilePreview
              content={fileContent}
              displayType={displayType}
              filename={selectedFile.filename}
              extension={selectedFile.extension || ''}
            />
          )}

          {!isLoadingContent && !contentError && !fileContent && !selectedFile && (
            <div className="flex flex-col items-center justify-center h-full gap-2">
              <File className="h-8 w-8 text-[var(--text-muted)] opacity-40" />
              <p className="text-sm text-[var(--text-muted)]">Select a file to preview</p>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

// ── Notebook Cell Types ─────────────────────────────────────
interface NotebookCell {
  cell_type: 'code' | 'markdown' | 'raw'
  source: string[] | string
  outputs?: NotebookOutput[]
  execution_count?: number | null
  metadata?: Record<string, unknown>
}

interface NotebookOutput {
  output_type: string
  text?: string[] | string
  data?: Record<string, string[] | string>
  ename?: string
  evalue?: string
  traceback?: string[]
  name?: string
}

function parseNotebook(raw: string): NotebookCell[] | null {
  try {
    const nb = JSON.parse(raw)
    if (!nb || !Array.isArray(nb.cells)) return null
    return nb.cells as NotebookCell[]
  } catch {
    return null
  }
}

function joinSource(src: string[] | string | undefined): string {
  if (!src) return ''
  return Array.isArray(src) ? src.join('') : String(src)
}

function joinText(txt: string[] | string | undefined): string {
  if (!txt) return ''
  return Array.isArray(txt) ? txt.join('') : String(txt)
}

// ── Notebook Renderer ───────────────────────────────────────
function NotebookPreview({ content, filename }: { content: string; filename: string }) {
  const cells = parseNotebook(content)

  if (!cells) {
    // Fallback: show raw JSON with line numbers
    return <CodePreview content={content} lang="json" />
  }

  const nonEmptyCells = cells.filter(c => joinSource(c.source).trim().length > 0 || (c.outputs && c.outputs.length > 0))

  return (
    <div className="bg-[var(--bg-page)] min-h-full">
      {/* Notebook header */}
      <div className="px-4 py-2 bg-[var(--border)]/30 border-b border-[var(--border)] flex items-center gap-2">
        <FileSpreadsheet className="h-3.5 w-3.5 text-orange-400" />
        <span className="text-[10px] font-semibold text-[var(--text-muted)] uppercase tracking-wider">
          Jupyter Notebook
        </span>
        <span className="text-[10px] text-[var(--text-muted)]">
          {nonEmptyCells.length} cell{nonEmptyCells.length !== 1 ? 's' : ''}
        </span>
      </div>

      <div className="p-3 space-y-2">
        {nonEmptyCells.map((cell, i) => (
          <NotebookCellView key={i} cell={cell} index={i} />
        ))}
        {nonEmptyCells.length === 0 && (
          <div className="text-center py-8 text-[var(--text-muted)] text-sm">
            This notebook is empty.
          </div>
        )}
      </div>
    </div>
  )
}

function NotebookCellView({ cell, index }: { cell: NotebookCell; index: number }) {
  const source = joinSource(cell.source)

  if (cell.cell_type === 'markdown') {
    return (
      <div className="rounded-lg border border-[var(--border)]/50 overflow-hidden">
        <div className="px-3 py-1 bg-[var(--border)]/20 flex items-center gap-2 border-b border-[var(--border)]/30">
          <FileText className="h-3 w-3 text-blue-400" />
          <span className="text-[10px] text-[var(--text-muted)] font-medium">Markdown</span>
        </div>
        <div className="px-4 py-3 text-sm text-[var(--text-secondary)] leading-relaxed prose-invert max-w-none">
          <MarkdownRenderer text={source} />
        </div>
      </div>
    )
  }

  if (cell.cell_type === 'code') {
    const execCount = cell.execution_count
    const outputs = cell.outputs || []

    return (
      <div className="rounded-lg border border-[var(--border)]/50 overflow-hidden">
        {/* Cell header */}
        <div className="px-3 py-1 bg-emerald-500/5 flex items-center gap-2 border-b border-[var(--border)]/30">
          <Code className="h-3 w-3 text-emerald-400" />
          <span className="text-[10px] text-emerald-400/80 font-mono font-medium">
            In [{execCount ?? ' '}]
          </span>
          <span className="text-[10px] text-[var(--text-muted)]">Python</span>
        </div>

        {/* Source code */}
        {source.trim() && (
          <div className="bg-[#0d1117] overflow-x-auto">
            <table className="w-full border-collapse">
              <tbody>
                {source.split('\n').map((line, li) => (
                  <tr key={li} className="hover:bg-white/[0.03]">
                    <td className="px-3 py-0 text-right text-[#484f58] select-none w-10 text-xs align-top font-mono border-r border-[#21262d]">
                      {li + 1}
                    </td>
                    <td className="px-4 py-0 whitespace-pre font-mono text-sm text-[#c9d1d9]">
                      {line || '\u00A0'}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {/* Outputs */}
        {outputs.length > 0 && (
          <div className="border-t border-[var(--border)]/30">
            <div className="px-3 py-0.5 bg-[var(--border)]/10">
              <span className="text-[10px] text-[var(--text-muted)] font-mono">
                Out [{execCount ?? ' '}]
              </span>
            </div>
            <div className="px-4 py-2 bg-[var(--bg-card)]/50">
              {outputs.map((out, oi) => (
                <CellOutput key={oi} output={out} />
              ))}
            </div>
          </div>
        )}
      </div>
    )
  }

  // Raw cell
  return (
    <div className="rounded-lg border border-[var(--border)]/50 overflow-hidden">
      <div className="px-3 py-1 bg-[var(--border)]/20 flex items-center gap-2 border-b border-[var(--border)]/30">
        <span className="text-[10px] text-[var(--text-muted)]">Raw</span>
      </div>
      <pre className="px-4 py-2 text-xs text-[var(--text-secondary)] whitespace-pre-wrap font-mono">
        {source}
      </pre>
    </div>
  )
}

function CellOutput({ output }: { output: NotebookOutput }) {
  // Error output
  if (output.output_type === 'error') {
    const tb = output.traceback?.join('\n') || `${output.ename}: ${output.evalue}`
    // Strip ANSI codes
    const clean = tb.replace(/\x1b\[[0-9;]*m/g, '')
    return (
      <pre className="text-xs text-red-400 font-mono whitespace-pre-wrap leading-relaxed">
        {clean}
      </pre>
    )
  }

  // Stream output (stdout/stderr)
  if (output.output_type === 'stream') {
    const text = joinText(output.text)
    return (
      <pre className={`text-xs font-mono whitespace-pre-wrap leading-relaxed ${output.name === 'stderr' ? 'text-yellow-400' : 'text-[var(--text-secondary)]'}`}>
        {text}
      </pre>
    )
  }

  // execute_result or display_data
  if (output.output_type === 'execute_result' || output.output_type === 'display_data') {
    const data = output.data || {}

    // Image output (PNG/JPEG)
    const imgBase64 = data['image/png'] || data['image/jpeg']
    if (imgBase64) {
      const src = `data:image/png;base64,${joinText(imgBase64)}`
      return (
        <div className="py-2">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src={src} alt="Cell output" className="max-w-full rounded" />
        </div>
      )
    }

    // HTML output
    const html = data['text/html']
    if (html) {
      return (
        <div
          className="text-xs text-[var(--text-secondary)] overflow-x-auto"
          dangerouslySetInnerHTML={{ __html: joinText(html) }}
        />
      )
    }

    // Plain text
    const text = data['text/plain']
    if (text) {
      return (
        <pre className="text-xs text-[var(--text-secondary)] font-mono whitespace-pre-wrap leading-relaxed">
          {joinText(text)}
        </pre>
      )
    }
  }

  return null
}

// ── Simple Markdown Renderer ────────────────────────────────
function MarkdownRenderer({ text }: { text: string }) {
  const lines = text.split('\n')
  const elements: React.ReactNode[] = []
  let i = 0

  while (i < lines.length) {
    const line = lines[i]

    // Headers
    if (line.startsWith('### ')) {
      elements.push(<h3 key={i} className="text-sm font-bold text-[var(--text-primary)] mt-3 mb-1">{line.slice(4)}</h3>)
    } else if (line.startsWith('## ')) {
      elements.push(<h2 key={i} className="text-base font-bold text-[var(--text-primary)] mt-3 mb-1">{line.slice(3)}</h2>)
    } else if (line.startsWith('# ')) {
      elements.push(<h1 key={i} className="text-lg font-bold text-[var(--text-primary)] mt-3 mb-1">{line.slice(2)}</h1>)
    }
    // Bullet lists
    else if (/^[-*]\s/.test(line)) {
      elements.push(
        <div key={i} className="flex gap-2 ml-2">
          <span className="text-[var(--text-muted)]">•</span>
          <span><InlineMarkdown text={line.slice(2)} /></span>
        </div>
      )
    }
    // Numbered lists
    else if (/^\d+\.\s/.test(line)) {
      const match = line.match(/^(\d+)\.\s(.*)$/)
      if (match) {
        elements.push(
          <div key={i} className="flex gap-2 ml-2">
            <span className="text-[var(--text-muted)] font-mono text-xs">{match[1]}.</span>
            <span><InlineMarkdown text={match[2]} /></span>
          </div>
        )
      }
    }
    // Empty line
    else if (line.trim() === '') {
      elements.push(<div key={i} className="h-2" />)
    }
    // Normal paragraph
    else {
      elements.push(<p key={i} className="leading-relaxed"><InlineMarkdown text={line} /></p>)
    }
    i++
  }

  return <>{elements}</>
}

function InlineMarkdown({ text }: { text: string }) {
  // Handle **bold**, *italic*, `code`, and [links](url)
  const parts = text.split(/(\*\*.*?\*\*|\*.*?\*|`.*?`|\[.*?\]\(.*?\))/)
  return (
    <>
      {parts.map((part, i) => {
        if (part.startsWith('**') && part.endsWith('**')) {
          return <strong key={i} className="font-semibold text-[var(--text-primary)]">{part.slice(2, -2)}</strong>
        }
        if (part.startsWith('*') && part.endsWith('*') && !part.startsWith('**')) {
          return <em key={i} className="italic">{part.slice(1, -1)}</em>
        }
        if (part.startsWith('`') && part.endsWith('`')) {
          return <code key={i} className="px-1 py-0.5 rounded bg-[var(--border)]/40 text-[11px] font-mono text-indigo-300">{part.slice(1, -1)}</code>
        }
        const linkMatch = part.match(/^\[(.*?)\]\((.*?)\)$/)
        if (linkMatch) {
          return <a key={i} href={linkMatch[2]} className="text-indigo-400 underline" target="_blank" rel="noopener noreferrer">{linkMatch[1]}</a>
        }
        return <span key={i}>{part}</span>
      })}
    </>
  )
}

// ── Code / Text Preview ─────────────────────────────────────
function CodePreview({ content, lang }: { content: string; lang?: string }) {
  const lines = content.split('\n')
  return (
    <div className="font-mono text-sm bg-[var(--bg-page)]">
      {lang && (
        <div className="px-4 py-1.5 bg-[var(--border)]/30 border-b border-[var(--border)] flex items-center gap-2">
          <Code className="h-3 w-3 text-[var(--text-muted)]" />
          <span className="text-[10px] font-semibold text-[var(--text-muted)] uppercase tracking-wider">
            {lang}
          </span>
          <span className="text-[10px] text-[var(--text-muted)]">
            {lines.length} lines
          </span>
        </div>
      )}
      <div className="overflow-x-auto">
        <table className="w-full border-collapse">
          <tbody>
            {lines.map((line, i) => (
              <tr key={i} className="hover:bg-[var(--border)]/20 transition-colors">
                <td className="px-3 py-0 text-right text-[var(--text-muted)]/40 select-none w-12 text-xs align-top border-r border-[var(--border)]/30">
                  {i + 1}
                </td>
                <td className="px-4 py-0 whitespace-pre text-[var(--text-secondary)]">
                  {line || '\u00A0'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

function FilePreview({
  content,
  displayType,
  filename,
  extension,
}: {
  content: string
  displayType: string
  filename: string
  extension: string
}) {
  // PDF preview
  if (displayType === 'pdf') {
    return (
      <iframe
        src={content}
        className="w-full h-full border-0"
        title={`Preview: ${filename}`}
      />
    )
  }

  // Image preview
  if (displayType === 'image') {
    return (
      <div className="flex items-center justify-center h-full p-4 bg-[var(--bg-page)]">
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img
          src={content}
          alt={filename}
          className="max-w-full max-h-full object-contain rounded-lg shadow-lg"
        />
      </div>
    )
  }

  // Jupyter Notebook — beautiful cell rendering
  if (displayType === 'notebook') {
    return <NotebookPreview content={content} filename={filename} />
  }

  // Code / text preview with line numbers
  if (displayType === 'code' || displayType === 'text' || displayType === 'docx') {
    const lang = LANG_MAP[extension] || ''
    return <CodePreview content={content} lang={lang || undefined} />
  }

  // Binary / unknown fallback
  return (
    <div className="flex flex-col items-center justify-center h-full gap-3">
      <Binary className="h-10 w-10 text-[var(--text-muted)] opacity-40" />
      <p className="text-sm text-[var(--text-muted)]">
        Binary file &mdash; preview not available
      </p>
      <p className="text-xs text-[var(--text-muted)]">
        Use the download button to view this file
      </p>
    </div>
  )
}
