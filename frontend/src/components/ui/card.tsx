'use client'

import { cn } from '@/lib/utils'

export interface CardProps extends React.HTMLAttributes<HTMLDivElement> {
  children: React.ReactNode
  className?: string
  onClick?: () => void
}

export function Card({ children, className, onClick, ...rest }: CardProps) {
  return (
    <div
      className={cn(
        'rounded-xl border bg-[var(--bg-card)] border-[var(--border)] p-6 shadow-[var(--shadow-sm)]',
        onClick && 'cursor-pointer hover:bg-[var(--bg-card-hover)] transition-colors',
        className
      )}
      onClick={onClick}
      role={onClick ? 'button' : undefined}
      {...rest}
    >
      {children}
    </div>
  )
}

export function CardHeader({ children, className }: { children: React.ReactNode; className?: string }) {
  return <div className={cn('mb-4', className)}>{children}</div>
}

export function CardTitle({ children, className }: { children: React.ReactNode; className?: string }) {
  return <h3 className={cn('text-lg font-semibold text-[var(--text-primary)]', className)}>{children}</h3>
}

export function CardContent({ children, className }: { children: React.ReactNode; className?: string }) {
  return <div className={cn(className)}>{children}</div>
}
