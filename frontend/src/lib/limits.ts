// Usage limits and token counts in words (src/sennet/usage.py says the same out loud).
import type { AgentLimits, LimitWindow } from './api'

/** From here a window is worth showing on the main screen, as it is worth a spoken word. */
export const WARN_AT = 80

/** The window most in the way: a full one that frees up last, else the fullest past WARN_AT. */
export function pressing(limits: AgentLimits | undefined): LimitWindow | null {
  const windows = limits?.windows ?? []
  const full = windows.filter((w) => w.used >= 100).sort((a, b) => (a.resets_at ?? 0) - (b.resets_at ?? 0))
  if (full.length) return full[full.length - 1]
  const close = windows.filter((w) => w.used >= WARN_AT).sort((a, b) => b.used - a.used)
  return close[0] ?? null
}

/** "4 pm", "tomorrow 9:30 am", "Sun 9 am" or "3 Nov", in this device's time. */
export function resetTime(at: number, now = Date.now()): string {
  const moment = new Date(Math.round(at / 60) * 60 * 1000)
  const hours = moment.getHours() % 12 || 12
  const clock = `${hours}${moment.getMinutes() ? `:${String(moment.getMinutes()).padStart(2, '0')}` : ''} ${moment.getHours() < 12 ? 'am' : 'pm'}`
  const days = Math.round((startOfDay(moment) - startOfDay(new Date(now))) / 86_400_000)
  if (days <= 0) return clock
  if (days === 1) return `tomorrow ${clock}`
  if (days < 7) return `${moment.toLocaleDateString('en-GB', { weekday: 'short' })} ${clock}`
  return moment.toLocaleDateString('en-GB', { day: 'numeric', month: 'short' })
}

function startOfDay(date: Date): number {
  return new Date(date.getFullYear(), date.getMonth(), date.getDate()).getTime()
}

/** "Claude Code: 5-hour limit reached · resets 4 pm", or "…: 85% of the 5-hour limit used · …". */
export function limitLine(agentTitle: string, window: LimitWindow, now = Date.now()): string {
  const state = window.used >= 100 ? `${window.title} limit reached` : `${window.used}% of the ${window.title} limit used`
  return `${agentTitle}: ${state}${window.resets_at ? ` · resets ${resetTime(window.resets_at, now)}` : ''}`
}

/** 950, 12.4k, 3.1M. */
export function tokenCount(n: number): string {
  if (n < 1000) return String(n)
  if (n < 1_000_000) return `${(n / 1000).toFixed(n < 10_000 ? 1 : 0).replace(/\.0$/, '')}k`
  return `${(n / 1_000_000).toFixed(1).replace(/\.0$/, '')}M`
}
