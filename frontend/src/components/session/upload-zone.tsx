'use client'

import { useState, useRef, useCallback } from 'react'
import { Upload, FileArchive, Loader2, CheckCircle2, AlertCircle } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

interface UploadZoneProps {
  sessionId: number
  onUpload: (file: File) => Promise<void>
  disabled?: boolean
}

export function UploadZone({ sessionId, onUpload, disabled }: UploadZoneProps) {
  const [isDragging, setIsDragging] = useState(false)
  const [isUploading, setIsUploading] = useState(false)
  const [uploadStatus, setUploadStatus] = useState<'idle' | 'success' | 'error'>('idle')
  const [errorMessage, setErrorMessage] = useState('')
  const [fileName, setFileName] = useState('')
  const fileInputRef = useRef<HTMLInputElement>(null)

  const handleFile = useCallback(async (file: File) => {
    if (!file.name.endsWith('.zip')) {
      setUploadStatus('error')
      setErrorMessage('Please upload a .zip file')
      return
    }

    setFileName(file.name)
    setIsUploading(true)
    setUploadStatus('idle')
    setErrorMessage('')

    try {
      await onUpload(file)
      setUploadStatus('success')
    } catch (err) {
      setUploadStatus('error')
      setErrorMessage(err instanceof Error ? err.message : 'Upload failed. Please try again.')
    } finally {
      setIsUploading(false)
    }
  }, [onUpload])

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    setIsDragging(false)
    if (disabled || isUploading) return

    const file = e.dataTransfer.files[0]
    if (file) handleFile(file)
  }, [disabled, isUploading, handleFile])

  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    if (!disabled && !isUploading) setIsDragging(true)
  }, [disabled, isUploading])

  const handleDragLeave = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    setIsDragging(false)
  }, [])

  const handleClick = () => {
    if (!disabled && !isUploading) fileInputRef.current?.click()
  }

  const handleInputChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0]
    if (file) handleFile(file)
    // Reset so the same file can be re-selected
    e.target.value = ''
  }

  return (
    <div
      data-testid="upload-zone"
      onDrop={handleDrop}
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
      onClick={handleClick}
      className={cn(
        'relative rounded-xl border-2 border-dashed p-8 text-center cursor-pointer transition-all duration-200',
        isDragging
          ? 'border-indigo-500 bg-indigo-500/10'
          : uploadStatus === 'success'
            ? 'border-emerald-500/50 bg-emerald-500/5'
            : uploadStatus === 'error'
              ? 'border-rose-500/50 bg-rose-500/5'
              : 'border-[var(--border)] hover:border-indigo-500/50 hover:bg-indigo-500/5',
        (disabled || isUploading) && 'cursor-not-allowed opacity-60',
      )}
    >
      <input
        ref={fileInputRef}
        type="file"
        accept=".zip"
        onChange={handleInputChange}
        className="hidden"
        data-testid="upload-input"
      />

      <div className="flex flex-col items-center gap-3">
        {isUploading ? (
          <>
            <Loader2 className="h-10 w-10 text-indigo-500 animate-spin" />
            <div>
              <p className="text-sm font-medium text-[var(--text-primary)]">
                Uploading {fileName}...
              </p>
              <p className="text-xs text-[var(--text-muted)] mt-1">
                Extracting and processing student submissions
              </p>
            </div>
          </>
        ) : uploadStatus === 'success' ? (
          <>
            <CheckCircle2 className="h-10 w-10 text-emerald-500" />
            <div>
              <p className="text-sm font-medium text-emerald-500">
                Upload successful!
              </p>
              <p className="text-xs text-[var(--text-muted)] mt-1">
                {fileName} — students are ready for grading
              </p>
            </div>
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={(e) => {
                e.stopPropagation()
                setUploadStatus('idle')
                setFileName('')
              }}
              className="mt-2"
            >
              Upload Different File
            </Button>
          </>
        ) : uploadStatus === 'error' ? (
          <>
            <AlertCircle className="h-10 w-10 text-rose-500" />
            <div>
              <p className="text-sm font-medium text-rose-500">
                {errorMessage}
              </p>
              <p className="text-xs text-[var(--text-muted)] mt-1">
                Click or drag a new file to try again
              </p>
            </div>
          </>
        ) : (
          <>
            <div className="rounded-full bg-indigo-500/10 p-3">
              {isDragging ? (
                <FileArchive className="h-8 w-8 text-indigo-500" />
              ) : (
                <Upload className="h-8 w-8 text-indigo-500" />
              )}
            </div>
            <div>
              <p className="text-sm font-medium text-[var(--text-primary)]">
                {isDragging ? 'Drop your ZIP file here' : 'Upload Student Submissions'}
              </p>
              <p className="text-xs text-[var(--text-muted)] mt-1">
                Drag & drop a ZIP file, or click to browse
              </p>
              <p className="text-xs text-[var(--text-muted)] mt-0.5">
                ZIP should contain one folder per student with their submission files
              </p>
            </div>
          </>
        )}
      </div>
    </div>
  )
}
