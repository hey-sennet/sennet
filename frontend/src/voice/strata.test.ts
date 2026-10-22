import { describe, expect, it } from 'vitest'
import { strataState } from './strata'

const rest = { listening: false, opening: false, speaking: false, state: 'idle' } as const

describe('strataState', () => {
  it('listens once the microphone captures', () => {
    expect(strataState({ ...rest, listening: true, opening: true })).toBe('idle')
    expect(strataState({ ...rest, listening: true, state: 'listening' }, { approvalPending: true })).toBe('listening')
  })

  it('speaks while a reply plays or the server is still sending one', () => {
    expect(strataState({ ...rest, speaking: true }, { approvalPending: true, taskRunning: true })).toBe('speaking')
    expect(strataState({ ...rest, state: 'speaking' })).toBe('speaking')
  })

  it('thinks while transcribing or while the task runs', () => {
    expect(strataState({ ...rest, state: 'transcribing' }, { approvalPending: true })).toBe('thinking')
    expect(strataState(rest, { taskRunning: true })).toBe('thinking')
  })

  it('shows a pending approval between turns, ahead of a running task', () => {
    expect(strataState(rest, { approvalPending: true, taskRunning: true })).toBe('approval')
  })

  it('rests otherwise', () => {
    expect(strataState(rest)).toBe('idle')
  })
})
