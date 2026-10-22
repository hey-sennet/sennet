import { useEffect, useLayoutEffect, useRef, useState, useSyncExternalStore } from 'react'
import { ChevronDown, ChevronLeft, ChevronRight, Plus, Settings } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Toaster } from '@/components/ui/sonner'
import { Dock } from '@/components/Dock'
import { SettingsDialog } from '@/components/SettingsDialog'
import { Stage } from '@/components/Stage'
import { TaskDetail } from '@/components/TaskDetail'
import { TaskList } from '@/components/TaskList'
import { api } from '@/lib/api'
import { useLiveState, useTaskDetail, type Connection } from '@/lib/store'
import { cn } from '@/lib/utils'
import { voice, type Target } from '@/voice/voice'
import { toast } from 'sonner'

// From here the task rail and drawer replace the dock's tasks glyph and the sheet.
const desktopQuery = window.matchMedia('(min-width: 1024px)')

function useDesktop() {
  return useSyncExternalStore(
    (onChange) => {
      desktopQuery.addEventListener('change', onChange)
      return () => desktopQuery.removeEventListener('change', onChange)
    },
    () => desktopQuery.matches,
  )
}

/**
 * Strata's layout (design/README.md). The disc stays put on every view. Phone: one column with
 * the disc, its words and the dock; tasks open as a bottom sheet. Desktop: the task rail on the
 * left and the selected task's timeline on the right, each a drawer collapsed to a tab at its
 * edge by default, widening a column when open so the disc glides aside. The selected task is what talking and typing
 * follow up on; with none, they start a task in the header's project.
 */
export default function App() {
  const live = useLiveState()
  const desktop = useDesktop()
  const [selected, setSelected] = useState<number | null>(null)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [homeProjectId, setHomeProjectId] = useState<number | null>(null)
  const [typing, setTyping] = useState(false)
  const [railOpen, setRailOpen] = useState(false)
  const [panelOpen, setPanelOpen] = useState(false)
  const [sheet, setSheet] = useState<'closed' | 'list' | 'task'>('closed')
  const homeProject = live.projects.find((p) => p.id === homeProjectId) ?? live.projects[0]
  const task = live.tasks.find((t) => t.id === selected)
  const project = task ? live.projects.find((p) => p.id === task.project_id) : homeProject
  const { detail, streaming } = useTaskDetail(selected, live.subscribe)
  const target: Target = selected !== null ? { task_id: selected } : homeProject ? { project_id: homeProject.id } : {}

  // A spoken request starts a task, which becomes the one to follow up on, as a typed one does.
  const selectedRef = useRef(selected)
  useEffect(() => {
    selectedRef.current = selected
  }, [selected])
  useEffect(() => {
    const v = voice()
    const offError = v.onError((message) => toast.error(message))
    const offFinal = v.socket.on((message) => {
      if (message.type === 'final' && message.outcome === 'dispatched' && selectedRef.current === null && message.task_id) {
        setSelected(message.task_id)
      }
    })
    return () => {
      offError()
      offFinal()
    }
  }, [])

  // A deleted project takes its tasks with it. Only a task seen in the list counts as gone:
  // a new one can be selected (from a voice turn's `final`) before its own event arrives.
  const seenTasks = useRef(new Set<number>())
  useEffect(() => {
    if (selected === null || live.connection !== 'live') return
    if (live.tasks.some((t) => t.id === selected)) seenTasks.current.add(selected)
    else if (seenTasks.current.has(selected)) setSelected(null)
  }, [live.tasks, live.connection, selected])

  const selectedApprovals = live.approvals.filter((a) => a.task_id === selected)

  // The drawer follows the newest output however it grows, unless you've scrolled up to read
  // something earlier; a new message, or another task, brings it back to the bottom.
  const panelRef = useRef<HTMLDivElement>(null)
  const panelContentRef = useRef<HTMLDivElement>(null)
  const pinned = useRef(true)
  const panelTop = useRef(0)
  const followPanel = () => {
    const el = panelRef.current
    if (el && pinned.current) el.scrollTop = el.scrollHeight
  }
  useLayoutEffect(() => {
    pinned.current = true
    followPanel()
  }, [selected, detail?.messages.length])
  useEffect(() => {
    if (!panelContentRef.current) return
    const observer = new ResizeObserver(followPanel)
    observer.observe(panelContentRef.current)
    return () => observer.disconnect()
  }, [desktop])

  const openTask = (id: number) => {
    setSelected(id)
    if (desktop) setPanelOpen(true)
    else setSheet('task')
  }
  const newTask = () => {
    setSelected(null)
    setPanelOpen(false)
  }

  const activeTasks = live.tasks.filter((t) => t.status === 'running' || t.status === 'awaiting_approval' || t.status === 'queued').length
  const timeline = detail && (
    <TaskDetail detail={detail} streaming={streaming} approvals={selectedApprovals}
      project={live.projects.find((p) => p.id === detail.project_id)} />
  )

  return (
    <div
      className={cn('grid h-dvh grid-cols-1 grid-rows-[56px_minmax(0,1fr)_auto] overflow-hidden',
        'lg:grid-cols-[var(--rail)_minmax(0,1fr)_var(--panel)] lg:transition-[grid-template-columns] lg:duration-600 lg:ease-strata')}
      style={{ '--rail': railOpen ? '280px' : '0px', '--panel': panelOpen ? 'min(424px, 36vw)' : '0px' } as React.CSSProperties}
    >
      <header className="col-span-full flex items-center justify-between gap-3 px-5 lg:px-8">
        <button className="font-serif text-[22px]" onClick={newTask}>
          agent talk<span className="text-muted-foreground">.</span>
        </button>
        <div className="flex min-w-0 items-center gap-2">
          {live.projects.length > 0 && homeProject && (
            <label className="relative flex h-8 min-w-0 items-center gap-2 rounded-full border px-3 text-[13px] text-muted-foreground transition-colors hover:text-foreground">
              <span className="size-1.5 shrink-0 rounded-full bg-foreground/70" />
              <select
                aria-label="Project"
                className="min-w-0 appearance-none truncate bg-transparent pr-4 outline-none"
                value={homeProject.id}
                onChange={(event) => {
                  setHomeProjectId(Number(event.target.value))
                  setSelected(null)
                }}
              >
                {live.projects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
              </select>
              <ChevronDown className="pointer-events-none absolute right-2.5 size-3" />
            </label>
          )}
          <ConnectionDot connection={live.connection} />
          <Button variant="ghost" size="icon" aria-label="Settings" onClick={() => setSettingsOpen(true)}>
            <Settings />
          </Button>
        </div>
      </header>

      {desktop && (
        <aside aria-label="Tasks" className="relative col-start-1 row-span-2 row-start-2 mb-6 min-h-0">
          <button
            className="absolute top-1/2 -right-10 z-10 -mt-8 grid h-16 w-7 place-items-center rounded-[14px] border bg-background text-muted-foreground transition-colors hover:text-foreground"
            aria-label={railOpen ? 'Hide tasks' : 'Show tasks'} title={railOpen ? 'Hide tasks' : 'Show tasks'} aria-expanded={railOpen}
            onClick={() => setRailOpen(!railOpen)}
          >
            <ChevronRight className={cn('size-3.5 transition-transform duration-600 ease-strata', railOpen && 'rotate-180')} />
          </button>
          <div className={cn('absolute inset-y-0 right-0 flex w-[280px] flex-col pr-4 pl-6 transition-opacity duration-400 ease-strata',
            railOpen ? 'opacity-100' : 'pointer-events-none opacity-0')} inert={!railOpen}>
            <div className="mt-2 mb-1 flex items-center justify-between">
              <p className="eyebrow pl-3 text-muted-foreground">Tasks</p>
              <Button variant="ghost" size="sm" className="text-muted-foreground" onClick={newTask}>
                <Plus /> New task
              </Button>
            </div>
            <div className="min-h-0 flex-1 overflow-y-auto opacity-90">
              <TaskList tasks={live.tasks} projects={live.projects} approvals={live.approvals} selected={selected} onSelect={openTask} />
            </div>
          </div>
        </aside>
      )}

      <Stage
        target={target}
        project={project}
        task={task}
        detail={detail}
        approvals={live.approvals}
        tasks={live.tasks}
        projects={live.projects}
        compact={typing}
        onClearTask={newTask}
        onAddProject={() => setSettingsOpen(true)}
        className="lg:col-start-2 lg:row-start-2"
      />

      <Dock
        typing={typing}
        onTypingChange={setTyping}
        disabled={!project}
        placeholder={task ? 'Follow up on this task' : homeProject ? `Ask ${homeProject.agent} to do something in ${homeProject.name}` : 'Add a project first'}
        onSend={async (text) => {
          if (selected !== null) await api.followUp(selected, text)
          else if (homeProject) setSelected((await api.createTask(homeProject.id, text)).id)
        }}
        activeTasks={activeTasks}
        needsYou={live.approvals.length > 0}
        onOpenTasks={desktop ? undefined : () => setSheet(selected !== null ? 'task' : 'list')}
        className="lg:col-start-2 lg:row-start-3"
      />

      {desktop && (
        <aside aria-label="Task" className="relative col-start-3 row-span-2 row-start-2 mb-6">
          <button
            className="absolute top-1/2 -left-10 -mt-8 grid h-16 w-7 place-items-center rounded-[14px] border bg-background text-muted-foreground transition-colors hover:text-foreground"
            aria-label={panelOpen ? 'Hide task' : 'Show task'} title={panelOpen ? 'Hide task' : 'Show task'} aria-expanded={panelOpen}
            onClick={() => setPanelOpen(!panelOpen)}
          >
            <ChevronLeft className={cn('size-3.5 transition-transform duration-600 ease-strata', panelOpen && 'rotate-180')} />
          </button>
          {/* The padding keeps the scrollbar clear of the rounded corners. */}
          <div className={cn('h-full w-[min(400px,34vw)] overflow-hidden rounded-[26px] border bg-card py-5 transition-opacity duration-400 ease-strata',
            panelOpen ? 'opacity-100' : 'pointer-events-none opacity-0')} inert={!panelOpen}>
            <div ref={panelRef} className="h-full overflow-y-auto overscroll-contain px-6 py-1"
              onScroll={(event) => {
                // Only scrolling up lets go: output landing before this event fires leaves a gap below too.
                const el = event.currentTarget
                if (el.scrollHeight - el.scrollTop - el.clientHeight < 48) pinned.current = true
                else if (el.scrollTop < panelTop.current) pinned.current = false
                panelTop.current = el.scrollTop
              }}>
              <div ref={panelContentRef}>
                {timeline ?? <p className="text-sm text-muted-foreground">Pick a task from the list to see what it did.</p>}
              </div>
            </div>
          </div>
        </aside>
      )}

      {!desktop && (
        <>
          <div className={cn('fixed inset-0 z-20 bg-background/40 transition-opacity duration-500', sheet === 'closed' ? 'pointer-events-none opacity-0' : 'opacity-100')}
            onClick={() => setSheet('closed')} aria-hidden />
          <section
            aria-label="Tasks"
            inert={sheet === 'closed'}
            className={cn('fixed inset-x-2 bottom-2 z-30 max-h-[86dvh] overflow-y-auto rounded-[26px] border bg-card/95 px-[18px] pt-2.5 pb-[18px] backdrop-blur-xl',
              'transition-transform duration-650 ease-strata', sheet === 'closed' ? 'translate-y-[110%]' : 'translate-y-0')}
          >
            <button className="mx-auto mb-1.5 block h-4 w-12" aria-label="Close" onClick={() => setSheet('closed')}>
              <span className="mx-auto block h-1 w-9 rounded-full bg-foreground/15" />
            </button>
            {sheet === 'task' && timeline ? (
              <div className="pt-1">
                <button className="mb-3.5 -ml-1 inline-flex items-center gap-1 text-[13px] text-muted-foreground hover:text-foreground" onClick={() => setSheet('list')}>
                  <ChevronLeft className="size-3.5" /> Tasks
                </button>
                {timeline}
              </div>
            ) : (
              <>
                <div className="mb-1 flex items-center justify-between">
                  <p className="eyebrow pl-3 text-muted-foreground">Tasks</p>
                  <Button variant="ghost" size="sm" className="text-muted-foreground" onClick={() => { newTask(); setSheet('closed') }}>
                    <Plus /> New task
                  </Button>
                </div>
                <TaskList tasks={live.tasks} projects={live.projects} approvals={live.approvals} selected={selected} onSelect={openTask} />
              </>
            )}
          </section>
        </>
      )}

      <SettingsDialog open={settingsOpen} onOpenChange={setSettingsOpen} projects={live.projects} rules={live.rules} settings={live.settings} />
      <Toaster position="top-center" />
    </div>
  )
}

function ConnectionDot({ connection }: { connection: Connection }) {
  const label = { connecting: 'Connecting', live: 'Connected', reconnecting: 'Reconnecting' }[connection]
  return (
    <span className="flex items-center gap-2 px-1 text-xs text-muted-foreground" title={label}>
      <span className={cn('size-2 rounded-full', connection === 'live' ? 'bg-foreground/60' : 'animate-pulse bg-warn')} />
      <span className="sr-only">{label}</span>
    </span>
  )
}
