import { useEffect, useLayoutEffect, useRef, useState, useSyncExternalStore, type UIEvent } from 'react'
import { ChevronLeft, ChevronRight, Plus, Settings } from 'lucide-react'
import { Button } from '@/components/ui/button'
import { Toaster } from '@/components/ui/sonner'
import { Dock } from '@/components/Dock'
import { ProjectMenu } from '@/components/ProjectMenu'
import { SettingsDialog } from '@/components/SettingsDialog'
import { Stage } from '@/components/Stage'
import { StopTask, TaskDetail } from '@/components/TaskDetail'
import { TaskList } from '@/components/TaskList'
import { useAgents } from '@/lib/agents'
import { api, type ModelChoice } from '@/lib/api'
import { limitLine, pressing } from '@/lib/limits'
import { approvalsHere, useMine } from '@/lib/mine'
import { useLiveState, useTaskDetail, type Connection } from '@/lib/store'
import { useKeyboardUp } from '@/lib/keyboard'
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
  const [mine, remember] = useMine()
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [homeProjectId, setHomeProjectId] = useState<number | null>(null)
  const [typing, setTyping] = useState(false)
  const [railOpen, setRailOpen] = useState(false)
  const [panelOpen, setPanelOpen] = useState(false)
  const [sheet, setSheet] = useState<'closed' | 'list' | 'task'>('closed')
  const keyboardUp = useKeyboardUp()
  const homeProject = live.projects.find((p) => p.id === homeProjectId) ?? live.projects[0]
  const task = live.tasks.find((t) => t.id === selected)
  const project = task ? live.projects.find((p) => p.id === task.project_id) : homeProject
  const { detail, streaming } = useTaskDetail(selected, live.subscribe, live.reconnects)
  const agents = useAgents()
  // The next task's model, chosen before it starts; it belongs to the project it was chosen in.
  const [draft, setDraft] = useState<ModelChoice & { project_id: number | null }>({ project_id: null, model: null, effort: null })
  const nextChoice: ModelChoice = draft.project_id === homeProject?.id ? draft : { model: null, effort: null }
  const target: Target = selected !== null ? { task_id: selected }
    : homeProject ? { project_id: homeProject.id, ...nextChoice } : {}

  // After an upgrade the server comes back newer than this page; offer the new client.
  useEffect(() => {
    if (!live.updated) return
    toast('Sennet was updated', {
      id: 'updated', duration: Infinity, description: 'Reload to use the new version.',
      action: { label: 'Reload', onClick: () => location.reload() },
    })
  }, [live.updated])

  // A spoken request starts a task, which becomes the one to follow up on, as a typed one does.
  const selectedRef = useRef(selected)
  useEffect(() => {
    selectedRef.current = selected
  }, [selected])
  // Tasks with a reply that wasn't spoken, being about a task not on screen. Opening one clears it.
  const [unheard, setUnheard] = useState<ReadonlySet<number>>(() => new Set())
  if (selected !== null && unheard.has(selected)) {
    const next = new Set(unheard)
    next.delete(selected)
    setUnheard(next)
  }
  const replyElsewhere = live.tasks.some((t) => unheard.has(t.id))
  useEffect(() => {
    const v = voice()
    const offError = v.onError((message) => toast.error(message))
    const offFinal = v.socket.on((message) => {
      if (message.type === 'final' && message.outcome === 'dispatched' && message.task_id) remember(message.task_id)
      // The task spoken to is the one on screen, whose replies are spoken (the server takes it so too).
      if (message.type === 'final' && message.outcome === 'dispatched' && message.task_id && selectedRef.current !== message.task_id) {
        if (selectedRef.current === null) setDraft({ project_id: null, model: null, effort: null })
        setSelected(message.task_id)
      }
      if (message.type === 'unheard' && message.task_id !== selectedRef.current) {
        const id = message.task_id
        setUnheard((current) => (current.has(id) ? current : new Set(current).add(id)))
      }
    })
    return () => {
      offError()
      offFinal()
    }
  }, [])

  // A deleted task, or one whose project was removed, stops being the one followed up. A task can
  // be missing from the list without being gone (an older one, opened from search), so only the
  // events, or the server after a reconnect, say so.
  const { addTasks, reconnects, subscribe } = live
  const projectOfSelected = useRef<number | null>(null)
  const tasksRef = useRef(live.tasks)
  useEffect(() => {
    tasksRef.current = live.tasks
    const found = live.tasks.find((t) => t.id === selected)
    if (found) projectOfSelected.current = found.project_id
  }, [live.tasks, selected])
  useEffect(() => subscribe((event) => {
    if ((event.type === 'task_removed' && event.task_id === selectedRef.current)
      || (event.type === 'project_removed' && event.project_id === projectOfSelected.current)) setSelected(null)
  }), [subscribe])
  // After a reconnect the list starts over; the selected task, if it isn't in it, is asked for. Only
  // then: a new task can be selected (from a voice turn) before its own event arrives.
  useEffect(() => {
    const id = selectedRef.current
    if (!reconnects || id === null || tasksRef.current.some((t) => t.id === id)) return
    api.task(id).then((found) => addTasks([found]), () => setSelected((now) => (now === id ? null : now)))
  }, [reconnects, addTasks])

  const selectedApprovals = live.approvals.filter((a) => a.task_id === selected)
  // The stage asks only about this device's tasks; another device may be following the rest.
  const { here, elsewhere } = approvalsHere(live.approvals, selected, mine)

  // The drawer, or on a phone the sheet's task, opens at the newest output and follows it however
  // it grows, unless you've scrolled up to read something earlier; a new message, or another
  // task, brings it back to the bottom.
  const panelRef = useRef<HTMLDivElement>(null)
  const panelContentRef = useRef<HTMLDivElement>(null)
  const pinned = useRef(true)
  const panelTop = useRef(0)
  const followPanel = () => {
    const el = panelRef.current
    if (el && pinned.current) el.scrollTop = el.scrollHeight
  }
  const trackPanel = (event: UIEvent<HTMLDivElement>) => {
    // Only scrolling up lets go: output landing before this event fires leaves a gap below too.
    const el = event.currentTarget
    if (el.scrollHeight - el.scrollTop - el.clientHeight < 48) pinned.current = true
    else if (el.scrollTop < panelTop.current) pinned.current = false
    panelTop.current = el.scrollTop
  }
  const hasDetail = detail !== null
  useLayoutEffect(() => {
    pinned.current = true
    followPanel()
  }, [selected, detail?.messages.length, sheet, hasDetail])
  useEffect(() => {
    if (!panelContentRef.current) return
    const observer = new ResizeObserver(followPanel)
    observer.observe(panelContentRef.current)
    return () => observer.disconnect()
  }, [desktop, sheet, hasDetail])

  // Keys on a keyboard: N for a new task, / to type, Escape to close what's open. Not while
  // typing in a field or a dialog is up, and never with a modifier held.
  const keys = useRef({ newTask: () => {}, typing, panelOpen, railOpen, settingsOpen })
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.ctrlKey || event.metaKey || event.altKey || event.defaultPrevented) return
      const target = event.target as HTMLElement | null
      if (target?.closest('input, textarea, select, [contenteditable="true"], [role="dialog"]')) return
      const now = keys.current
      if (now.settingsOpen) return
      if (event.key === 'n' || event.key === 'N') now.newTask()
      else if (event.key === '/') setTyping(true)
      else if (event.key === 'Escape' && now.typing) setTyping(false)
      else if (event.key === 'Escape' && now.panelOpen) setPanelOpen(false)
      else if (event.key === 'Escape' && now.railOpen) setRailOpen(false)
      else return
      event.preventDefault()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [])

  // Moving yourself, not by talking, leaves the last words behind with the task they were about.
  const select = (id: number | null) => {
    if (id !== selectedRef.current) voice().leave(id)
    setSelected(id)
  }
  const openTask = (id: number) => {
    select(id)
    if (desktop) setPanelOpen(true)
    else setSheet('task')
  }
  const newTask = () => {
    if (task) setHomeProjectId(task.project_id) // the header showed the task's project: start there
    select(null)
    setPanelOpen(false)
  }
  useEffect(() => {
    keys.current = { newTask, typing, panelOpen, railOpen, settingsOpen }
  })

  // The model glyph's menu: the selected task's own choice, or the next task's. The project's
  // choice is the default, while the project still uses the task's agent.
  const modelAgent = task?.agent ?? homeProject?.agent
  const inherited = project && project.agent === modelAgent
    ? { model: project.model, effort: project.effort, from: `${project.name}'s default` } : undefined
  const modelMenu = modelAgent ? {
    agent: agents?.[modelAgent],
    ...(task ? { model: task.model, effort: task.effort } : nextChoice),
    inherited,
    heading: task ? 'Model for this task' : `Model for a new task in ${homeProject?.name}`,
    note: task && ['running', 'awaiting_approval', 'background'].includes(task.status) ? 'From your next message' : undefined,
    onChange: async (model: string | null, effort: string | null) => {
      if (!task) return setDraft({ project_id: homeProject?.id ?? null, model, effort })
      try {
        await api.chooseModel(task.id, model, effort)
      } catch (error) {
        toast.error(error instanceof Error ? error.message : 'Could not change the model')
      }
    },
  } : undefined

  // A limit in the way of what you say next, or close to it, for the agent it would go to.
  const limitAgent = task?.agent ?? homeProject?.agent
  const limitWindow = limitAgent ? pressing(live.limits[limitAgent]) : null
  const limit = limitAgent && limitWindow ? { line: limitLine(agents?.[limitAgent]?.title ?? limitAgent, limitWindow), reached: limitWindow.used >= 100 } : undefined

  const activeTasks = live.tasks.filter((t) => t.status === 'running' || t.status === 'awaiting_approval' || t.status === 'queued').length
  const timeline = detail && (
    <TaskDetail detail={detail} streaming={streaming} approvals={selectedApprovals} stop={desktop}
      project={live.projects.find((p) => p.id === detail.project_id)} />
  )

  return (
    <div
      className={cn('grid h-dvh grid-cols-1 grid-rows-[56px_minmax(0,1fr)_auto] overflow-hidden',
        'lg:grid-cols-[var(--rail)_minmax(0,1fr)_var(--panel)] lg:transition-[grid-template-columns] lg:duration-600 lg:ease-strata')}
      style={{ '--rail': railOpen ? '280px' : '0px', '--panel': panelOpen ? 'min(424px, 36vw)' : '0px' } as React.CSSProperties}
    >
      <header className="col-span-full flex items-center justify-between gap-3 px-5 lg:px-8">
        <button className="font-serif text-[22px] tracking-[0.08em] uppercase" onClick={newTask}>
          Sennet<span className="-ml-[0.08em] text-muted-foreground">.</span>
        </button>
        <div className="flex min-w-0 items-center gap-2">
          {live.projects.length > 0 && homeProject && (
            <ProjectMenu projects={live.projects} current={project ?? homeProject} agents={agents} onChange={(id) => {
              setHomeProjectId(id)
              select(null)
            }} />
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
            className="absolute top-1/2 -right-10 z-10 -mt-8 flex h-16 w-7 flex-col items-center justify-center gap-1 rounded-[14px] border bg-background text-muted-foreground transition-colors hover:text-foreground"
            aria-label={railOpen ? 'Hide tasks' : `Show tasks${activeTasks ? `, ${activeTasks} active` : ''}${live.approvals.length ? `, ${live.approvals.length} need${live.approvals.length === 1 ? 's' : ''} you` : ''}${replyElsewhere ? ', a new reply' : ''}`}
            title={railOpen ? 'Hide tasks' : 'Show tasks'} aria-expanded={railOpen}
            onClick={() => setRailOpen(!railOpen)}
          >
            {/* Closed, it says what the phone's tasks glyph does: how many are working, and whether one needs you
                or has a reply you didn't hear. */}
            {!railOpen && activeTasks > 0 && <span className="text-[11px] leading-none font-medium tabular-nums">{activeTasks}</span>}
            <ChevronRight className={cn('size-3.5 transition-transform duration-600 ease-strata', railOpen && 'rotate-180')} />
            {!railOpen && (live.approvals.length > 0 || replyElsewhere) && (
              <span className={cn('absolute -top-1 -right-1 size-2.5 rounded-full border-2 border-background',
                live.approvals.length > 0 ? 'bg-warn' : 'bg-agent')} />
            )}
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
              <TaskList tasks={live.tasks} projects={live.projects} approvals={live.approvals} agents={agents} selected={selected}
                more={live.more_tasks} unheard={unheard} onSelect={openTask} onShowMore={live.showMore} onFound={live.addTasks} />
            </div>

          </div>
        </aside>
      )}

      <Stage
        target={target}
        project={project}
        task={task}
        detail={detail}
        approvals={here}
        elsewhere={elsewhere}
        tasks={live.tasks}
        projects={live.projects}
        compact={typing}
        discless={typing && !desktop && keyboardUp}
        model={modelMenu}
        limit={limit}
        onSelectTask={select}
        onAddProject={() => setSettingsOpen(true)}
        className="lg:col-start-2 lg:row-start-2"
      />

      <Dock
        typing={typing}
        onTypingChange={setTyping}
        disabled={!project}
        // Short enough for one line on a phone: the label and the header already name the project and model.
        placeholder={task ? 'Follow up on this task' : homeProject ? 'Start a new task' : 'Add a project first'}
        onSend={async (text) => {
          if (selected !== null) {
            await api.followUp(selected, text)
            remember(selected)
          } else if (homeProject) {
            const created = await api.createTask(homeProject.id, text, nextChoice)
            remember(created.id)
            setSelected(created.id)
            setDraft({ project_id: null, model: null, effort: null })
          }
        }}
        target={target}
        onNewTask={selected !== null ? newTask : undefined}
        activeTasks={activeTasks}
        needsYou={live.approvals.length > 0}
        replyElsewhere={replyElsewhere}
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
            <div ref={panelRef} className="h-full overflow-y-auto overscroll-contain px-6 py-1" onScroll={trackPanel}>
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
            className={cn('fixed inset-x-2 bottom-2 z-30 flex max-h-[86dvh] flex-col overflow-hidden rounded-[26px] border bg-card/95 pt-2.5 backdrop-blur-xl',
              'transition-transform duration-650 ease-strata', sheet === 'closed' ? 'translate-y-[110%]' : 'translate-y-0')}
          >
            {/* Out of the scroll, so the task's actions are there however far down you are. */}
            <div className="shrink-0 px-[18px]">
              <button className="mx-auto mb-1.5 block h-4 w-12" aria-label="Close" onClick={() => setSheet('closed')}>
                <span className="mx-auto block h-1 w-9 rounded-full bg-foreground/15" />
              </button>
              {sheet === 'task' && detail && (
                <div className="mt-1 mb-2 flex h-8 items-center justify-between">
                  <button className="-ml-1 inline-flex items-center gap-1 text-[13px] text-muted-foreground hover:text-foreground" onClick={() => setSheet('list')}>
                    <ChevronLeft className="size-3.5" /> Tasks
                  </button>
                  <StopTask detail={detail} className="-mr-2.5" />
                </div>
              )}
            </div>
            {sheet === 'task' && timeline ? (
              <div ref={panelRef} className="min-h-0 overflow-y-auto overscroll-contain px-[18px] pt-1.5 pb-[18px]" onScroll={trackPanel}>
                <div ref={panelContentRef}>{timeline}</div>
              </div>
            ) : (
              <div className="min-h-0 overflow-y-auto overscroll-contain px-[18px] pb-[18px]">
                <div className="mb-1 flex items-center justify-between">
                  <p className="eyebrow pl-3 text-muted-foreground">Tasks</p>
                  <Button variant="ghost" size="sm" className="text-muted-foreground" onClick={() => { newTask(); setSheet('closed') }}>
                    <Plus /> New task
                  </Button>
                </div>
                <TaskList tasks={live.tasks} projects={live.projects} approvals={live.approvals} agents={agents} selected={selected}
                more={live.more_tasks} unheard={unheard} onSelect={openTask} onShowMore={live.showMore} onFound={live.addTasks} />
              </div>
            )}
          </section>
        </>
      )}

      <SettingsDialog open={settingsOpen} onOpenChange={setSettingsOpen} projects={live.projects} rules={live.rules} settings={live.settings} version={live.version} limits={live.limits}
        subscribe={live.subscribe} />
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
