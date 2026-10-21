import { useEffect, useState, useSyncExternalStore } from 'react'
import { Plus, Settings } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Toaster } from '@/components/ui/sonner'
import { ApprovalCard } from '@/components/ApprovalCard'
import { Composer } from '@/components/Composer'
import { SettingsDialog } from '@/components/SettingsDialog'
import { TaskList } from '@/components/TaskList'
import { TaskView } from '@/components/TaskView'
import { api } from '@/lib/api'
import { useLiveState, useTaskDetail, type Connection } from '@/lib/store'
import { cn } from '@/lib/utils'

const desktopQuery = window.matchMedia('(min-width: 768px)')

function useDesktop() {
  return useSyncExternalStore(
    (onChange) => {
      desktopQuery.addEventListener('change', onChange)
      return () => desktopQuery.removeEventListener('change', onChange)
    },
    () => desktopQuery.matches,
  )
}

export default function App() {
  const live = useLiveState()
  const desktop = useDesktop()
  const [selected, setSelected] = useState<number | null>(null)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const { detail, streaming } = useTaskDetail(selected, live.subscribe)
  const selectedApprovals = live.approvals.filter((a) => a.task_id === selected)
  const otherApprovals = live.approvals.filter((a) => a.task_id !== selected)

  // A deleted project takes its tasks with it.
  useEffect(() => {
    if (selected !== null && live.connection === 'live' && !live.tasks.some((t) => t.id === selected)) setSelected(null)
  }, [live.tasks, live.connection, selected])

  return (
    <div className="flex h-dvh flex-col">
      <header className="flex h-14 shrink-0 items-center justify-between border-b px-4 md:px-6">
        <button className="text-[17px] font-medium tracking-tight" onClick={() => setSelected(null)}>
          agent talk<span className="text-muted-foreground">.</span>
        </button>
        <div className="flex items-center gap-3">
          <ConnectionDot connection={live.connection} />
          <Button variant="ghost" size="icon" aria-label="Settings" onClick={() => setSettingsOpen(true)}>
            <Settings />
          </Button>
        </div>
      </header>

      <div className="flex min-h-0 flex-1">
        <aside className={cn('flex w-full flex-col border-r md:w-80 md:shrink-0', selected !== null && 'hidden md:flex')}>
          <div className="p-3">
            <Button variant="outline" className="w-full justify-start" onClick={() => setSelected(null)}>
              <Plus /> New task
            </Button>
          </div>
          {selected === null && !desktop && <div className="px-3 pb-3"><NewTask live={live} onCreated={setSelected} onOpenSettings={() => setSettingsOpen(true)} /></div>}
          <div className="min-h-0 flex-1 overflow-y-auto px-2 pb-4">
            <TaskList tasks={live.tasks} projects={live.projects} approvals={live.approvals} selected={selected} onSelect={setSelected} />
          </div>
        </aside>

        <main className={cn('min-w-0 flex-1 flex-col', selected === null ? 'hidden md:flex' : 'flex')}>
          {otherApprovals.length > 0 && (
            <div className="flex max-h-[45%] flex-col gap-3 overflow-y-auto border-b bg-muted/20 px-4 py-4 md:px-8">
              {otherApprovals.map((approval) => {
                const task = live.tasks.find((t) => t.id === approval.task_id)
                return (
                  <ApprovalCard key={approval.id} approval={approval} task={task}
                    project={live.projects.find((p) => p.id === task?.project_id)}
                    onOpenTask={() => setSelected(approval.task_id)} />
                )
              })}
            </div>
          )}
          {selected !== null && detail ? (
            <TaskView detail={detail} streaming={streaming} approvals={selectedApprovals}
              project={live.projects.find((p) => p.id === detail.project_id)} onBack={() => setSelected(null)} />
          ) : selected === null && desktop ? (
            <div className="flex flex-1 items-center justify-center px-4">
              <div className="w-full max-w-2xl">
                <h1 className="mb-1 text-2xl font-medium tracking-tight">What should we work on?</h1>
                <p className="mb-6 text-sm text-muted-foreground">Voice comes next. For now, type a request.</p>
                <NewTask live={live} onCreated={setSelected} autoFocus onOpenSettings={() => setSettingsOpen(true)} />
              </div>
            </div>
          ) : null}
        </main>
      </div>

      <SettingsDialog open={settingsOpen} onOpenChange={setSettingsOpen} projects={live.projects} rules={live.rules} settings={live.settings} />
      <Toaster position="top-center" />
    </div>
  )
}

function NewTask({ live, onCreated, autoFocus, onOpenSettings }: {
  live: ReturnType<typeof useLiveState>
  onCreated: (id: number) => void
  autoFocus?: boolean
  onOpenSettings?: () => void
}) {
  const [projectId, setProjectId] = useState<number | null>(null)
  const project = live.projects.find((p) => p.id === projectId) ?? live.projects[0]

  if (live.connection !== 'live' && !live.projects.length) {
    return <p className="text-sm text-muted-foreground">Connecting…</p>
  }
  if (!live.projects.length) {
    return (
      <div className="rounded-2xl border border-dashed p-6 text-sm text-muted-foreground">
        Add a project first: a directory your agent should work in.
        <div className="mt-3 flex flex-wrap gap-2">
          {onOpenSettings && <Button size="sm" onClick={onOpenSettings}>Add a project</Button>}
          <code className="self-center font-mono text-xs">agent-talk project add ~/code/app</code>
        </div>
      </div>
    )
  }
  return (
    <Composer
      autoFocus={autoFocus}
      placeholder={`Ask ${project.agent} to do something in ${project.name}`}
      onSend={async (text) => onCreated((await api.createTask(project.id, text)).id)}
    >
      <select
        aria-label="Project"
        className="max-w-full truncate rounded-md bg-transparent py-1 text-sm text-muted-foreground hover:text-foreground"
        value={project.id}
        onChange={(event) => setProjectId(Number(event.target.value))}
      >
        {live.projects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
      </select>
    </Composer>
  )
}

function ConnectionDot({ connection }: { connection: Connection }) {
  const label = { connecting: 'Connecting', live: 'Connected', reconnecting: 'Reconnecting' }[connection]
  return (
    <span className="flex items-center gap-2 text-xs text-muted-foreground" title={label}>
      <span className={cn('size-2 rounded-full', connection === 'live' ? 'bg-emerald-400' : 'animate-pulse bg-amber-400')} />
      <span className="hidden sm:inline">{label}</span>
    </span>
  )
}
