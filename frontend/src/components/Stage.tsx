import { useEffect, type ComponentProps, type ReactNode } from 'react'
import { Square } from 'lucide-react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { api, type Approval, type Project, type Task, type TaskDetail } from '@/lib/api'
import { cn } from '@/lib/utils'
import { Strata } from '@/strata/Strata'
import { strataPreview } from '@/strata/preview'
import chime from '@/voice/approval-chime.mp3'
import { strataState } from '@/voice/strata'
import { usePushToTalk } from '@/voice/usePushToTalk'
import { voice, type Target } from '@/voice/voice'
import { useDecide } from './ApprovalCard'
import { Caption } from './Caption'
import { ModelMenu } from './ModelMenu'
import { plainText, spokenPart } from './Markdown'

const voiceSources = { user: voice().source, agent: voice().playback.source }

/**
 * The disc and the words beneath it, on every view. The disc is the talk control: what you say
 * goes to the selected task, or starts a new one in `project`. Beneath it, in order: what is
 * being said, an approval waiting on you, then the selected task's progress or last reply.
 */
export function Stage({ target, project, task, detail, approvals, elsewhere, tasks, projects, compact, discless, model, limit, onSelectTask, onOpenTask, onAddProject, className }: {
  target: Target
  project?: Project
  task?: Task
  detail: TaskDetail | null
  approvals: Approval[] // the ones this device asks about: its selected task's, or the tasks it started
  elsewhere: Approval[] // waiting on tasks another device may be following
  tasks: Task[]
  projects: Project[]
  compact: boolean // typing: the disc steps back, as it does for an approval
  discless: boolean // typing on a phone, its keyboard up: that needs the room, and the words are what you're answering
  model?: Omit<ComponentProps<typeof ModelMenu>, 'className'> // who your words go to, with where
  limit?: { line: string; reached: boolean } // the agent's usage limit, once close to it or reached
  onSelectTask: (id: number) => void // makes it the one this device asks about
  onOpenTask: (id: number) => void // selects it and shows its timeline
  onAddProject: () => void
  className?: string
}) {
  const talk = usePushToTalk(target)
  const snapshot = talk.state
  const running = task?.status === 'running' || task?.status === 'queued'
  const state = strataPreview?.state ?? strataState(snapshot, { taskRunning: running, approvalPending: approvals.length > 0 })
  const approval = approvals[0]
  // A waiting approval replaces the agent's words (which are usually its question), not yours.
  const said = snapshot.words && (snapshot.words.committed || snapshot.words.provisional) ? snapshot.words : null
  // Once your request is sent, the task's progress takes over from your words.
  const sent = said?.speaker === 'user' && running && !snapshot.listening
    && (snapshot.state === 'idle' || snapshot.state === 'open')
  // An update said mid-task gives way to the progress as soon as it has been said; only the
  // last reply lingers to be read.
  const update = said?.speaker === 'agent' && running && !snapshot.speaking
  const words = said && !sent && !update && !(approval && said.speaker === 'agent') ? said : null
  // The chime says an approval needs you (voice-protocol §8): once as its card shows, even over
  // the agent's speech, and again for each approval after it.
  const chiming = !words && !strataPreview ? approval?.id : undefined
  useEffect(() => {
    if (chiming !== undefined) voice().playback.cue(chime)
  }, [chiming])
  // An open conversation's next turn goes wherever the selection is now.
  const targetKey = JSON.stringify(target)
  useEffect(() => {
    voice().retarget(JSON.parse(targetKey) as Target)
  }, [targetKey])
  const projectOf = (taskId: number) => projects.find((p) => p.id === tasks.find((t) => t.id === taskId)?.project_id)

  const label = state === 'listening' ? 'Listening'
    : state === 'speaking' ? 'Speaking'
    : snapshot.state === 'transcribing' ? 'Hearing you'
    : state === 'approval' && !words ? (approvals.length > 1 ? `Needs you · 1 of ${approvals.length}` : 'Needs you')
    : state === 'thinking' ? 'Working' // the header names the project
    : !talk.ready ? (snapshot.connected ? 'Voice isn’t set up · type instead' : 'Connecting')
    : task ? 'Following up' : project ? 'New task' : 'Add a project'

  let body: ReactNode
  if (words) {
    body = <Caption committed={words.committed} provisional={words.provisional} speaker={words.speaker} />
  } else if (approval) {
    body = <StageApproval key={approval.id} approval={approval} project={projectOf(approval.task_id)}
      task={tasks.find((t) => t.id === approval.task_id)} onOpenTask={() => onOpenTask(approval.task_id)} />
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
        {activity(detail) === 'Thinking' ? <Ellipsis /> : (
          <p key={activity(detail)} className="animate-shimmer font-serif text-[clamp(26px,6vw,34px)] leading-[1.18] tracking-[-0.01em]">
            {activity(detail)}
          </p>
        )}
        {current(detail) && <p className="mt-3 truncate font-mono text-[12.5px] text-muted-foreground">› {current(detail)}</p>}
        <Button variant="ghost" size="sm" className="mt-3 -ml-2.5 font-sans text-muted-foreground" onClick={() => stopTask(task.id)}>
          <Square className="fill-current" /> Stop task
        </Button>
      </div>
    )
  } else if (task && lastReply(detail)) {
    // What you heard stays once its caption clears, rather than the reply written to be read. It
    // takes the caption's place, so it keeps its scroll and can still be scrolled.
    body = <Caption still speaker="agent" committed={spokenPart(lastReply(detail)!) ?? plainText(lastReply(detail)!)} />
  } else {
    body = <Lead quiet>{task ? task.title : 'What should we work on?'}</Lead>
  }

  return (
    <main className={cn('stage flex min-h-0 flex-col items-center justify-center gap-(--gap) overflow-hidden px-6 lg:px-14', className)}>
      {/* The label keeps the state in its colour meanwhile: working, speaking, needs you. */}
      {!discless && <button
        {...talk.props}
        className={cn('aspect-square shrink-0 touch-none rounded-full outline-none select-none focus-visible:ring-1 focus-visible:ring-ring',
          'transition-[width] duration-700 ease-strata enabled:cursor-pointer',
          // An approval's card is taller than the words the disc leaves room for.
          compact || (approval && !words) ? 'w-[calc(var(--disc)*0.62)]' : 'w-(--disc)')}
      >
        {strataPreview
          ? <Strata state={strataPreview.state} sources={strataPreview.sources} frozenAt={strataPreview.frozenAt} className="size-full" />
          : <Strata state={state} sources={voiceSources} className="size-full" />}
      </button>}
      <div className="relative w-full max-w-[34rem]">
        {limit && !approval && (
          // In the gap above the label, out of the layout, so the disc and the words don't move for it.
          <p className={cn('absolute bottom-full mb-2.5 truncate text-[13px]', limit.reached ? 'text-destructive' : 'text-warn')}
            title={limit.line}>{limit.line}</p>
        )}
        <p className={cn('eyebrow mb-3 flex min-h-5 items-center gap-1.5 text-muted-foreground transition-colors duration-400',
          state === 'listening' && 'text-foreground', (state === 'speaking' || state === 'thinking') && 'text-agent',
          state === 'approval' && !words && 'text-warn')}>
          <span className="shrink-0">{snapshot.conversation && state !== 'listening' && state !== 'speaking' ? `Conversation · ${label}` : label}</span>
          {model && (label === 'Following up' || label === 'New task') && (
            <>
              <span aria-hidden>·</span>
              <ModelMenu {...model} />
            </>
          )}
        </p>
        {/* A fixed height, so words filling it don't move the disc; only an approval's card, taller,
            changes it, and the height glides there and back. */}
        <div className={cn('font-serif text-[clamp(26px,6vw,34px)] transition-[height] duration-600 ease-strata [interpolate-size:allow-keywords]',
          approval && !words ? 'h-auto' : 'h-(--words)')}>{body}</div>
        {!approval && elsewhere[0] && (
          // Quiet, with no chime: the device following that task asks. This one can still answer.
          <button className="mt-3 inline-flex items-center gap-1.5 text-[13px] text-warn hover:underline"
            onClick={() => onSelectTask(elsewhere[0].task_id)}>
            <span className="size-1.5 rounded-full bg-warn" />
            {elsewhere.length > 1 ? `${elsewhere.length} other approvals need you` : `Another task needs you${projectOf(elsewhere[0].task_id) ? ` in ${projectOf(elsewhere[0].task_id)!.name}` : ''}`}
          </button>
        )}

      </div>
    </main>
  )
}

async function stopTask(id: number) {
  try {
    await api.cancel(id)
  } catch (error) {
    toast.error(error instanceof Error ? error.message : 'Could not stop the task')
  }
}

function Lead({ children, quiet, agent, className }: { children: ReactNode; quiet?: boolean; agent?: boolean; className?: string }) {
  return (
    <p className={cn('animate-word-in font-serif [overflow-wrap:anywhere]',
      agent ? 'text-[clamp(19px,4.5vw,23px)] leading-[1.4] italic' : 'text-[clamp(26px,6vw,34px)] leading-[1.18] tracking-[-0.01em] text-balance',
      quiet && 'text-muted-foreground', className)}>
      {children}
    </p>
  )
}

/** The oldest approval waiting on you. */
function StageApproval({ approval, project, task, onOpenTask }: { approval: Approval; project?: Project; task?: Task; onOpenTask: () => void }) {
  const { busy, decide } = useDecide(approval)
  const shown = approval.kind === 'command' ? approval.detail.command : approval.kind === 'tool' ? approval.detail.tool : null
  const destructive = approval.risk === 'destructive'
  return (
    <div className="animate-word-in">
      {/* Tasks in one project run side by side, so the project alone doesn't say which one asks. */}
      {task && (
        <button type="button" className="mb-2 block max-w-full truncate font-sans text-[13px] text-muted-foreground hover:text-foreground"
          onClick={onOpenTask}>
          {task.title}
        </button>
      )}
      <p className="line-clamp-3 font-serif text-[clamp(26px,6vw,34px)] leading-[1.18] tracking-[-0.01em] text-balance">
        {/* The command is in the box below; repeated here, a long one pushes the buttons away. */}
        {project?.name ?? 'The agent'} wants to {approval.kind === 'command' ? 'run a command' : approval.summary}
      </p>
      {shown && (
        <code className={cn('mt-3 block w-fit max-h-[5.5em] max-w-full overflow-y-auto rounded-lg px-2.5 py-1.5 font-mono text-[14px] leading-[1.5] break-all whitespace-pre-wrap',
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

/** Thinking: three dots rising in turn. */
function Ellipsis() {
  return (
    <p role="status" aria-label="Thinking" className="animate-word-in flex h-[1.18em] items-center gap-[0.22em] text-[clamp(26px,6vw,34px)] text-agent">
      {[0, 1, 2].map((i) => (
        <span key={i} className="animate-dot size-[0.22em] rounded-full bg-current" style={{ animationDelay: `${i * 0.16}s` }} />
      ))}
    </p>
  )
}

/** What the agent is doing, from its latest event in this turn. */
function activity(detail: TaskDetail | null): string {
  const last = detail?.events.findLast((e) => e.kind !== 'error')
  return last?.kind === 'command_started' ? 'Running a command'
    : last?.kind === 'tool_used' && last.data.status === 'running' ? 'Looking into it'
    : last?.kind === 'file_change' ? 'Editing files'
    : last?.kind === 'message_delta' ? 'Writing'
    : 'Thinking'
}

/** The command or tool call running now, if the latest one hasn't finished. */
function current(detail: TaskDetail | null): string | null {
  const last = detail?.events.findLast((e) => ['command_started', 'command_finished', 'tool_used'].includes(e.kind))
  if (last?.kind === 'tool_used') return last.data.status === 'running' ? [last.data.tool, last.data.summary].filter(Boolean).join(' ') : null
  return last?.kind === 'command_started' ? last.data.command ?? null : null
}

function lastReply(detail: TaskDetail | null): string | null {
  return detail?.messages.findLast((m) => m.role === 'agent')?.text ?? null
}
