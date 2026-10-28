// When a task started and how long it ran, for its header.
import type { TaskDetail } from './api'
import { clockTime, startOfDay } from './limits'

/**
 * Seconds the task's turns ran, summed: from a request (or a turn the agent started itself) to its
 * `turn_completed`; the time between turns isn't counted. A request sent during a turn waits for it,
 * so its own turn starts as that one ends. A turn still open runs to `now` while the task works,
 * else to the task's last change (a stop that left no `turn_completed`).
 */
export function ranFor(detail: TaskDetail, now = Date.now() / 1000): number {
  const points = [
    ...detail.messages.filter((m) => m.role === 'user' && m.state !== 'cancelled').map((m) => ({ at: m.created_at, start: true })),
    ...detail.events.filter((e) => e.kind === 'turn_started' && e.data.by === 'agent').map((e) => ({ at: e.created_at, start: true })),
    ...detail.events.filter((e) => e.kind === 'turn_completed').map((e) => ({ at: e.created_at, start: false })),
  ].sort((a, b) => a.at - b.at)
  let total = 0
  let since: number | null = null // the open turn's start
  let waiting = 0 // requests sent during it, each a turn of its own after it
  for (const point of points) {
    if (point.start && since === null) since = point.at
    else if (point.start) waiting++
    else if (since !== null) {
      total += point.at - since
      since = waiting ? (waiting--, point.at) : null
    }
  }
  if (since !== null) {
    const working = ['running', 'awaiting_approval', 'queued'].includes(detail.status)
    total += Math.max(0, (working ? now : detail.updated_at) - since)
  }
  return total
}

/** "under a minute", "3 min", "1 hr 20 min" or "2 hr". */
export function duration(seconds: number): string {
  const minutes = Math.floor(seconds / 60)
  if (minutes < 1) return 'under a minute'
  if (minutes < 60) return `${minutes} min`
  const hours = Math.floor(minutes / 60)
  return minutes % 60 ? `${hours} hr ${minutes % 60} min` : `${hours} hr`
}

/** "2:14 pm" today, "yesterday 2:14 pm", "Tue 2:14 pm" this week, else "3 Nov". */
export function startTime(at: number, now = Date.now()): string {
  const moment = new Date(at * 1000)
  const days = Math.round((startOfDay(new Date(now)) - startOfDay(moment)) / 86_400_000)
  if (days <= 0) return clockTime(moment)
  if (days === 1) return `yesterday ${clockTime(moment)}`
  if (days < 7) return `${moment.toLocaleDateString('en-GB', { weekday: 'short' })} ${clockTime(moment)}`
  return moment.toLocaleDateString('en-GB', { day: 'numeric', month: 'short' })
}
