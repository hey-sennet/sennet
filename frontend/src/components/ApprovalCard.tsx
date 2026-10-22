import { useState } from 'react'
import { ChevronDown } from 'lucide-react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { api, type Approval, type Decision, type Project, type Task } from '@/lib/api'
import { cn } from '@/lib/utils'

// Amber is Strata's colour for anything waiting on the user; destructive requests go red.
const RISK: Record<Approval['risk'], { label: string; tone: string }> = {
  read: { label: 'Read', tone: 'text-muted-foreground' },
  edit: { label: 'Edit files', tone: 'text-warn' },
  execute: { label: 'Run', tone: 'text-warn' },
  network: { label: 'Network', tone: 'text-warn' },
  destructive: { label: 'Destructive', tone: 'text-destructive' },
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
  const destructive = approval.risk === 'destructive'
  const changes = approval.detail.changes ?? []
  const shown = approval.kind === 'command' ? approval.detail.command
    : approval.kind === 'tool' ? [approval.detail.tool, approval.detail.tool_input].filter(Boolean).join('\n') : null

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
    <article className={cn('rounded-3xl border bg-card p-5', destructive ? 'border-destructive/40' : 'border-warn/25')}>
      <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1 text-xs text-muted-foreground">
        <span className={cn('eyebrow', risk.tone)}>{risk.label}</span>
        {project && <span>{project.name}</span>}
        {task && (
          <button className="truncate hover:text-foreground" onClick={onOpenTask}>
            · {task.title}
          </button>
        )}
      </div>
      <p className="mt-2 font-serif text-[26px] leading-[1.1] tracking-[-0.01em] text-balance">
        Wants to <span>{approval.summary}</span>
      </p>
      {approval.detail.title && <p className="mt-2 text-sm">{approval.detail.title}</p>}
      {approval.detail.reason && <p className="mt-2 text-sm text-muted-foreground">{approval.detail.reason}</p>}
      {shown && (
        <pre className={cn('mt-3 overflow-x-auto rounded-lg px-3 py-2 font-mono text-[13px] leading-relaxed whitespace-pre-wrap break-all',
          destructive ? 'bg-destructive/14 text-destructive' : 'bg-warn/14 text-warn')}>{shown}</pre>
      )}
      {changes.length > 0 && (
        <div className="mt-3">
          <button className="flex items-center gap-1 text-xs text-muted-foreground hover:text-foreground" onClick={() => setOpen(!open)}>
            <ChevronDown className={cn('size-3.5 transition-transform', open && 'rotate-180')} />
            {open ? 'Hide' : 'Show'} changes ({changes.length} {changes.length === 1 ? 'file' : 'files'})
          </button>
          {open && changes.map((change) => (
            <div key={change.path} className="mt-2">
              <p className="font-mono text-xs text-muted-foreground">
                {change.path}{change.kind !== 'update' && <span> · {change.kind === 'add' ? 'new file' : change.kind === 'delete' ? 'deleted' : 'moved'}</span>}
              </p>
              {change.diff && (
                <pre className="mt-1 max-h-72 overflow-auto rounded-lg bg-muted/60 px-3 py-2 font-mono text-xs leading-relaxed">
                  {change.diff.split('\n').map((line, i) => (
                    <span key={i} className={cn('block', line.startsWith('+') && 'text-emerald-400', line.startsWith('-') && 'text-red-400')}>{line || ' '}</span>
                  ))}
                </pre>
              )}
            </div>
          ))}
        </div>
      )}
      <div className="mt-5 flex flex-wrap gap-2 [&>*]:h-10 [&>*]:rounded-full [&>*]:px-5">
        <Button variant="warn" disabled={busy} onClick={() => decide('approve')}>Approve</Button>
        <Button variant="outline" disabled={busy} onClick={() => decide('decline')}>Decline</Button>
        {approval.can_always && (
          <Button variant="ghost" className="text-muted-foreground" disabled={busy} onClick={() => decide('approve_always')}>
            Always in {project?.name ?? 'this project'}
          </Button>
        )}
      </div>
    </article>
  )
}
