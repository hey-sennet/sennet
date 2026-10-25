import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from 'react'
import { api, type AgentLimits, type Approval, type Project, type Rule, type ServerEvent, type State, type Task, type TaskDetail } from './api'
import { markUnpaired, UNPAIRED } from './pairing'

export type Connection = 'connecting' | 'live' | 'reconnecting'

interface Live {
  version: string | null
  projects: Project[]
  tasks: Task[]
  approvals: Approval[]
  rules: Rule[]
  limits: Record<string, AgentLimits>
  settings: State['settings'] | null
  more_tasks: boolean
}

const CONNECT_TIMEOUT_MS = 5_000         // a connection not open by then is tried again
const FRESH_AFTER_HIDDEN_MS = 10_000     // back on screen after this long hidden, connect afresh
// While showing, the page says so this often: the server doesn't send this device push notifications
// meanwhile, and counts a page that stops saying so as gone (src/sennet/push.py's SHOWING_S).
const SHOWING_EVERY_MS = 30_000
const empty: Live = { version: null, projects: [], tasks: [], approvals: [], rules: [], limits: {}, settings: null, more_tasks: false }

/** Finished tasks: what the list calls Earlier, and what may be deleted. */
export const FINISHED: ReadonlySet<Task['status']> = new Set(['completed', 'failed', 'cancelled', 'unknown'])

const byRecent = (a: Task, b: Task) => b.updated_at - a.updated_at || b.id - a.id

function upsert<T extends { id: number }>(items: T[], item: T, first = false): T[] {
  const index = items.findIndex((existing) => existing.id === item.id)
  if (index < 0) return first ? [item, ...items] : [...items, item]
  const next = items.slice()
  next[index] = item
  return next
}

/** Server state plus live events. Reloads everything on reconnect, so missed events never matter.
 * `updated` turns true when the server comes back with another version than this page loaded with.
 * `reconnects` counts the reloads after the first, for views that hold more than this state. */
export function useLiveState() {
  const [live, setLive] = useState<Live>(empty)
  const [connection, setConnection] = useState<Connection>('connecting')
  const [updated, setUpdated] = useState(false)
  const [reconnects, setReconnects] = useState(0)
  const listeners = useRef(new Set<(event: ServerEvent) => void>())
  const firstVersion = useRef<string | null>(null)

  const reload = useCallback(async () => {
    const state = await api.state()
    firstVersion.current ??= state.version
    if (state.version !== firstVersion.current) setUpdated(true)
    setLive({ ...state })
  }, [])

  useEffect(() => {
    let socket: WebSocket | undefined
    let retry: ReturnType<typeof setTimeout> | undefined
    let attempt = 0
    let stopped = false
    let synced = false

    const tellShowing = () => {
      if (socket?.readyState === WebSocket.OPEN) socket.send(JSON.stringify({ type: 'showing', showing: !document.hidden }))
    }
    const showing = setInterval(() => !document.hidden && tellShowing(), SHOWING_EVERY_MS)

    const connect = () => {
      const protocol = location.protocol === 'https:' ? 'wss:' : 'ws:'
      const opening = socket = new WebSocket(`${protocol}//${location.host}/api/events`)
      // A connection not open by then is dropped, and its onclose tries again.
      const timeout = setTimeout(() => opening.readyState === WebSocket.CONNECTING && opening.close(), CONNECT_TIMEOUT_MS)
      socket.onopen = async () => {
        clearTimeout(timeout)
        attempt = 0
        tellShowing()
        try {
          await reload()
          setConnection('live')
          if (synced) setReconnects((n) => n + 1)
          synced = true
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
        clearTimeout(timeout)
        if (stopped) return
        if (code === UNPAIRED) return markUnpaired()
        setConnection('reconnecting')
        retry = setTimeout(connect, Math.min(10000, 500 * 2 ** attempt++))
      }
    }
    // Back on screen after a while, start afresh: a phone can leave a backgrounded page's socket
    // looking open with nothing getting through, and the reload brings whatever was missed.
    const fresh = () => {
      if (stopped) return
      clearTimeout(retry)
      attempt = 0
      if (socket) {
        socket.onopen = socket.onmessage = socket.onclose = null
        socket.close()
      }
      setConnection('reconnecting')
      connect()
    }
    let hiddenAt = Date.now()
    const onVisibility = () => {
      tellShowing()
      if (document.hidden) hiddenAt = Date.now()
      else if (socket?.readyState !== WebSocket.OPEN || Date.now() - hiddenAt > FRESH_AFTER_HIDDEN_MS) fresh()
    }
    const onOnline = () => socket?.readyState !== WebSocket.OPEN && fresh()
    document.addEventListener('visibilitychange', onVisibility)
    window.addEventListener('online', onOnline)
    connect()
    return () => {
      stopped = true
      clearTimeout(retry)
      clearInterval(showing)
      document.removeEventListener('visibilitychange', onVisibility)
      window.removeEventListener('online', onOnline)
      socket?.close()
    }
  }, [reload])

  const subscribe = useCallback((listener: (event: ServerEvent) => void) => {
    listeners.current.add(listener)
    return () => void listeners.current.delete(listener)
  }, [])

  /** Tasks from elsewhere (an older page, a search result), added to the list. */
  const addTasks = useCallback((tasks: Task[], more?: boolean) => {
    setLive((state) => ({
      ...state,
      tasks: tasks.reduce((all, task) => all.some((t) => t.id === task.id) ? all : [...all, task], state.tasks).sort(byRecent),
      more_tasks: more ?? state.more_tasks,
    }))
  }, [])

  /** The next page of finished tasks, older than the oldest here. */
  const showMore = useCallback(async () => {
    const oldest = live.tasks.filter((t) => FINISHED.has(t.status)).sort(byRecent).at(-1)
    if (!oldest) return
    const page = await api.earlierTasks(oldest)
    addTasks(page.tasks, page.more)
  }, [live.tasks, addTasks])

  return { ...live, connection, updated, reconnects, reload, subscribe, addTasks, showMore }
}

function apply(state: Live, event: ServerEvent): Live {
  switch (event.type) {
    case 'task':
      return { ...state, tasks: upsert(state.tasks, event.task, true).sort(byRecent) }
    case 'task_removed':
      return { ...state, tasks: state.tasks.filter((t) => t.id !== event.task_id) }
    case 'approval': {
      const approvals = event.approval.status === 'pending'
        ? upsert(state.approvals, event.approval)
        : state.approvals.filter((a) => a.id !== event.approval.id)
      return { ...state, approvals }
    }
    case 'project':
      return { ...state, projects: upsert(state.projects, event.project).sort((a, b) => a.name.localeCompare(b.name)) }
    case 'project_removed': // its tasks go with it
      return { ...state, projects: state.projects.filter((p) => p.id !== event.project_id),
               tasks: state.tasks.filter((t) => t.project_id !== event.project_id) }
    case 'rules':
      return { ...state, rules: event.rules }
    case 'limits': {
      const { [event.agent]: _, ...others } = state.limits
      return { ...state, limits: event.limits ? { ...others, [event.agent]: event.limits } : others }
    }
    default:
      return state
  }
}

/** Full detail for one task, kept current from live events, including streamed text.
 * Fetched again after a reconnect (`reconnects` changes), since events sent while offline are lost. */
/** A task's live event applied to its detail. An event `held` while the snapshot loaded may be
 *  older than the snapshot, so it only adds what the snapshot lacks and never rolls it back. */
export function applyEvent(detail: TaskDetail, event: ServerEvent, held = false): TaskDetail {
  switch (event.type) {
    case 'task':
      return held && event.task.updated_at < detail.updated_at ? detail : { ...detail, ...event.task }
    case 'message':
      return held && detail.messages.some((m) => m.id === event.message.id) ? detail
        : { ...detail, messages: upsert(detail.messages, event.message) }
    case 'event':
      return detail.events.some((e) => e.id === event.event.id) ? detail : { ...detail, events: [...detail.events, event.event] }
    default:
      return detail
  }
}

/** Text streaming into a task's timeline, outside React state: a delta re-renders only what
 *  shows it (`useStreaming`), not the whole page, several times a second while a reply streams. */
export class Streaming {
  private items: Record<string, string> = {}
  private text = ''
  private readonly listeners = new Set<() => void>()

  add(item: string, delta: string) {
    this.items = { ...this.items, [item]: (this.items[item] ?? '') + delta }
    this.text = Object.values(this.items).join('\n\n')
    this.listeners.forEach((listener) => listener())
  }

  clear() {
    if (!this.text) return
    this.items = {}
    this.text = ''
    this.listeners.forEach((listener) => listener())
  }

  readonly get = () => this.text

  readonly subscribe = (listener: () => void) => {
    this.listeners.add(listener)
    return () => void this.listeners.delete(listener)
  }
}

export function useStreaming(streaming: Streaming): string {
  return useSyncExternalStore(streaming.subscribe, streaming.get)
}

export function useTaskDetail(taskId: number | null, subscribe: ReturnType<typeof useLiveState>['subscribe'],
                              reconnects: number) {
  const [detail, setDetail] = useState<TaskDetail | null>(null)
  const [streaming] = useState(() => new Streaming())

  useEffect(() => {
    setDetail(null)
    streaming.clear()
    if (taskId === null) return
    let current = true
    // Events that arrive while the snapshot loads are held, then applied to it: a fast reply can
    // be stored after the snapshot was read and still reach this page before the snapshot does.
    let held: ServerEvent[] | null = []
    api.task(taskId).then((task) => {
      if (!current) return
      const early = held ?? []
      held = null
      setDetail(early.reduce((detail, event) => applyEvent(detail, event, true), task))
    }).catch(() => current && setDetail(null))
    const unsubscribe = subscribe((event) => {
      if ('task_id' in event && event.task_id !== taskId) return
      if (event.type === 'task' && event.task.id !== taskId) return
      if (held) held.push(event)
      else setDetail((detail) => (detail ? applyEvent(detail, event) : detail))
      if (event.type === 'delta') streaming.add(event.item_id, event.delta)
      else if (event.type === 'message' && event.message.role === 'agent') streaming.clear()
    })
    return () => {
      current = false
      unsubscribe()
    }
  }, [taskId, subscribe, streaming])

  const seen = useRef(reconnects)
  useEffect(() => {
    if (seen.current === reconnects) return
    seen.current = reconnects
    if (taskId === null) return
    let current = true
    // Streamed text from before the drop has a gap in it; the stored message replaces it once it is done.
    api.task(taskId).then((task) => {
      if (!current) return
      setDetail(task)
      streaming.clear()
    }).catch(() => {})
    return () => void (current = false)
  }, [reconnects, taskId, streaming])

  return { detail, streaming }
}
