// Whether this browser is paired (docs/m5-access.md). Unknown until the first check; a 401 from the
// API or a socket closed with UNPAIRED marks it unpaired, and the app shows the pairing screen.
// Pairing reloads the page, so nothing here goes back to paired.
import { useSyncExternalStore } from 'react'
import { api, whenUnpaired } from './api'

export const UNPAIRED = 4401 // the close code of a socket from an unpaired or revoked device

type Paired = 'checking' | 'paired' | 'unpaired'

let paired: Paired = 'checking'
const listeners = new Set<() => void>()

function set(next: Paired) {
  if (paired === 'unpaired') return
  paired = next
  listeners.forEach((listener) => listener())
}

export function markUnpaired() {
  set('unpaired')
}

whenUnpaired(markUnpaired)

/** Ask once whether this browser is paired; a link to /pair is for pairing it, whatever the answer. */
export function checkPaired() {
  if (location.pathname === '/pair') return markUnpaired()
  api.devices().then(() => set('paired'), () => {
    // A 401 has already marked it unpaired; anything else (the server restarting) shows the app,
    // which reconnects.
    set('paired')
  })
}

export function usePaired(): Paired {
  return useSyncExternalStore((listener) => {
    listeners.add(listener)
    return () => listeners.delete(listener)
  }, () => paired)
}
