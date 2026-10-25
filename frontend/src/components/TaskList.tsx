import { useEffect, useRef, useState, type PointerEvent } from 'react'
import { Search, Trash2, X } from 'lucide-react'
import { toast } from 'sonner'
import { api, type AgentStatus, type Approval, type Project, type SearchHit, type Task } from '@/lib/api'
import { STATUS_LABELS } from '@/lib/status'
import { FINISHED } from '@/lib/store'
import { cn } from '@/lib/utils'
import { ConfirmDialog, useConfirm } from './ConfirmDialog'
import { StatusDot } from './StatusBadge'

const SWIPE_OPEN = 88 // px a row slides left to show its delete button
const SEARCH_DELAY_MS = 200
const LEAVE_MS = 300 // a deleted row folds away before it goes, so the rows below slide up

export function relativeTime(seconds: number): string {
  const delta = Date.now() / 1000 - seconds
  if (delta < 60) return 'just now'
  if (delta < 3600) return `${Math.floor(delta / 60)}m ago`
  if (delta < 86400) return `${Math.floor(delta / 3600)}h ago`
  return new Date(seconds * 1000).toLocaleDateString()
}

/**
 * Every task: those waiting on you, then those at work, then the latest finished ones, with
 * "Show more" for older ones. A search box above finds any task by what was said in it. A
 * finished task is deleted with the trash button its row shows on hover, or on a touch screen
 * by swiping its row left.
 */
export function TaskList({ tasks, projects, approvals, agents, selected, more, unheard, onSelect, onShowMore, onFound }: {
  tasks: Task[]
  projects: Project[]
  approvals: Approval[]
  agents: Record<string, AgentStatus> | null
  selected: number | null
  more: boolean // older finished tasks to page through
  unheard?: ReadonlySet<number> // tasks with a reply that wasn't spoken, being off screen
  onSelect: (id: number) => void
  onShowMore: () => Promise<void>
  onFound: (tasks: Task[]) => void // a task opened from search, which the list may not have yet
}) {
  const names = new Map(projects.map((p) => [p.id, p.name]))
  const waiting = new Set(approvals.map((a) => a.task_id))
  const [query, setQuery] = useState('')
  // The results, with the query they answer: a newer query shows "Searching…" until its own arrive.
  const [hits, setHits] = useState<(SearchHit[] & { query?: string }) | null>(null)
  const [swiped, setSwiped] = useState<number | null>(null)
  const [loading, setLoading] = useState(false)
  const deleting = useConfirm<Task>()
  const [leaving, setLeaving] = useState<number | null>(null)
  const searching = query.trim().length >= 3

  useEffect(() => {
    if (!searching) return
    let current = true
    const timer = setTimeout(() => {
      const asked = query.trim()
      api.search(asked).then((found) => current && setHits(Object.assign(found, { query: asked })),
        () => current && setHits(Object.assign([], { query: asked })))
    }, SEARCH_DELAY_MS)
    return () => {
      current = false
      clearTimeout(timer)
    }
  }, [query, searching])

  const remove = async (task: Task) => {
    deleting.close()
    setLeaving(task.id)
    await new Promise((resolve) => setTimeout(resolve, LEAVE_MS))
    try {
      await api.deleteTask(task.id)
      setHits((found) => found && Object.assign(found.filter((hit) => hit.task.id !== task.id), { query: found.query }))
    } catch (error) {
      setLeaving(null)
      toast.error(error instanceof Error ? error.message : 'Could not delete the task')
    }
  }

  const row = (task: Task, snippet?: SearchHit) => {
    const status = waiting.has(task.id) ? 'awaiting_approval' : task.status
    return (
      <TaskRow key={task.id} task={task} selected={selected === task.id} swiped={swiped === task.id}
        leaving={leaving === task.id}
        onSwipe={(open) => setSwiped(open ? task.id : null)} onDelete={() => {
          setSwiped(null)
          deleting.ask(task)
        }}
        onSelect={() => {
          if (snippet) onFound([task])
          onSelect(task.id)
        }}>
        <StatusDot status={status} className="mt-[7px] size-[7px]" />
        <div className="min-w-0 flex-1">
          <p className="flex items-center gap-2 text-sm leading-snug">
            <span className="truncate">{task.title}</span>
            {unheard?.has(task.id) && <span role="img" aria-label="New reply" className="size-1.5 shrink-0 rounded-full bg-agent" />}
          </p>
          <p className="mt-0.5 truncate text-xs text-muted-foreground">
            {names.get(task.project_id) ?? 'Removed project'} · {STATUS_LABELS[status].toLowerCase()} · {relativeTime(task.updated_at)}
          </p>
          {snippet && <Highlighted hit={snippet} />}
        </div>
      </TaskRow>
    )
  }

  const searchBox = (
    <label className="mx-1 mb-3 flex h-9 items-center gap-2 rounded-full border bg-background/60 px-3 text-sm text-muted-foreground focus-within:border-foreground/30">
      <Search className="size-3.5 shrink-0" />
      <input type="search" value={query} onChange={(event) => setQuery(event.target.value)}
        onKeyDown={(event) => event.key === 'Escape' && query && (event.stopPropagation(), setQuery(''))}
        placeholder="Search tasks" aria-label="Search tasks"
        className="min-w-0 flex-1 bg-transparent text-foreground outline-none placeholder:text-muted-foreground [&::-webkit-search-cancel-button]:hidden" />
      {query && (
        <button type="button" aria-label="Clear search" className="hover:text-foreground" onClick={() => setQuery('')}>
          <X className="size-3.5" />
        </button>
      )}
    </label>
  )

  const task = deleting.subject
  const confirmDelete = (
    <ConfirmDialog open={deleting.open} title={`Delete “${task?.title}”?`} action="Delete"
      onConfirm={() => task && remove(task)} onCancel={deleting.close}>
      This removes it from Sennet. {task && (agents?.[task.agent]?.title ?? task.agent)} keeps its own copy of the conversation.
    </ConfirmDialog>
  )

  if (searching) {
    return (
      <div>
        {searchBox}
        {confirmDelete}
        {hits === null || hits.query !== query.trim() ? <p className="px-3 py-2 text-sm text-muted-foreground">Searching…</p>
          : !hits.length ? <p className="px-3 py-2 text-sm text-muted-foreground">No task mentions that.</p>
          : <ul aria-label="Search results" className="flex flex-col gap-0.5">{hits.map((hit) => row(hit.task, hit))}</ul>}
      </div>
    )
  }
  if (!tasks.length) {
    return (
      <div>
        {searchBox}
        {confirmDelete}
        <p className="px-3 py-6 text-sm text-muted-foreground">No tasks yet. Start one and it will appear here.</p>
      </div>
    )
  }
  const groups = groupTasks(tasks, waiting).filter((g) => g.tasks.length || (g.title === 'Earlier' && more))
  return (
    <div>
      {searchBox}
      {confirmDelete}
      <div className="flex flex-col gap-4">
        {groups.map((group) => (
          <section key={group.title} aria-label={group.title}>
            {/* A heading only once there's more than one kind to tell apart. */}
            {groups.length > 1 && (
              <h4 className={cn('eyebrow px-3 pb-1', group.title === 'Needs you' ? 'text-warn' : 'text-muted-foreground')}>{group.title}</h4>
            )}
            <ul className="flex flex-col gap-0.5">{group.tasks.map((task) => row(task))}</ul>
            {group.title === 'Earlier' && more && (
              <button type="button" disabled={loading} className="mt-1 px-3 py-1.5 text-xs text-muted-foreground hover:text-foreground disabled:opacity-60"
                onClick={async () => {
                  setLoading(true)
                  try {
                    await onShowMore()
                  } catch {
                    toast.error('Could not load older tasks')
                  } finally {
                    setLoading(false)
                  }
                }}>
                {loading ? 'Loading…' : 'Show more'}
              </button>
            )}
          </section>
        ))}
      </div>
    </div>
  )
}

/** A row that opens its task. A finished one can be deleted: a trash button on hover where there
 * is a pointer to hover with, or a swipe left on a touch screen. */
function TaskRow({ task, selected, swiped, leaving, onSwipe, onSelect, onDelete, children }: {
  task: Task
  selected: boolean
  swiped: boolean
  leaving: boolean // deleted, and folding away
  onSwipe: (open: boolean) => void
  onSelect: () => void
  onDelete: () => void
  children: React.ReactNode
}) {
  const finished = FINISHED.has(task.status)
  const [drag, setDrag] = useState<number | null>(null)
  const start = useRef<{ x: number; y: number; moved: boolean } | null>(null)
  const offset = drag ?? (swiped ? -SWIPE_OPEN : 0)

  const down = (event: PointerEvent) => {
    if (event.pointerType !== 'touch') return
    start.current = { x: event.clientX, y: event.clientY, moved: false }
  }
  const move = (event: PointerEvent) => {
    const from = start.current
    if (!from) return
    const dx = event.clientX - from.x + (swiped ? -SWIPE_OPEN : 0)
    if (!from.moved && Math.abs(event.clientX - from.x) < 8) return
    if (!from.moved && Math.abs(event.clientY - from.y) > Math.abs(event.clientX - from.x)) {
      start.current = null // scrolling the list, not swiping
      return
    }
    from.moved = true
    setDrag(Math.max(-SWIPE_OPEN - 24, Math.min(0, dx)))
  }
  const up = () => {
    if (start.current?.moved && drag !== null) onSwipe(drag < -SWIPE_OPEN / 2)
    setDrag(null)
  }

  return (
    <li className={cn('group grid overflow-hidden rounded-2xl transition-[grid-template-rows,opacity] ease-strata',
      leaving ? 'grid-rows-[0fr] opacity-0' : 'grid-rows-[1fr]')} style={{ transitionDuration: `${LEAVE_MS}ms` }} inert={leaving}>
      <div className="relative min-h-0">
        {offset < 0 && (
          <button type="button" disabled={!finished} onClick={onDelete} style={{ width: SWIPE_OPEN }}
            className="absolute inset-y-0 right-0 rounded-2xl bg-destructive/90 text-xs font-medium text-white disabled:bg-muted disabled:text-muted-foreground">
            {finished ? 'Delete' : 'Stop it first'}
          </button>
        )}
        <div style={{ transform: offset ? `translateX(${offset}px)` : undefined }}
          className={cn('relative flex touch-pan-y items-start rounded-2xl', drag === null && 'transition-transform duration-300 ease-strata',
            offset < 0 && 'bg-card')}
          onPointerDown={down} onPointerMove={move} onPointerUp={up} onPointerCancel={up}>
          <button
            onClick={(event) => {
              if (start.current?.moved) {
                event.preventDefault()
                start.current = null
                return
              }
              if (swiped) return onSwipe(false)
              onSelect()
            }}
            className={cn('flex min-w-0 flex-1 gap-3 rounded-2xl px-3 py-2.5 text-left transition-colors hover:bg-muted/60',
              selected && 'bg-muted')}
          >
            {children}
          </button>
          {/* Only where something can hover; touch screens swipe instead. */}
          <button type="button" disabled={!finished} onClick={onDelete}
            aria-label={finished ? `Delete ${task.title}` : `${task.title} is still working; stop it before deleting`}
            title={finished ? 'Delete task' : 'Stop it first'}
            className="absolute top-2 right-2 hidden size-7 place-items-center rounded-full text-muted-foreground opacity-0 transition-opacity
              hover:bg-muted hover:text-foreground focus-visible:opacity-100 disabled:hover:bg-transparent disabled:hover:text-muted-foreground
              [@media(hover:hover)]:grid [@media(hover:hover)]:group-hover:opacity-100 disabled:[@media(hover:hover)]:group-hover:opacity-40">
            <Trash2 className="size-3.5" />
          </button>
        </div>
      </div>
    </li>
  )
}

/** Where a search matched: the snippet, with the matching words marked. */
function Highlighted({ hit }: { hit: SearchHit }) {
  const parts: React.ReactNode[] = []
  let at = 0
  for (const [start, end] of hit.highlights) {
    if (start < at) continue // overlaps the one before
    parts.push(hit.snippet.slice(at, start), <mark key={start} className="rounded-sm bg-agent/25 text-foreground">{hit.snippet.slice(start, end)}</mark>)
    at = end
  }
  parts.push(hit.snippet.slice(at))
  return <p className="mt-1 line-clamp-2 text-xs text-muted-foreground">{parts}</p>
}

/** Tasks waiting on you first, then those at work, then the rest; newest first within each. */
export function groupTasks(tasks: Task[], waiting: ReadonlySet<number>) {
  const needsYou = (t: Task) => waiting.has(t.id) || t.status === 'awaiting_approval'
  const working = (t: Task) => ['running', 'queued', 'background'].includes(t.status)
  return [
    { title: 'Needs you', tasks: tasks.filter(needsYou) },
    { title: 'Working', tasks: tasks.filter((t) => !needsYou(t) && working(t)) },
    { title: 'Earlier', tasks: tasks.filter((t) => !needsYou(t) && !working(t)) },
  ]
}
