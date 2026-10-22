import type { ReactNode } from 'react'
import { X } from 'lucide-react'
import { Button } from '@/components/ui/button'
import type { Approval, Project, Task, TaskDetail } from '@/lib/api'
import { cn } from '@/lib/utils'
import { Strata } from '@/strata/Strata'
import { strataPreview } from '@/strata/preview'
import { strataState } from '@/voice/strata'
import { usePushToTalk } from '@/voice/usePushToTalk'
import { voice, type Target } from '@/voice/voice'
import { useDecide } from './ApprovalCard'
import { Caption } from './Caption'

const voiceSources = { user: voice().source, agent: voice().playback.source }

/**
 * The disc and the words beneath it, on every view. The disc is the talk control: what you say
 * goes to the selected task, or starts a new one in `project`. Beneath it, in order: what is
 * being said, an approval waiting on you, then the selected task's progress or last reply.
 */
export function Stage({ target, project, task, detail, approvals, tasks, projects, compact, onClearTask, onAddProject, className }: {
  target: Target
  project?: Project
  task?: Task
  detail: TaskDetail | null
  approvals: Approval[]
  tasks: Task[]
  projects: Project[]
  compact: boolean // typing: the disc steps back
  onClearTask: () => void
  onAddProject: () => void
  className?: string
}) {
  const talk = usePushToTalk(target)
  const snapshot = talk.state
  const running = task?.status === 'running' || task?.status === 'queued'
  const state = strataPreview?.state ?? strataState(snapshot, { taskRunning: running, approvalPending: approvals.length > 0 })
  const words = snapshot.words && (snapshot.words.committed || snapshot.words.provisional) ? snapshot.words : null
  const approval = approvals[0]
  const projectOf = (taskId: number) => projects.find((p) => p.id === tasks.find((t) => t.id === taskId)?.project_id)

  const label = state === 'listening' ? 'Listening'
    : state === 'speaking' ? 'Speaking'
    : snapshot.state === 'transcribing' ? 'Hearing you'
    : state === 'approval' && !words ? (approvals.length > 1 ? `Needs you · 1 of ${approvals.length}` : 'Needs you')
    : state === 'thinking' ? `Working in ${project?.name ?? 'the project'}`
    : !talk.ready ? (snapshot.connected ? 'Voice isn’t set up · type instead' : 'Connecting')
    : task ? 'Following up' : project ? `New task · ${project.name}` : 'Add a project'

  let body: ReactNode
  if (words) {
    body = <Caption committed={words.committed} provisional={words.provisional} speaker={words.speaker} />
  } else if (approval) {
    body = <StageApproval key={approval.id} approval={approval} project={projectOf(approval.task_id)} />
  } else if (!projects.length) {
    body = (
      <div>
        <Lead quiet>Add a project: a directory your agent should work in.</Lead>
        <Button size="sm" className="mt-4" onClick={onAddProject}>Add a project</Button>
      </div>
    )
  } else if (task && running) {
    body = (
      <div>
        <Lead>{task.title}<span className="animate-pulse">…</span></Lead>
        {current(detail) && <p className="mt-3 font-mono text-[12.5px] text-muted-foreground">› {current(detail)}</p>}
      </div>
    )
  } else if (task && lastReply(detail)) {
    body = <Lead agent className="line-clamp-5">{lastReply(detail)}</Lead>
  } else {
    body = <Lead quiet>{task ? task.title : 'What should we work on?'}</Lead>
  }

  return (
    <main className={cn('flex min-h-0 flex-col items-center justify-center gap-[clamp(16px,4vh,40px)] overflow-hidden px-6', className)}>
      <button
        {...talk.props}
        className={cn('aspect-square shrink-0 touch-none rounded-full outline-none select-none focus-visible:ring-1 focus-visible:ring-ring',
          'transition-[width] duration-700 ease-strata enabled:cursor-pointer',
          compact ? 'w-[calc(var(--disc)*0.62)]' : 'w-(--disc)',
          '[--disc:min(64vw,34vh,460px)] lg:[--disc:min(30vw,40vh,440px)]')}
      >
        {strataPreview
          ? <Strata state={strataPreview.state} sources={strataPreview.sources} frozenAt={strataPreview.frozenAt} className="size-full" />
          : <Strata state={state} sources={voiceSources} className="size-full" />}
      </button>
      <div className="w-full max-w-[34rem]">
        <p className={cn('eyebrow mb-3 flex min-h-5 items-center gap-1.5 text-muted-foreground transition-colors duration-400',
          state === 'listening' && 'text-foreground', (state === 'speaking' || state === 'thinking') && 'text-agent',
          state === 'approval' && !words && 'text-warn')}>
          <span className="truncate">{label}</span>
          {label === 'Following up' && (
            <button className="-my-1 rounded-full p-1 hover:text-foreground" aria-label="New task instead" title="New task instead" onClick={onClearTask}>
              <X className="size-3" />
            </button>
          )}
        </p>
        <div className="min-h-[4.5em] font-serif text-[clamp(26px,6vw,34px)]">{body}</div>
      </div>
    </main>
  )
}

function Lead({ children, quiet, agent, className }: { children: ReactNode; quiet?: boolean; agent?: boolean; className?: string }) {
  return (
    <p className={cn('animate-word-in font-serif text-[clamp(26px,6vw,34px)] leading-[1.08] tracking-[-0.015em] text-balance',
      quiet && 'text-muted-foreground', agent && 'italic', className)}>
      {children}
    </p>
  )
}

/** The oldest approval waiting on you, answerable here or by voice. */
function StageApproval({ approval, project }: { approval: Approval; project?: Project }) {
  const { busy, decide } = useDecide(approval)
  const shown = approval.kind === 'command' ? approval.detail.command : approval.kind === 'tool' ? approval.detail.tool : null
  const destructive = approval.risk === 'destructive'
  return (
    <div className="animate-word-in">
      <p className="font-serif text-[clamp(26px,6vw,34px)] leading-[1.08] tracking-[-0.015em] text-balance">
        {project?.name ?? 'The agent'} wants to {approval.summary}
      </p>
      {shown && (
        <code className={cn('mt-3 inline-block max-w-full truncate rounded-lg px-2.5 py-1.5 font-mono text-[14px]',
          destructive ? 'bg-destructive/14 text-destructive' : 'bg-warn/14 text-warn')}>{shown}</code>
      )}
      <div className="mt-5 flex flex-wrap gap-2 font-sans [&>*]:h-10 [&>*]:rounded-full [&>*]:px-5">
        <Button variant="warn" disabled={busy} onClick={() => decide('approve')}>Approve</Button>
        <Button variant="outline" disabled={busy} onClick={() => decide('decline')}>Decline</Button>
        {approval.can_always && (
          <Button variant="ghost" className="text-muted-foreground" disabled={busy} onClick={() => decide('approve_always')}>
            Always in {project?.name ?? 'this project'}
          </Button>
        )}
      </div>
    </div>
  )
}

/** The command running now, if the latest command hasn't finished. */
function current(detail: TaskDetail | null): string | null {
  const last = detail?.events.findLast((e) => e.kind === 'command_started' || e.kind === 'command_finished')
  return last?.kind === 'command_started' ? last.data.command ?? null : null
}

function lastReply(detail: TaskDetail | null): string | null {
  return detail?.messages.findLast((m) => m.role === 'agent')?.text ?? null
}
