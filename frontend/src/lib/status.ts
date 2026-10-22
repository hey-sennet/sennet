import type { TaskStatus } from './api'

export const STATUS_LABELS: Record<TaskStatus, string> = {
  queued: 'Queued',
  running: 'Working',
  awaiting_approval: 'Needs you',
  background: 'Working in the background',
  completed: 'Done',
  failed: 'Failed',
  cancelled: 'Cancelled',
  unknown: 'Interrupted',
}
