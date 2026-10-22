import type { TaskStatus } from '@/lib/api'
import { STATUS_LABELS } from '@/lib/status'
import { cn } from '@/lib/utils'

// Strata's palette: cream while working, amber when it needs you, quiet once settled.
const TONES: Record<TaskStatus, string> = {
  queued: 'text-muted-foreground',
  running: 'text-foreground',
  awaiting_approval: 'text-warn',
  background: 'text-foreground',
  completed: 'text-muted-foreground',
  failed: 'text-destructive',
  cancelled: 'text-muted-foreground',
  unknown: 'text-warn',
}

export function StatusDot({ status, className }: { status: TaskStatus; className?: string }) {
  const active = status === 'running' || status === 'awaiting_approval' || status === 'background'
  return <span className={cn('size-1.5 shrink-0 rounded-full bg-current', TONES[status], active && 'animate-pulse', className)} aria-hidden />
}

export function StatusBadge({ status, className }: { status: TaskStatus; className?: string }) {
  return (
    <span className={cn('inline-flex items-center gap-1.5 text-xs font-medium whitespace-nowrap', TONES[status], className)}>
      <StatusDot status={status} />
      {STATUS_LABELS[status]}
    </span>
  )
}
