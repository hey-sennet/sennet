import type { Approval, Project, Task } from '@/lib/api'
import { cn } from '@/lib/utils'
import { StatusBadge } from './StatusBadge'

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
  return (
    <ul className="flex flex-col gap-0.5">
      {tasks.map((task) => (
        <li key={task.id}>
          <button
            onClick={() => onSelect(task.id)}
            className={cn(
              'w-full rounded-lg px-3 py-2.5 text-left transition-colors hover:bg-muted/60',
              selected === task.id && 'bg-muted',
            )}
          >
            <div className="flex items-center justify-between gap-2 text-xs text-muted-foreground">
              <span className="truncate">{names.get(task.project_id) ?? 'Removed project'}</span>
              <StatusBadge status={waiting.has(task.id) ? 'awaiting_approval' : task.status} />
            </div>
            <p className="mt-0.5 truncate text-sm">{task.title}</p>
            <p className="mt-0.5 text-xs text-muted-foreground/70">{relativeTime(task.updated_at)}</p>
          </button>
        </li>
      ))}
    </ul>
  )
}
