import { useEffect, useCallback } from 'react'

export interface ShortcutMap {
  [key: string]: () => void
}

interface UseKeyboardShortcutsOptions {
  enabled?: boolean
}

function isInputElement(target: EventTarget | null): boolean {
  if (!target || !(target instanceof HTMLElement)) return false
  const tag = target.tagName.toLowerCase()
  if (tag === 'input' || tag === 'textarea' || tag === 'select') return true
  if (target.isContentEditable) return true
  // Fallback: check both property and attribute for contenteditable
  const ceAttr = target.getAttribute('contenteditable')
  const ceProp = target.contentEditable
  return ceAttr === 'true' || ceProp === 'true'
}

function normalizeKey(event: KeyboardEvent): string {
  const parts: string[] = []
  if (event.metaKey || event.ctrlKey) parts.push('mod')
  if (event.shiftKey) parts.push('shift')
  if (event.altKey) parts.push('alt')

  const key = event.key.toLowerCase()
  // Avoid duplicating modifier names
  if (!['control', 'meta', 'shift', 'alt'].includes(key)) {
    parts.push(key)
  }

  return parts.join('+')
}

export function useKeyboardShortcuts(
  shortcuts: ShortcutMap,
  options: UseKeyboardShortcutsOptions = {}
) {
  const { enabled = true } = options

  const handleKeyDown = useCallback(
    (event: KeyboardEvent) => {
      if (!enabled) return
      if (isInputElement(event.target)) return

      const key = normalizeKey(event)
      const handler = shortcuts[key]
      if (handler) {
        event.preventDefault()
        handler()
      }
    },
    [shortcuts, enabled]
  )

  useEffect(() => {
    if (!enabled) return

    document.addEventListener('keydown', handleKeyDown)
    return () => {
      document.removeEventListener('keydown', handleKeyDown)
    }
  }, [handleKeyDown, enabled])
}
