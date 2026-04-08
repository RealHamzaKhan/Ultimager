import { renderHook } from '@testing-library/react'
import { vi, describe, it, expect, beforeEach, afterEach } from 'vitest'
import { useKeyboardShortcuts } from '@/hooks/use-keyboard-shortcuts'

function fireKeydown(
  key: string,
  options: Partial<KeyboardEventInit> = {},
  target?: HTMLElement
) {
  const event = new KeyboardEvent('keydown', {
    key,
    bubbles: true,
    cancelable: true,
    ...options,
  })
  const dispatcher = target ?? document
  dispatcher.dispatchEvent(event)
  return event
}

describe('useKeyboardShortcuts', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('fires handler on matching keydown', () => {
    const handler = vi.fn()
    renderHook(() =>
      useKeyboardShortcuts({ escape: handler })
    )

    fireKeydown('Escape')
    expect(handler).toHaveBeenCalledTimes(1)
  })

  it('fires mod+k handler for Cmd+K', () => {
    const handler = vi.fn()
    renderHook(() =>
      useKeyboardShortcuts({ 'mod+k': handler })
    )

    fireKeydown('k', { metaKey: true })
    expect(handler).toHaveBeenCalledTimes(1)
  })

  it('fires mod+k handler for Ctrl+K', () => {
    const handler = vi.fn()
    renderHook(() =>
      useKeyboardShortcuts({ 'mod+k': handler })
    )

    fireKeydown('k', { ctrlKey: true })
    expect(handler).toHaveBeenCalledTimes(1)
  })

  it('handles arrow key shortcuts', () => {
    const upHandler = vi.fn()
    const downHandler = vi.fn()
    renderHook(() =>
      useKeyboardShortcuts({
        arrowup: upHandler,
        arrowdown: downHandler,
      })
    )

    fireKeydown('ArrowUp')
    fireKeydown('ArrowDown')

    expect(upHandler).toHaveBeenCalledTimes(1)
    expect(downHandler).toHaveBeenCalledTimes(1)
  })

  it('does not fire when focus is in an input element', () => {
    const handler = vi.fn()
    renderHook(() =>
      useKeyboardShortcuts({ escape: handler })
    )

    const input = document.createElement('input')
    document.body.appendChild(input)

    fireKeydown('Escape', {}, input)

    expect(handler).not.toHaveBeenCalled()
    document.body.removeChild(input)
  })

  it('does not fire when focus is in a textarea', () => {
    const handler = vi.fn()
    renderHook(() =>
      useKeyboardShortcuts({ escape: handler })
    )

    const textarea = document.createElement('textarea')
    document.body.appendChild(textarea)

    fireKeydown('Escape', {}, textarea)

    expect(handler).not.toHaveBeenCalled()
    document.body.removeChild(textarea)
  })

  it('does not fire when focus is in a contentEditable element', () => {
    const handler = vi.fn()
    renderHook(() =>
      useKeyboardShortcuts({ escape: handler })
    )

    const div = document.createElement('div')
    div.setAttribute('contenteditable', 'true')
    div.contentEditable = 'true'
    document.body.appendChild(div)

    fireKeydown('Escape', {}, div)

    expect(handler).not.toHaveBeenCalled()
    document.body.removeChild(div)
  })

  it('does not fire when disabled', () => {
    const handler = vi.fn()
    renderHook(() =>
      useKeyboardShortcuts({ escape: handler }, { enabled: false })
    )

    fireKeydown('Escape')
    expect(handler).not.toHaveBeenCalled()
  })

  it('ignores unregistered keys', () => {
    const handler = vi.fn()
    renderHook(() =>
      useKeyboardShortcuts({ escape: handler })
    )

    fireKeydown('a')
    expect(handler).not.toHaveBeenCalled()
  })

  it('removes event listener on unmount', () => {
    const handler = vi.fn()
    const { unmount } = renderHook(() =>
      useKeyboardShortcuts({ escape: handler })
    )

    unmount()

    fireKeydown('Escape')
    expect(handler).not.toHaveBeenCalled()
  })

  it('handles shift modifier', () => {
    const handler = vi.fn()
    renderHook(() =>
      useKeyboardShortcuts({ 'shift+?': handler })
    )

    fireKeydown('?', { shiftKey: true })
    expect(handler).toHaveBeenCalledTimes(1)
  })
})
