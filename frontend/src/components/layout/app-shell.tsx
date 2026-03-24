'use client'

import { Sidebar } from './sidebar'
import { Topbar } from './topbar'
import CommandPalette from './command-palette'
import { useUIStore } from '@/stores/ui-store'
import { cn } from '@/lib/utils'

export function AppShell({ children }: { children: React.ReactNode }) {
  const sidebarOpen = useUIStore((s) => s.sidebarOpen)

  return (
    <div className="flex min-h-screen bg-[var(--bg-page)]">
      <Sidebar />
      <div
        className={cn(
          'flex-1 transition-all duration-300',
          sidebarOpen ? 'ml-64' : 'ml-16'
        )}
      >
        <Topbar />
        <main className="p-6 overflow-auto">{children}</main>
      </div>
      <CommandPalette />
    </div>
  )
}
