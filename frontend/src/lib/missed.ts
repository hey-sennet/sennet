import type { Message, TaskEvent } from './api'

/**
 * Each finished turn's answer: its last message before `turn_completed` (agents/base.py). The
 * short updates written on the way aren't answers, and a turn still running has none yet.
 */
export function answers(messages: Message[], events: TaskEvent[]): Message[] {
  const said = messages.filter((m) => m.role === 'agent').sort((a, b) => a.created_at - b.created_at)
  const found: Message[] = []
  let from = 0
  for (const event of events) {
    if (event.kind !== 'turn_completed') continue
    let last: Message | undefined
    while (from < said.length && said[from].created_at <= event.created_at) last = said[from++]
    if (last && event.data.status === 'completed') found.push(last)
  }
  return found
}

/**
 * The answers written after `after`, the newest message there was when you left the task, oldest
 * first. Without it (the task wasn't open here since the page loaded), the last answer.
 */
export function missedAnswers(messages: Message[], events: TaskEvent[], after: number | null): Message[] {
  const all = answers(messages, events)
  return after === null ? all.slice(-1) : all.filter((m) => m.created_at > after)
}

/** When the newest message on a task was written: what you've seen of it, when you leave it. */
export function seenUntil(messages: Message[]): number {
  return messages.reduce((latest, m) => Math.max(latest, m.created_at), 0)
}
