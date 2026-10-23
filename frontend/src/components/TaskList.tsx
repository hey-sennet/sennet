import type { Approval, Project, Task } from '@/lib/api'
import { STATUS_LABELS } from '@/lib/status'
import { cn } from '@/lib/utils'
import { StatusDot } from './StatusBadge'

export function relativeTime(seconds: number): string {
  const delta = Date.now() / 1000 - seconds
  if (delta < 60) return 'just now'
  if (delta < 3600) return `${Math.floor(delta / 60)}m ago`
  if (delta < 86400) return `${Math.floor(delta / 3600)}h ago`
  return new Date(seconds * 1000).toLocaleDateString()
}

export function TaskList({ tasks, projects, approvals, selected, onSelect }: {
  tasks: Task[]
  projects: Project[]
  approvals: Approval[]
  selected: number | null
  onSelect: (id: number) => void
}) {
  const names = new Map(projects.map((p) => [p.id, p.name]))
  const waiting = new Set(approvals.map((a) => a.task_id))
  if (!tasks.length) {
    return <p className="px-3 py-6 text-sm text-muted-foreground">No tasks yet. Start one and it will appear here.</p>
  }
  const row = (task: Task) => {
    const status = waiting.has(task.id) ? 'awaiting_approval' : task.status
    return (
      <li key={task.id}>
        <button
          onClick={() => onSelect(task.id)}
          className={cn(
            'flex w-full gap-3 rounded-2xl px-3 py-2.5 text-left transition-colors hover:bg-muted/60',
            selected === task.id && 'bg-muted',
          )}
        >
          <StatusDot status={status} className="mt-[7px] size-[7px]" />
          <div className="min-w-0 flex-1">
            <p className="truncate text-sm leading-snug">{task.title}</p>
            <p className="mt-0.5 truncate text-xs text-muted-foreground">
              {names.get(task.project_id) ?? 'Removed project'} · {STATUS_LABELS[status].toLowerCase()} · {relativeTime(task.updated_at)}
            </p>
          </div>
        </button>
      </li>
    )
  }
  const groups = groupTasks(tasks, waiting).filter((g) => g.tasks.length)
  return (
    <div className="flex flex-col gap-4">
      {groups.map((group) => (
        <section key={group.title} aria-label={group.title}>
          {/* A heading only once there's more than one kind to tell apart. */}
          {groups.length > 1 && (
            <h4 className={cn('eyebrow px-3 pb-1', group.title === 'Needs you' ? 'text-warn' : 'text-muted-foreground')}>{group.title}</h4>
          )}
          <ul className="flex flex-col gap-0.5">{group.tasks.map(row)}</ul>
        </section>
      ))}
    </div>
  )
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
