import { useState } from 'react'
import type { Approval } from './api'

// The tasks this device started or followed up, so its stage asks only about those
// (voice-protocol §8). Kept in the browser so a reload remembers them; storage can be
// missing or blocked, and then it lasts as long as the page.
const KEY = 'sennet.mine'
const LIMIT = 200

function load(): number[] {
  try {
    const stored = JSON.parse(localStorage.getItem(KEY) ?? '[]')
    return Array.isArray(stored) ? stored.filter((id) => typeof id === 'number') : []
  } catch {
    return []
  }
}

export function useMine(): [ReadonlySet<number>, (taskId: number) => void] {
  const [mine, setMine] = useState<ReadonlySet<number>>(() => new Set(load()))
  const remember = (taskId: number) => setMine((current) => {
    if (current.has(taskId)) return current
    const next = [...current, taskId].slice(-LIMIT)
    try {
      localStorage.setItem(KEY, JSON.stringify(next))
    } catch {
      // not kept past this page
    }
    return new Set(next)
  })
  return [mine, remember]
}

/** The approvals this device's stage asks about: the selected task's, or with none selected,
 *  those of tasks this device started. The rest are left to the device following them. */
export function approvalsHere(approvals: Approval[], selected: number | null, mine: ReadonlySet<number>) {
  const here = approvals.filter((a) => (selected !== null ? a.task_id === selected : mine.has(a.task_id)))
  return { here, elsewhere: approvals.filter((a) => !here.includes(a)) }
}
