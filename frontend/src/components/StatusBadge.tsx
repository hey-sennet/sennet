import type { TaskStatus } from '@/lib/api'
import { cn } from '@/lib/utils'

const LABELS: Record<TaskStatus, string> = {
  queued: 'Queued',
  running: 'Working',
  awaiting_approval: 'Needs you',
  completed: 'Done',
  failed: 'Failed',
  cancelled: 'Cancelled',
  unknown: 'Interrupted',
}

const TONES: Record<TaskStatus, string> = {
  queued: 'text-muted-foreground',
  running: 'text-sky-400',
  awaiting_approval: 'text-amber-400',
  completed: 'text-emerald-400',
  failed: 'text-red-400',
  cancelled: 'text-muted-foreground',
  unknown: 'text-orange-400',
}

export function StatusBadge({ status, className }: { status: TaskStatus; className?: string }) {
  const active = status === 'running' || status === 'awaiting_approval'
  return (
    <span className={cn('inline-flex items-center gap-1.5 text-xs font-medium whitespace-nowrap', TONES[status], className)}>
      <span className={cn('size-1.5 rounded-full bg-current', active && 'animate-pulse')} aria-hidden />
      {LABELS[status]}
    </span>
  )
}
