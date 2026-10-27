import { describe, expect, it } from 'vitest'
import type { Message, TaskEvent } from './api'
import { answers, missedAnswers, seenUntil } from './missed'

const message = (id: number, role: Message['role'], created_at: number) => ({ id, role, created_at }) as Message
const ended = (created_at: number, status = 'completed') => ({ id: 0, kind: 'turn_completed', data: { status }, created_at }) as TaskEvent
const started = (created_at: number) => ({ id: 0, kind: 'turn_started', data: {}, created_at }) as TaskEvent

describe('answers', () => {
  it('is the last message of each finished turn', () => {
    const messages = [message(1, 'user', 1), message(2, 'agent', 2), message(3, 'agent', 3),
      message(4, 'user', 5), message(5, 'agent', 6)]
    expect(answers(messages, [started(1), ended(4), started(5), ended(7)]).map((m) => m.id)).toEqual([3, 5])
  })

  it('has none for a turn still running, or one that failed or was stopped', () => {
    const messages = [message(1, 'agent', 1), message(2, 'agent', 3), message(3, 'agent', 5)]
    expect(answers(messages, [ended(2, 'failed'), ended(4, 'cancelled')])).toEqual([])
  })

  it('takes nothing from an earlier turn for one that wrote nothing', () => {
    expect(answers([message(1, 'agent', 1)], [ended(2), ended(3)]).map((m) => m.id)).toEqual([1])
  })
})

describe('missedAnswers', () => {
  const messages = [message(1, 'agent', 1), message(2, 'user', 3), message(3, 'agent', 4)]
  const events = [ended(2), ended(5)]

  it('is the answers written since you left, oldest first', () => {
    expect(missedAnswers(messages, events, 1).map((m) => m.id)).toEqual([3])
    expect(missedAnswers(messages, events, 0).map((m) => m.id)).toEqual([1, 3])
  })

  it('is the last answer when it is not known when you left', () => {
    expect(missedAnswers(messages, events, null).map((m) => m.id)).toEqual([3])
  })

  it('is nothing when nothing finished since', () => {
    expect(missedAnswers(messages, events, 4)).toEqual([])
  })
})

it('a task is seen until its newest message', () => {
  expect(seenUntil([message(1, 'user', 3), message(2, 'agent', 2)])).toBe(3)
  expect(seenUntil([])).toBe(0)
})
