'use client'

import { useState, useEffect, useRef, useCallback } from 'react'
import { useRouter } from 'next/navigation'
import { useUIStore } from '@/stores/ui-store'
import { cn } from '@/lib/utils'
import {
  Search,
  Home,
  PlusCircle,
  Sun,
  Moon,
  PanelLeftClose,
  Command,
} from 'lucide-react'

interface CommandItem {
  id: string
  label: string
  icon: React.ReactNode
  shortcut?: string
  action: () => void
}

function CommandPalette() {
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [activeIndex, setActiveIndex] = useState(0)
  const inputRef = useRef<HTMLInputElement>(null)
  const listRef = useRef<HTMLDivElement>(null)
  const router = useRouter()
  const { theme, setTheme, toggleSidebar } = useUIStore()

  const close = useCallback(() => {
    setOpen(false)
    setQuery('')
    setActiveIndex(0)
  }, [])

  const commands: CommandItem[] = [
    {
      id: 'dashboard',
      label: 'Go to Dashboard',
      icon: <Home className="h-4 w-4" />,
      shortcut: '⌘D',
      action: () => {
        router.push('/')
        close()
      },
    },
    {
      id: 'new-session',
      label: 'Create New Session',
      icon: <PlusCircle className="h-4 w-4" />,
      shortcut: '⌘N',
      action: () => {
        router.push('/sessions/new')
        close()
      },
    },
    {
      id: 'toggle-theme',
      label: 'Toggle Theme',
      icon: theme === 'dark' ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />,
      shortcut: '⌘T',
      action: () => {
        setTheme(theme === 'dark' ? 'light' : 'dark')
        close()
      },
    },
    {
      id: 'toggle-sidebar',
      label: 'Toggle Sidebar',
      icon: <PanelLeftClose className="h-4 w-4" />,
      shortcut: '⌘B',
      action: () => {
        toggleSidebar()
        close()
      },
    },
  ]

  const filtered = commands.filter((cmd) =>
    cmd.label.toLowerCase().includes(query.toLowerCase())
  )

  // Reset active index when filter changes
  useEffect(() => {
    setActiveIndex(0)
  }, [query])

  // Global keyboard shortcut to open
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key === 'k') {
        e.preventDefault()
        setOpen((prev) => !prev)
      }
    }
    document.addEventListener('keydown', handleKeyDown)
    return () => document.removeEventListener('keydown', handleKeyDown)
  }, [])

  // Auto-focus input when opened
  useEffect(() => {
    if (open) {
      requestAnimationFrame(() => {
        inputRef.current?.focus()
      })
    }
  }, [open])

  // Scroll active item into view
  useEffect(() => {
    if (!listRef.current) return
    const activeEl = listRef.current.querySelector('[data-active="true"]')
    activeEl?.scrollIntoView({ block: 'nearest' })
  }, [activeIndex])

  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      switch (e.key) {
        case 'ArrowDown':
          e.preventDefault()
          setActiveIndex((prev) => (prev + 1) % filtered.length)
          break
        case 'ArrowUp':
          e.preventDefault()
          setActiveIndex((prev) => (prev - 1 + filtered.length) % filtered.length)
          break
        case 'Enter':
          e.preventDefault()
          if (filtered[activeIndex]) {
            filtered[activeIndex].action()
          }
          break
        case 'Escape':
          e.preventDefault()
          close()
          break
      }
    },
    [filtered, activeIndex, close]
  )

  if (!open) return null

  return (
    <div
      data-testid="command-palette"
      className="fixed inset-0 z-50 flex items-start justify-center pt-[20vh]"
      onKeyDown={handleKeyDown}
    >
      {/* Backdrop */}
      <div
        data-testid="command-backdrop"
        className="absolute inset-0 bg-black/60 backdrop-blur-sm"
        onClick={close}
      />

      {/* Modal */}
      <div
        className={cn(
          'relative z-10 w-full max-w-lg overflow-hidden rounded-xl border',
          'border-[var(--color-border)] bg-[var(--color-bg-secondary)]/95',
          'shadow-2xl shadow-black/40 backdrop-blur-md'
        )}
      >
        {/* Search input */}
        <div className="flex items-center gap-3 border-b border-[var(--color-border)] px-4">
          <Search className="h-4 w-4 shrink-0 text-[var(--color-text-muted)]" />
          <input
            ref={inputRef}
            data-testid="command-input"
            type="text"
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Type a command..."
            className={cn(
              'h-12 w-full bg-transparent text-sm text-[var(--color-text-primary)]',
              'placeholder:text-[var(--color-text-muted)] outline-none'
            )}
          />
          <kbd
            className={cn(
              'hidden shrink-0 rounded border border-[var(--color-border)] px-1.5 py-0.5',
              'text-[10px] font-medium text-[var(--color-text-muted)] sm:inline-block'
            )}
          >
            ESC
          </kbd>
        </div>

        {/* Command list */}
        <div ref={listRef} className="max-h-72 overflow-y-auto p-2">
          {filtered.length === 0 ? (
            <div className="px-3 py-8 text-center text-sm text-[var(--color-text-muted)]">
              No commands found.
            </div>
          ) : (
            filtered.map((cmd, index) => (
              <button
                key={cmd.id}
                data-testid={`command-item-${index}`}
                data-active={index === activeIndex}
                onClick={() => cmd.action()}
                onMouseEnter={() => setActiveIndex(index)}
                className={cn(
                  'flex w-full items-center gap-3 rounded-lg px-3 py-2.5 text-sm',
                  'transition-colors duration-75',
                  index === activeIndex
                    ? 'bg-[var(--color-accent)]/15 text-[var(--color-text-primary)]'
                    : 'text-[var(--color-text-secondary)] hover:bg-[var(--color-accent)]/10'
                )}
              >
                <span
                  className={cn(
                    'flex h-8 w-8 shrink-0 items-center justify-center rounded-md',
                    'border border-[var(--color-border)] bg-[var(--color-bg-primary)]'
                  )}
                >
                  {cmd.icon}
                </span>
                <span className="flex-1 text-left">{cmd.label}</span>
                {cmd.shortcut && (
                  <kbd
                    className={cn(
                      'rounded border border-[var(--color-border)] px-1.5 py-0.5',
                      'text-[10px] font-medium text-[var(--color-text-muted)]'
                    )}
                  >
                    {cmd.shortcut}
                  </kbd>
                )}
              </button>
            ))
          )}
        </div>

        {/* Footer */}
        <div
          className={cn(
            'flex items-center justify-between border-t border-[var(--color-border)]',
            'px-4 py-2 text-[11px] text-[var(--color-text-muted)]'
          )}
        >
          <div className="flex items-center gap-2">
            <span className="flex items-center gap-1">
              <kbd className="rounded border border-[var(--color-border)] px-1 py-0.5 text-[10px]">↑↓</kbd>
              navigate
            </span>
            <span className="flex items-center gap-1">
              <kbd className="rounded border border-[var(--color-border)] px-1 py-0.5 text-[10px]">↵</kbd>
              select
            </span>
          </div>
          <div className="flex items-center gap-1">
            <Command className="h-3 w-3" />
            <span>Command Palette</span>
          </div>
        </div>
      </div>
    </div>
  )
}

export default CommandPalette
