import { useState } from 'react'
import { ChevronDown } from 'lucide-react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { api, type Approval, type Decision, type Project, type Task } from '@/lib/api'
import { cn } from '@/lib/utils'

const RISK: Record<Approval['risk'], { label: string; tone: string }> = {
  read: { label: 'Read', tone: 'text-muted-foreground border-border' },
  edit: { label: 'Edit files', tone: 'text-sky-300 border-sky-400/30' },
  execute: { label: 'Run command', tone: 'text-amber-300 border-amber-400/30' },
  network: { label: 'Network', tone: 'text-amber-300 border-amber-400/30' },
  destructive: { label: 'Destructive', tone: 'text-red-300 border-red-400/40' },
}

export function ApprovalCard({ approval, task, project, onOpenTask }: {
  approval: Approval
  task?: Task
  project?: Project
  onOpenTask?: () => void
}) {
  const [busy, setBusy] = useState(false)
  const [open, setOpen] = useState(false)
  const risk = RISK[approval.risk]
  const canAlways = approval.risk !== 'destructive' && approval.kind !== 'permissions'
  const changes: { path: string; kind: string; diff: string }[] = approval.detail.changes ?? []

  const decide = async (decision: Decision) => {
    setBusy(true)
    try {
      await api.decide(approval.id, decision)
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Could not send your decision')
      setBusy(false)
    }
  }

  return (
    <article className={cn('rounded-xl border bg-card p-4', approval.risk === 'destructive' ? 'border-red-400/40' : 'border-amber-400/30')}>
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground">
        <span className={cn('rounded-full border px-2 py-0.5 font-medium', risk.tone)}>{risk.label}</span>
        {project && <span>{project.name}</span>}
        {task && (
          <button className="truncate hover:text-foreground" onClick={onOpenTask}>
            · {task.title}
          </button>
        )}
      </div>
      <p className="mt-2 text-[15px] leading-snug">
        Wants to <span className="font-medium">{approval.summary}</span>
      </p>
      {approval.detail.reason && <p className="mt-1 text-sm text-muted-foreground">{approval.detail.reason}</p>}
      {approval.kind === 'command' && approval.detail.command && (
        <pre className="mt-3 overflow-x-auto rounded-lg bg-muted/60 px-3 py-2 font-mono text-xs leading-relaxed">{approval.detail.command}</pre>
      )}
      {changes.length > 0 && (
        <div className="mt-3">
          <button className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground" onClick={() => setOpen(!open)}>
            <ChevronDown className={cn('size-3.5 transition-transform', open && 'rotate-180')} />
            {open ? 'Hide' : 'Show'} changes ({changes.length} {changes.length === 1 ? 'file' : 'files'})
          </button>
          {open && changes.map((change) => (
            <div key={change.path} className="mt-2">
              <p className="font-mono text-xs text-muted-foreground">{change.path}</p>
              <pre className="mt-1 max-h-72 overflow-auto rounded-lg bg-muted/60 px-3 py-2 font-mono text-xs leading-relaxed">
                {change.diff.split('\n').map((line, i) => (
                  <span key={i} className={cn('block', line.startsWith('+') && 'text-emerald-400', line.startsWith('-') && 'text-red-400')}>{line || ' '}</span>
                ))}
              </pre>
            </div>
          ))}
        </div>
      )}
      <div className="mt-4 flex flex-wrap gap-2">
        <Button disabled={busy} onClick={() => decide('approve')}>Approve</Button>
        {canAlways && (
          <Button variant="secondary" disabled={busy} onClick={() => decide('approve_always')}>
            Always in {project?.name ?? 'this project'}
          </Button>
        )}
        <Button variant="ghost" disabled={busy} onClick={() => decide('decline')}>Decline</Button>
      </div>
    </article>
  )
}
