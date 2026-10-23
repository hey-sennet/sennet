import { describe, expect, it } from 'vitest'
import type { Message, ServerEvent, TaskDetail } from './api'
import { applyEvent } from './store'

const message = (id: number, state: string, text = 'hi') => ({ id, task_id: 1, role: 'agent', text, state, created_at: 0 }) as unknown as Message
const detail = (messages: Message[], updated_at = 10) =>
  ({ id: 1, status: 'completed', updated_at, messages, events: [] }) as unknown as TaskDetail

describe('applyEvent', () => {
  it('adds a reply the snapshot missed', () => {
    const event = { type: 'message', task_id: 1, message: message(2, 'done', 'You said: first') } as ServerEvent
    expect(applyEvent(detail([message(1, 'done')]), event, true).messages.map((m) => m.id)).toEqual([1, 2])
  })

  it('never rolls the snapshot back with an event held from before it', () => {
    const snapshot = detail([message(1, 'done')])
    expect(applyEvent(snapshot, { type: 'message', task_id: 1, message: message(1, 'sent') } as ServerEvent, true)).toBe(snapshot)
    const older = { type: 'task', task: { id: 1, status: 'running', updated_at: 5 } } as ServerEvent
    expect(applyEvent(snapshot, older, true).status).toBe('completed')
  })

  it('applies live events as they come', () => {
    const live = { type: 'message', task_id: 1, message: message(1, 'cancelled') } as ServerEvent
    expect(applyEvent(detail([message(1, 'queued')]), live).messages[0].state).toBe('cancelled')
  })
})
