import { useEffect, useMemo, useRef } from 'react'
import { ArrowLeft, Check, FileText, Square, Terminal, X } from 'lucide-react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { api, type Approval, type Message, type Project, type StepStatus, type TaskDetail, type TaskEvent, type TurnStatus } from '@/lib/api'
import { cn } from '@/lib/utils'
import { ApprovalCard } from './ApprovalCard'
import { Composer } from './Composer'
import { StatusBadge } from './StatusBadge'

type Entry = { at: number; message: Message } | { at: number; event: TaskEvent }

const VISIBLE_EVENTS = new Set(['command_finished', 'file_change', 'plan', 'error', 'turn_completed'])

export function TaskView({ detail, streaming, project, approvals, onBack }: {
  detail: TaskDetail
  streaming: string
  project?: Project
  approvals: Approval[]
  onBack: () => void
}) {
  const bottom = useRef<HTMLDivElement>(null)
  const active = detail.status === 'running' || detail.status === 'awaiting_approval'

  const entries = useMemo<Entry[]>(() => {
    const messages: Entry[] = detail.messages.map((message) => ({ at: message.created_at, message }))
    // Only the latest plan matters; earlier ones are superseded.
    const lastPlan = detail.events.findLast((e) => e.kind === 'plan')
    const events: Entry[] = detail.events
      .filter((e) => VISIBLE_EVENTS.has(e.kind) && (e.kind !== 'plan' || e === lastPlan))
      .filter((e) => e.kind !== 'turn_completed' || e.data.status !== 'completed')
      .map((event) => ({ at: event.created_at, event }))
    return [...messages, ...events].sort((a, b) => a.at - b.at)
  }, [detail.messages, detail.events])

  useEffect(() => {
    bottom.current?.scrollIntoView({ block: 'end' })
  }, [entries.length, streaming, approvals.length])

  const cancel = async () => {
    try {
      await api.cancel(detail.id)
    } catch (error) {
      toast.error(error instanceof Error ? error.message : 'Could not cancel')
    }
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      <header className="flex items-start gap-3 border-b px-4 py-3 md:px-8">
        <Button variant="ghost" size="icon" className="-ml-2 md:hidden" aria-label="Back to tasks" onClick={onBack}>
          <ArrowLeft />
        </Button>
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 text-xs text-muted-foreground">
            <span>{project?.name ?? 'Removed project'}</span>
            <span>·</span>
            <span>{detail.agent}</span>
          </div>
          <h1 className="mt-0.5 truncate text-base font-medium">{detail.title}</h1>
        </div>
        <div className="flex items-center gap-3 pt-1">
          <StatusBadge status={detail.status} />
          {(active || detail.status === 'queued') && (
            <Button variant="outline" size="sm" onClick={cancel}>
              <Square className="fill-current" /> Stop
            </Button>
          )}
        </div>
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto px-4 py-6 md:px-8">
        <div className="mx-auto flex max-w-3xl flex-col gap-4">
          {entries.map((entry) =>
            'message' in entry ? <MessageView key={`m${entry.message.id}`} message={entry.message} /> : <EventView key={`e${entry.event.id}`} event={entry.event} />,
          )}
          {streaming && <div className="whitespace-pre-wrap text-[15px] leading-relaxed text-foreground/80">{streaming}</div>}
          {active && !streaming && !approvals.length && (
            <p className="text-sm text-muted-foreground"><span className="animate-pulse">Working…</span></p>
          )}
          {approvals.map((approval) => <ApprovalCard key={approval.id} approval={approval} project={project} />)}
          {detail.error && <p className="rounded-lg border border-destructive/30 bg-destructive/5 px-3 py-2 text-sm text-destructive">{detail.error}</p>}
          <div ref={bottom} />
        </div>
      </div>

      <div className="border-t px-4 py-3 md:px-8">
        <div className="mx-auto max-w-3xl">
          <Composer
            placeholder={active ? 'Add a follow-up; it runs when this turn finishes' : 'Follow up on this task'}
            onSend={(text) => api.followUp(detail.id, text)}
          />
        </div>
      </div>
    </div>
  )
}

function MessageView({ message }: { message: Message }) {
  if (message.role === 'user') {
    return (
      <div className={cn('ml-auto max-w-[85%] rounded-2xl rounded-br-md bg-muted px-4 py-2.5 text-[15px] whitespace-pre-wrap',
        message.state === 'queued' && 'opacity-60', message.state === 'cancelled' && 'line-through opacity-40')}>
        {message.text}
        {message.state === 'queued' && <span className="mt-1 block text-xs text-muted-foreground">Queued</span>}
      </div>
    )
  }
  return <div className="text-[15px] leading-relaxed whitespace-pre-wrap">{message.text}</div>
}

function EventView({ event }: { event: TaskEvent }) {
  const { kind, data } = event
  if (kind === 'command_finished') {
    const failed = data.status !== 'completed' || (data.exit_code ?? 0) !== 0
    return (
      <details className="group text-sm">
        <summary className="flex cursor-pointer list-none items-center gap-2 text-muted-foreground hover:text-foreground">
          <Terminal className="size-3.5 shrink-0" />
          <code className="truncate font-mono text-xs">{data.command}</code>
          {data.status === 'declined' ? <X className="size-3.5 text-muted-foreground" />
            : failed ? <span className="text-xs text-destructive">exit {data.exit_code}</span>
            : <Check className="size-3.5 text-muted-foreground" />}
        </summary>
        {data.output_tail && (
          <pre className="mt-2 max-h-64 overflow-auto rounded-lg bg-muted/60 px-3 py-2 font-mono text-xs leading-relaxed">{data.output_tail}</pre>
        )}
      </details>
    )
  }
  if (kind === 'file_change') {
    return (
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-sm text-muted-foreground">
        <FileText className="size-3.5" />
        {data.status === 'completed' ? 'Changed' : data.status === 'declined' ? 'Declined changes to'
          : data.status === 'failed' ? 'Failed to change' : 'Changing'}
        {data.changes.map((c: { path: string }) => <code key={c.path} className="font-mono text-xs">{c.path.split('/').pop()}</code>)}
      </div>
    )
  }
  if (kind === 'plan') {
    return (
      <ol className="rounded-xl border px-4 py-3 text-sm">
        {data.steps.map((step: { step: string; status: StepStatus }, i: number) => (
          <li key={i} className={cn('flex gap-2 py-0.5', step.status === 'done' && 'text-muted-foreground line-through')}>
            <span className="text-muted-foreground">{step.status === 'active' ? '→' : step.status === 'done' ? '✓' : '·'}</span>
            {step.step}
          </li>
        ))}
      </ol>
    )
  }
  if (kind === 'error') return <p className="text-sm text-destructive">{data.message}{data.will_retry ? ' (retrying)' : ''}</p>
  if (kind === 'turn_completed') {
    const status = data.status as TurnStatus
    return <p className="text-xs text-muted-foreground">{status === 'cancelled' ? 'Stopped' : `Turn ${status}`}{data.error ? `: ${data.error}` : ''}</p>
  }
  return null
}
