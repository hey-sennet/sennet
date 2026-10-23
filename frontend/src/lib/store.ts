import { useCallback, useEffect, useRef, useState } from 'react'
import { api, type Approval, type Project, type Rule, type ServerEvent, type State, type Task, type TaskDetail } from './api'
import { markUnpaired, UNPAIRED } from './pairing'

export type Connection = 'connecting' | 'live' | 'reconnecting'

interface Live {
  projects: Project[]
  tasks: Task[]
  approvals: Approval[]
  rules: Rule[]
  settings: State['settings'] | null
}

const empty: Live = { projects: [], tasks: [], approvals: [], rules: [], settings: null }

function upsert<T extends { id: number }>(items: T[], item: T, first = false): T[] {
  const index = items.findIndex((existing) => existing.id === item.id)
  if (index < 0) return first ? [item, ...items] : [...items, item]
  const next = items.slice()
  next[index] = item
  return next
}

/** Server state plus live events. Reloads everything on reconnect, so missed events never matter. */
export function useLiveState() {
  const [live, setLive] = useState<Live>(empty)
  const [connection, setConnection] = useState<Connection>('connecting')
  const listeners = useRef(new Set<(event: ServerEvent) => void>())

  const reload = useCallback(async () => {
    const state = await api.state()
    setLive({ ...state })
  }, [])

  useEffect(() => {
    let socket: WebSocket | undefined
    let retry: ReturnType<typeof setTimeout> | undefined
    let attempt = 0
    let stopped = false

    const connect = () => {
      const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:'
      socket = new WebSocket(`${protocol}//${location.host}/api/events`)
      socket.onopen = async () => {
        attempt = 0
        try {
          await reload()
          setConnection('live')
        } catch {
          socket?.close()
        }
      }
      socket.onmessage = ({ data }) => {
        const event = JSON.parse(data) as ServerEvent
        setLive((state) => apply(state, event))
        listeners.current.forEach((listener) => listener(event))
      }
      socket.onclose = ({ code }) => {
        if (stopped) return
        if (code === UNPAIRED) return markUnpaired()
        setConnection('reconnecting')
        retry = setTimeout(connect, Math.min(10000, 500 * 2 ** attempt++))
      }
    }
    connect()
    return () => {
      stopped = true
      clearTimeout(retry)
      socket?.close()
    }
  }, [reload])

  const subscribe = useCallback((listener: (event: ServerEvent) => void) => {
    listeners.current.add(listener)
    return () => void listeners.current.delete(listener)
  }, [])

  return { ...live, connection, reload, subscribe }
}

function apply(state: Live, event: ServerEvent): Live {
  switch (event.type) {
    case 'task':
      return { ...state, tasks: upsert(state.tasks, event.task, true).sort((a, b) => b.updated_at - a.updated_at) }
    case 'approval': {
      const approvals = event.approval.status === 'pending'
        ? upsert(state.approvals, event.approval)
        : state.approvals.filter((a) => a.id !== event.approval.id)
      return { ...state, approvals }
    }
    case 'project':
      return { ...state, projects: upsert(state.projects, event.project).sort((a, b) => a.name.localeCompare(b.name)) }
    case 'project_removed':
      return { ...state, projects: state.projects.filter((p) => p.id !== event.project_id) }
    case 'rules':
      return { ...state, rules: event.rules }
    default:
      return state
  }
}

/** Full detail for one task, kept current from live events, including streamed text. */
export function useTaskDetail(taskId: number | null, subscribe: ReturnType<typeof useLiveState>['subscribe']) {
  const [detail, setDetail] = useState<TaskDetail | null>(null)
  const [streaming, setStreaming] = useState<Record<string, string>>({})

  useEffect(() => {
    setDetail(null)
    setStreaming({})
    if (taskId === null) return
    let current = true
    api.task(taskId).then((task) => current && setDetail(task)).catch(() => current && setDetail(null))
    const unsubscribe = subscribe((event) => {
      if ('task_id' in event && event.task_id !== taskId) return
      if (event.type === 'task' && event.task.id !== taskId) return
      setDetail((detail) => {
        if (!detail) return detail
        switch (event.type) {
          case 'task':
            return { ...detail, ...event.task }
          case 'message':
            return { ...detail, messages: upsert(detail.messages, event.message) }
          case 'event':
            return { ...detail, events: [...detail.events, event.event] }
          default:
            return detail
        }
      })
      if (event.type === 'delta') {
        setStreaming((s) => ({ ...s, [event.item_id]: (s[event.item_id] ?? '') + event.delta }))
      } else if (event.type === 'message' && event.message.role === 'agent') {
        setStreaming({})
      }
    })
    return () => {
      current = false
      unsubscribe()
    }
  }, [taskId, subscribe])

  return { detail, streaming: Object.values(streaming).join('\n\n') }
}
