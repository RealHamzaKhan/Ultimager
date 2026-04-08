'use client'

import { usePathname } from 'next/navigation'
import { useUIStore } from '@/stores/ui-store'
import { Sun, Moon, Search } from 'lucide-react'
import { Button } from '@/components/ui/button'

function getBreadcrumb(pathname: string): string[] {
  if (pathname === '/') return ['Dashboard']
  const parts = pathname.split('/').filter(Boolean)
  return parts.map((p) => {
    if (p === 'sessions') return 'Sessions'
    if (p === 'new') return 'New'
    if (p === 'results') return 'Results'
    return p
  })
}

export function Topbar() {
  const pathname = usePathname()
  const { theme, setTheme } = useUIStore()
  const breadcrumb = getBreadcrumb(pathname)

  return (
    <header className="sticky top-0 z-20 flex h-16 items-center justify-between border-b border-[var(--border)] bg-[var(--bg-card)]/80 backdrop-blur-sm px-6">
      {/* Breadcrumb */}
      <nav aria-label="Breadcrumb">
        <ol className="flex items-center gap-2 text-sm">
          {breadcrumb.map((item, i) => (
            <li key={i} className="flex items-center gap-2">
              {i > 0 && <span className="text-[var(--text-muted)]">/</span>}
              <span
                className={
                  i === breadcrumb.length - 1
                    ? 'text-[var(--text-primary)] font-medium'
                    : 'text-[var(--text-muted)]'
                }
              >
                {item}
              </span>
            </li>
          ))}
        </ol>
      </nav>

      {/* Actions */}
      <div className="flex items-center gap-2">
        <Button variant="ghost" size="sm" aria-label="Search">
          <Search className="h-4 w-4" />
        </Button>
        <Button
          variant="ghost"
          size="sm"
          onClick={() => setTheme(theme === 'dark' ? 'light' : 'dark')}
          aria-label="Toggle theme"
        >
          {theme === 'dark' ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
        </Button>
      </div>
    </header>
  )
}
