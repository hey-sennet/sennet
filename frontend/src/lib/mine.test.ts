import { describe, expect, it } from 'vitest'
import type { Approval } from './api'
import { approvalsHere } from './mine'

const approval = (id: number, task_id: number) => ({ id, task_id }) as Approval

describe('approvalsHere', () => {
  const approvals = [approval(1, 10), approval(2, 20), approval(3, 10)]

  it('asks about the selected task only', () => {
    const { here, elsewhere } = approvalsHere(approvals, 10, new Set([20]))
    expect(here.map((a) => a.id)).toEqual([1, 3])
    expect(elsewhere.map((a) => a.id)).toEqual([2])
  })

  it('with nothing selected, asks about tasks this device started', () => {
    const { here, elsewhere } = approvalsHere(approvals, null, new Set([20]))
    expect(here.map((a) => a.id)).toEqual([2])
    expect(elsewhere.map((a) => a.id)).toEqual([1, 3])
  })

  it('asks about nothing when this device started none of them', () => {
    expect(approvalsHere(approvals, null, new Set()).here).toEqual([])
  })
})
