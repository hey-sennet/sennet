// The agents, their status and their models, from /api/agents: fetched once and shared, again on
// `refreshAgents` (Settings does, as it opens). Asking runs each agent's CLI, so it isn't polled.
import { useSyncExternalStore } from 'react'
import { api, type AgentStatus } from './api'

type Agents = Record<string, AgentStatus> | null

let agents: Agents = null
let loading: Promise<void> | null = null
const listeners = new Set<() => void>()

export function refreshAgents() {
  loading = api.agents().then((result) => {
    agents = result
  }, () => {
    loading = null // try again next time
  }).finally(() => listeners.forEach((listener) => listener()))
  return loading
}

function subscribe(listener: () => void) {
  listeners.add(listener)
  if (!loading) void refreshAgents()
  return () => listeners.delete(listener)
}

export function useAgents(): Agents {
  return useSyncExternalStore(subscribe, () => agents)
}
