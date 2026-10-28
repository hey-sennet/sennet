import { describe, expect, it } from 'vitest'
import type { Message, TaskDetail, TaskEvent, TaskStatus } from './api'
import { duration, ranFor, startTime } from './timing'

let id = 0
const ask = (at: number, state: Message['state'] = 'done'): Message =>
  ({ id: ++id, task_id: 1, role: 'user', text: 'do it', state, attachments: [], created_at: at })
const said = (at: number): Message => ({ ...ask(at), role: 'agent' })
const event = (kind: string, at: number, data: Record<string, unknown> = {}): TaskEvent => ({ id: ++id, kind, data, created_at: at })
const done = (at: number) => event('turn_completed', at, { status: 'completed', error: null })
const task = (status: TaskStatus, messages: Message[], events: TaskEvent[], updated_at = 0) =>
  ({ status, messages, events, updated_at }) as TaskDetail

describe('timing', () => {
  it('sums its turns, not the time between them', () => {
    expect(ranFor(task('completed', [ask(100), said(150), ask(1000)], [done(160), done(1100)]))).toBe(160)
  })

  it('counts a request sent during a turn from when that turn ends', () => {
    expect(ranFor(task('completed', [ask(100), ask(120)], [done(200), done(260)]))).toBe(160)
  })

  it('counts a turn the agent started, and not a request that was cancelled', () => {
    const events = [done(200), event('turn_started', 500, { by: 'agent' }), done(530)]
    expect(ranFor(task('completed', [ask(100), ask(150, 'cancelled')], events))).toBe(130)
  })

  it('runs to now while it works, and to its last change once it has stopped', () => {
    expect(ranFor(task('running', [ask(100)], []), 400)).toBe(300)
    expect(ranFor(task('awaiting_approval', [ask(100)], []), 400)).toBe(300)
    expect(ranFor(task('cancelled', [ask(100)], [], 130), 400)).toBe(30)
  })

  it('says how long in words', () => {
    expect(duration(0)).toBe('under a minute')
    expect(duration(59)).toBe('under a minute')
    expect(duration(3 * 60 + 20)).toBe('3 min')
    expect(duration(80 * 60)).toBe('1 hr 20 min')
    expect(duration(2 * 3600)).toBe('2 hr')
  })

  it('says when it started', () => {
    const now = new Date(2026, 9, 28, 16, 0).getTime()
    const at = (day: number, hour: number, minute = 0) => new Date(2026, 9, day, hour, minute).getTime() / 1000
    expect(startTime(at(28, 14, 14), now)).toBe('2:14 pm')
    expect(startTime(at(27, 9), now)).toBe('yesterday 9 am')
    expect(startTime(at(25, 23, 5), now)).toBe('Sun 11:05 pm')
    expect(startTime(at(3, 10), now)).toBe('3 Oct')
  })
})
