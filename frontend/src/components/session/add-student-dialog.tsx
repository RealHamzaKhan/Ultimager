'use client'

import { useState, useRef, useCallback } from 'react'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { uploadStudent } from '@/lib/api'
import {
  X,
  UserPlus,
  FileUp,
  File,
  Trash2,
  Loader2,
  CheckCircle2,
  AlertTriangle,
} from 'lucide-react'

interface AddStudentDialogProps {
  sessionId: number
  open: boolean
  onClose: () => void
  onSuccess: () => void
}

export function AddStudentDialog({ sessionId, open, onClose, onSuccess }: AddStudentDialogProps) {
  const [studentName, setStudentName] = useState('')
  const [files, setFiles] = useState<File[]>([])
  const [uploading, setUploading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState(false)
  const fileInputRef = useRef<HTMLInputElement>(null)
  const dropZoneRef = useRef<HTMLDivElement>(null)
  const [dragOver, setDragOver] = useState(false)

  const reset = useCallback(() => {
    setStudentName('')
    setFiles([])
    setError(null)
    setSuccess(false)
    setUploading(false)
  }, [])

  const handleClose = useCallback(() => {
    if (uploading) return
    reset()
    onClose()
  }, [uploading, reset, onClose])

  const handleFileDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    setDragOver(false)
    const dropped = Array.from(e.dataTransfer.files)
    if (dropped.length > 0) {
      setFiles((prev) => [...prev, ...dropped])
      setError(null)
    }
  }, [])

  const handleFileSelect = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    const selected = Array.from(e.target.files || [])
    if (selected.length > 0) {
      setFiles((prev) => [...prev, ...selected])
      setError(null)
    }
    // Reset input so the same file can be selected again
    e.target.value = ''
  }, [])

  const removeFile = useCallback((index: number) => {
    setFiles((prev) => prev.filter((_, i) => i !== index))
  }, [])

  const handleSubmit = async () => {
    const name = studentName.trim()
    if (!name) {
      setError('Student name is required')
      return
    }
    if (files.length === 0) {
      setError('At least one file is required')
      return
    }

    setUploading(true)
    setError(null)

    try {
      await uploadStudent(sessionId, name, files)
      setSuccess(true)
      setTimeout(() => {
        onSuccess()
        handleClose()
      }, 1200)
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Upload failed'
      setError(msg)
      setUploading(false)
    }
  }

  if (!open) return null

  const formatSize = (bytes: number) => {
    if (bytes < 1024) return `${bytes} B`
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
  }

  const extIcon = (name: string) => {
    const ext = name.split('.').pop()?.toLowerCase() || ''
    const colors: Record<string, string> = {
      py: 'text-emerald-400',
      java: 'text-orange-400',
      pdf: 'text-rose-400',
      docx: 'text-blue-400',
      doc: 'text-blue-400',
      zip: 'text-amber-400',
      png: 'text-violet-400',
      jpg: 'text-violet-400',
      jpeg: 'text-violet-400',
    }
    return colors[ext] || 'text-zinc-400'
  }

  return (
    <>
      {/* Backdrop */}
      <div
        className="fixed inset-0 z-50 bg-black/60 backdrop-blur-sm animate-in fade-in duration-200"
        onClick={handleClose}
      />

      {/* Dialog */}
      <div className="fixed inset-0 z-50 flex items-center justify-center p-4 pointer-events-none">
        <div
          className="pointer-events-auto w-full max-w-lg rounded-2xl border border-zinc-700/60 bg-zinc-900 shadow-2xl shadow-black/40 animate-in slide-in-from-bottom-4 fade-in duration-300"
          onClick={(e) => e.stopPropagation()}
        >
          {/* Header */}
          <div className="flex items-center justify-between px-6 pt-5 pb-4 border-b border-zinc-800">
            <div className="flex items-center gap-3">
              <div className="flex items-center justify-center w-9 h-9 rounded-lg bg-indigo-500/10">
                <UserPlus className="h-4.5 w-4.5 text-indigo-400" />
              </div>
              <div>
                <h2 className="text-base font-semibold text-zinc-100">Add Student</h2>
                <p className="text-xs text-zinc-500 mt-0.5">Upload files for a single student</p>
              </div>
            </div>
            <button
              onClick={handleClose}
              disabled={uploading}
              className="rounded-lg p-1.5 text-zinc-500 hover:text-zinc-300 hover:bg-zinc-800 transition-colors disabled:opacity-50"
            >
              <X className="h-4 w-4" />
            </button>
          </div>

          {/* Body */}
          <div className="px-6 py-5 space-y-5">
            {/* Success state */}
            {success ? (
              <div className="flex flex-col items-center py-6 gap-3">
                <div className="flex items-center justify-center w-14 h-14 rounded-full bg-emerald-500/10">
                  <CheckCircle2 className="h-7 w-7 text-emerald-400" />
                </div>
                <p className="text-sm font-medium text-emerald-400">Student uploaded successfully!</p>
                <p className="text-xs text-zinc-500">Grading will begin automatically</p>
              </div>
            ) : (
              <>
                {/* Student name */}
                <Input
                  id="student-name"
                  label="Student Name / ID"
                  placeholder="e.g. 22F-3456 or John Doe"
                  value={studentName}
                  onChange={(e) => {
                    setStudentName(e.target.value)
                    setError(null)
                  }}
                  disabled={uploading}
                  className="bg-zinc-800/60 border-zinc-700/60"
                />

                {/* Drop zone */}
                <div>
                  <label className="block text-sm font-medium text-zinc-400 mb-1.5">Files</label>
                  <div
                    ref={dropZoneRef}
                    onDragOver={(e) => { e.preventDefault(); setDragOver(true) }}
                    onDragLeave={() => setDragOver(false)}
                    onDrop={handleFileDrop}
                    onClick={() => fileInputRef.current?.click()}
                    className={`
                      relative cursor-pointer rounded-xl border-2 border-dashed transition-all duration-200
                      ${dragOver
                        ? 'border-indigo-400 bg-indigo-500/5 scale-[1.01]'
                        : 'border-zinc-700/60 bg-zinc-800/30 hover:border-zinc-600 hover:bg-zinc-800/50'
                      }
                      ${files.length > 0 ? 'py-4 px-4' : 'py-8 px-4'}
                    `}
                  >
                    <input
                      ref={fileInputRef}
                      type="file"
                      multiple
                      onChange={handleFileSelect}
                      className="hidden"
                      disabled={uploading}
                    />

                    {files.length === 0 ? (
                      <div className="flex flex-col items-center gap-2 text-center">
                        <div className="flex items-center justify-center w-10 h-10 rounded-full bg-zinc-800 border border-zinc-700/60">
                          <FileUp className="h-4.5 w-4.5 text-zinc-500" />
                        </div>
                        <div>
                          <p className="text-sm text-zinc-300 font-medium">
                            Drop files here or <span className="text-indigo-400">browse</span>
                          </p>
                          <p className="text-xs text-zinc-600 mt-0.5">
                            .py, .java, .pdf, .docx, .zip, images, and more
                          </p>
                        </div>
                      </div>
                    ) : (
                      <div className="space-y-2">
                        {files.map((file, i) => (
                          <div
                            key={`${file.name}-${i}`}
                            className="flex items-center gap-3 rounded-lg bg-zinc-800/60 border border-zinc-700/40 px-3 py-2 group"
                          >
                            <File className={`h-4 w-4 shrink-0 ${extIcon(file.name)}`} />
                            <div className="min-w-0 flex-1">
                              <p className="text-sm text-zinc-300 truncate">{file.name}</p>
                              <p className="text-xs text-zinc-600">{formatSize(file.size)}</p>
                            </div>
                            <button
                              onClick={(e) => { e.stopPropagation(); removeFile(i) }}
                              disabled={uploading}
                              className="p-1 rounded text-zinc-600 hover:text-rose-400 hover:bg-rose-500/10 opacity-0 group-hover:opacity-100 transition-all disabled:opacity-50"
                            >
                              <Trash2 className="h-3.5 w-3.5" />
                            </button>
                          </div>
                        ))}
                        <p className="text-xs text-zinc-600 text-center pt-1">
                          Click or drop to add more files
                        </p>
                      </div>
                    )}
                  </div>
                </div>

                {/* Error */}
                {error && (
                  <div className="flex items-start gap-2 rounded-lg bg-rose-500/5 border border-rose-500/20 px-3 py-2.5">
                    <AlertTriangle className="h-4 w-4 text-rose-400 shrink-0 mt-0.5" />
                    <p className="text-sm text-rose-400">{error}</p>
                  </div>
                )}
              </>
            )}
          </div>

          {/* Footer */}
          {!success && (
            <div className="flex items-center justify-end gap-2 px-6 py-4 border-t border-zinc-800">
              <Button
                variant="ghost"
                size="sm"
                onClick={handleClose}
                disabled={uploading}
                className="text-zinc-400"
              >
                Cancel
              </Button>
              <Button
                variant="primary"
                size="sm"
                onClick={handleSubmit}
                disabled={uploading || !studentName.trim() || files.length === 0}
                className="gap-1.5 min-w-[120px]"
              >
                {uploading ? (
                  <>
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                    Uploading...
                  </>
                ) : (
                  <>
                    <UserPlus className="h-3.5 w-3.5" />
                    Add Student
                  </>
                )}
              </Button>
            </div>
          )}
        </div>
      </div>
    </>
  )
}
