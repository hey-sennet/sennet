import { describe, expect, it } from 'vitest'
import type { Task } from '@/lib/api'
import { groupTasks } from './TaskList'

const task = (id: number, status: string) => ({ id, status }) as Task

describe('groupTasks', () => {
  it('puts tasks waiting on you first, then those at work, then the rest, keeping their order', () => {
    const tasks = [task(1, 'completed'), task(2, 'running'), task(3, 'awaiting_approval'), task(4, 'queued'),
      task(5, 'failed'), task(6, 'background'), task(7, 'running')]
    const groups = groupTasks(tasks, new Set([7]))
    expect(groups.map((g) => [g.title, g.tasks.map((t) => t.id)])).toEqual([
      ['Needs you', [3, 7]], ['Working', [2, 4, 6]], ['Earlier', [1, 5]]])
  })
})
