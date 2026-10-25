import { useEffect, useState } from 'react'
import { Trash2, X } from 'lucide-react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { refreshAgents, useAgents } from '@/lib/agents'
import { api, type Access, type AgentLimits, type AgentStatus, type ApprovalMode, type Project, type Rule, type ServerEvent, type State } from '@/lib/api'
import { cn } from '@/lib/utils'
import { useVoice } from '@/voice/usePushToTalk'
import { voice } from '@/voice/voice'
import { ConfirmDialog } from './ConfirmDialog'
import { Devices } from './Devices'
import { FolderBrowser } from './FolderBrowser'
import { Limits } from './Limits'
import { Notifications } from './Notifications'
import { ModelPicker } from './ModelPicker'

const MODES: { value: ApprovalMode | ''; label: string }[] = [
  { value: '', label: 'Default' },
  { value: 'ask', label: 'Ask first' },
  { value: 'auto', label: 'Auto within its access' },
  { value: 'never', label: 'Never ask' },
  { value: 'classifier', label: 'Classifier decides' },
]

const MODE_HELP: Record<ApprovalMode, string> = {
  ask: 'The agent asks before running anything outside its trusted set.',
  auto: 'The agent works freely within its access level and asks only to go beyond it.',
  never: 'Nothing is asked. Actions that need more access fail instead.',
  classifier: 'Nothing is asked up front: the agent\'s own safety classifier allows or blocks each action. It isn\'t a sandbox, so it can reach outside the project.',
}

const ACCESS: { value: Access | ''; label: string }[] = [
  { value: '', label: 'Default' },
  { value: 'read-only', label: 'Read only' },
  { value: 'workspace', label: 'This project' },
  { value: 'full', label: 'Full access' },
]

const ACCESS_HELP: Record<Access, string> = {
  'read-only': 'The agent may read, but not change files.',
  workspace: 'The agent may change files in this project.',
  full: 'The agent may do anything you can.',
}

// The main screen's shapes: pills for controls, rounded cards for rows.
const field = 'h-8 rounded-full border border-border bg-background px-3 text-xs text-foreground outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:opacity-50'
const card = 'min-w-0 rounded-[22px] border bg-card'

async function attempt(action: () => Promise<unknown>, failure: string) {
  try {
    await action()
    return true
  } catch (error) {
    toast.error(error instanceof Error ? error.message : failure)
    return false
  }
}

export function SettingsDialog({ open, onOpenChange, projects, rules, settings, version, limits, subscribe }: {
  open: boolean
  onOpenChange: (open: boolean) => void
  projects: Project[]
  rules: Rule[]
  settings: State['settings'] | null
  version: string | null
  limits: Record<string, AgentLimits> // usage limits by agent, for agents that report them
  subscribe: (listener: (event: ServerEvent) => void) => () => void
}) {
  const agents = useAgents()

  useEffect(() => {
    if (!open) return
    void refreshAgents()
    api.limits().catch(() => {}) // asks again for limits a few minutes old; they arrive as live events
  }, [open])

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      {/* A column of its own, scrolled inside, so nothing wide (a long path) pushes it off a phone. */}
      <DialogContent className="flex max-h-[min(90dvh,880px)] w-[calc(100%-2rem)] flex-col gap-0 overflow-hidden rounded-[28px] border border-border bg-background p-0 ring-0 duration-300 ease-strata sm:max-w-2xl">
        <div className="flex min-w-0 flex-col gap-9 overflow-y-auto overscroll-contain px-6 pt-8 pb-10 sm:px-10 sm:pt-10">
        <DialogHeader className="animate-word-in gap-1.5 pr-8 motion-reduce:animate-none">
          <DialogTitle className="font-serif text-[30px] leading-[1.1] font-normal tracking-[-0.01em]">Settings</DialogTitle>
          <DialogDescription>Projects, approvals, agents, notifications and paired devices.</DialogDescription>
        </DialogHeader>

        <Section title="Agents" order={1}>
          {!agents && <p className="text-sm text-muted-foreground">Checking…</p>}
          {agents && (
            <div className={cn(card, 'divide-y')}>
              {Object.entries(agents).map(([name, status]) => {
                const ready = status.installed && status.logged_in
                return (
                  <div key={name} className="flex flex-col gap-3 px-4 py-3 text-sm">
                    <div className="flex items-center justify-between gap-4">
                      <span className="font-medium">{status.title}</span>
                      <span className={cn('flex min-w-0 items-center gap-2 text-right', ready ? 'text-muted-foreground' : 'text-warn')}>
                        <span className={cn('size-1.5 shrink-0 rounded-full', ready ? 'bg-agent' : 'bg-warn')} />
                        {status.detail}
                      </span>
                    </div>
                    {!!limits[name]?.windows.length && <Limits limits={limits[name]} />}
                  </div>
                )
              })}
            </div>
          )}
        </Section>

        <Section title="Projects" order={2}>
          <div className="flex flex-col gap-2">
            {projects.map((project) => (
              <ProjectRow key={project.id} project={project} defaultMode={settings?.approvals.mode ?? 'ask'}
                defaultAccess={settings?.approvals.access ?? 'workspace'} agents={agents} />
            ))}
            {!projects.length && <p className="text-sm text-muted-foreground">No projects yet.</p>}
          </div>
          <AddProject agents={agents} />
        </Section>

        <Section title="Remembered approvals" hint={'Created when you choose "Always". Destructive actions are always asked.'} order={3}>
          {!rules.length && <p className="text-sm text-muted-foreground">None yet.</p>}
          {!!rules.length && (
            <div className={cn(card, 'divide-y')}>
              {rules.map((rule) => (
                <div key={rule.id} className="flex items-center justify-between gap-3 py-1.5 pr-1.5 pl-4 text-sm">
                  <span className="min-w-0">
                    <span className="text-muted-foreground">{projects.find((p) => p.id === rule.project_id)?.name}: </span>
                    {rule.kind === 'edit' ? 'all file edits'
                      : rule.kind === 'tool' ? <>the <code className="font-mono text-xs">{rule.pattern}</code> tool</>
                      : <code className="font-mono text-xs [overflow-wrap:anywhere]">{rule.pattern} …</code>}
                  </span>
                  <Button variant="ghost" size="icon-sm" className="rounded-full" aria-label="Forget this approval"
                    onClick={() => attempt(() => api.removeRule(rule.id), 'Could not remove the rule')}>
                    <X />
                  </Button>
                </div>
              ))}
            </div>
          )}
        </Section>

        <WakeWord />

        <Section title="Notifications" hint="On this device only." order={5}>
          <Notifications />
        </Section>

        <Section title="Devices" hint="Browsers paired with Sennet. Unpair any you no longer use." order={6}>
          <Devices subscribe={subscribe} />
        </Section>

        <About version={version} />
        </div>
      </DialogContent>
    </Dialog>
  )
}

/** The wake word on this device, when the server has a model for one. */
function WakeWord() {
  const { wakePhrase, wakeEnabled } = useVoice()
  if (!wakePhrase) return null
  return (
    <Section title="Wake word" hint="On this device only. It starts listening at your first tap on the page." order={4}>
      <label className="flex items-start gap-2 text-sm">
        {/* Turning it on is a tap, so it can start listening at once (voice.ts). */}
        <input type="checkbox" className="mt-1" data-wake-control checked={wakeEnabled}
          onChange={(event) => voice().setWakeEnabled(event.target.checked)} />
        <span>
          Listen for “{wakePhrase}”
          <span className="block text-xs text-muted-foreground">
            The microphone stays open and the screen stays on while the page is showing; only this
            browser hears it until you say the phrase. Tap the phrase under the disc to pause it.
          </span>
        </span>
      </label>
    </Section>
  )
}

function About({ version }: { version: string | null }) {
  const link = 'underline underline-offset-2 hover:text-foreground'
  return (
    <Section title="About" order={7}>
      <p className="text-sm">
        Sennet {version ?? ''}
        <span className="text-muted-foreground"> · free and open source under the Apache License 2.0</span>
      </p>
      <p className="text-xs text-muted-foreground">
        Speech recognition uses NVIDIA's Parakeet TDT 0.6B v2 (CC-BY-4.0), and speech is spoken by Kokoro-82M
        (Apache-2.0) through espeak-ng and phonemizer (GPL-3.0). <a className={link} href="/notices.txt" target="_blank"
          rel="noreferrer">Notices</a> lists every model and component with its license, and <a className={link}
          href="/licenses.txt" target="_blank" rel="noreferrer">licenses</a> the packages in this app.
      </p>
    </Section>
  )
}

function Section({ title, hint, order, children }: { title: string; hint?: string; order: number; children: React.ReactNode }) {
  return (
    // Each section rises in after the one above it, as words do under the disc.
    <section className="flex min-w-0 animate-word-in flex-col gap-3 motion-reduce:animate-none" style={{ animationDelay: `${order * 60}ms` }}>
      <div>
        <h3 className="eyebrow text-muted-foreground">{title}</h3>
        {hint && <p className="mt-1.5 text-[13px] text-muted-foreground">{hint}</p>}
      </div>
      {children}
    </section>
  )
}

function ProjectRow({ project, defaultMode, defaultAccess, agents }: {
  project: Project
  defaultMode: ApprovalMode
  defaultAccess: Access
  agents: Record<string, AgentStatus> | null  // unknown until they load
}) {
  const [removing, setRemoving] = useState(false)
  const capabilities = agents?.[project.agent]?.capabilities
  const supported = capabilities?.approval_modes
  const mode = project.approval_mode ?? defaultMode
  const access = project.access ?? defaultAccess
  const unsupported = (value: ApprovalMode | '') => !!supported && !supported.includes(value || defaultMode)
  const noAccess = (value: Access | '') => !!capabilities && !capabilities.access_levels.includes(value || defaultAccess)
  return (
    <div className={cn(card, 'px-4 py-3.5')}>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-sm font-medium">{project.name}</p>
          <p className="truncate font-mono text-xs text-muted-foreground">{project.path}</p>
        </div>
        <Button variant="ghost" size="icon-sm" className="rounded-full" aria-label={`Remove ${project.name}`}
          onClick={() => setRemoving(true)}>
          <Trash2 />
        </Button>
        <ConfirmDialog open={removing} title={`Remove ${project.name}?`} action="Remove" onCancel={() => setRemoving(false)}
          onConfirm={() => {
            setRemoving(false)
            void attempt(() => api.removeProject(project.id), 'Could not remove the project')
          }}>
          Its files are not touched.
        </ConfirmDialog>
      </div>
      <label className="mt-2 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
        Agent
        <AgentSelect agents={agents} value={project.agent}
          onChange={(agent) => attempt(() => api.updateProject(project.id, { agent }), 'Could not update the project')} />
        <span>New tasks use it; tasks already started keep theirs.</span>
      </label>
      {!!agents?.[project.agent]?.models.length && (
        <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
          Model
          <ModelPicker agent={agents[project.agent]} model={project.model} effort={project.effort}
            onChange={(model, effort) => attempt(() => api.updateProject(project.id, { model, effort }),
              'Could not update the project')} />
          <span className="basis-full">For new tasks here. Each task can change its own.</span>
        </div>
      )}
      <label className="mt-2 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
        Approvals
        <select
          className={field}
          value={project.approval_mode ?? ''}
          onChange={(event) => attempt(() => api.updateProject(project.id, { approval_mode: (event.target.value || null) as ApprovalMode | null }),
            'Could not update the project')}
        >
          {MODES.map((m) => (
            <option key={m.value} value={m.value} disabled={unsupported(m.value)}>
              {m.value === '' ? `Default (${MODES.find((x) => x.value === defaultMode)?.label})` : m.label}
              {unsupported(m.value) ? ` (not supported by ${project.agent})` : ''}
            </option>
          ))}
        </select>
        <span className="basis-full">{MODE_HELP[mode]}</span>
        {unsupported(project.approval_mode ?? '') && (
          <span className="basis-full text-warn">{project.agent} doesn't support this mode, so its tasks won't start.</span>
        )}
      </label>
      <label className="mt-2 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
        Access
        <select
          className={field}
          value={project.access ?? ''}
          onChange={(event) => attempt(() => api.updateProject(project.id, { access: (event.target.value || null) as Access | null }),
            'Could not update the project')}
        >
          {ACCESS.map((a) => (
            <option key={a.value} value={a.value} disabled={noAccess(a.value)}>
              {a.value === '' ? `Default (${ACCESS.find((x) => x.value === defaultAccess)?.label})` : a.label}
              {noAccess(a.value) ? ` (not supported by ${project.agent})` : ''}
            </option>
          ))}
        </select>
        <span className="basis-full">{ACCESS_HELP[access]}</span>
        {access === 'full' && (mode === 'auto' || mode === 'never') && (
          <span className="basis-full text-warn">Nothing is checked: the agent runs any command and edits any file without asking.</span>
        )}
      </label>
      {(capabilities?.project_config || project.project_config) && (
        <label className="mt-2 flex items-start gap-2 text-xs text-muted-foreground">
          <input type="checkbox" className="mt-0.5" checked={project.project_config}
            onChange={(event) => attempt(() => api.updateProject(project.id, { project_config: event.target.checked }),
              'Could not update the project')} />
          <span>
            Load this project's own {agents?.[project.agent]?.title ?? project.agent} settings.
            Its hooks and MCP servers then run without asking, so turn this on only for projects you trust.
          </span>
        </label>
      )}
    </div>
  )
}

function AgentSelect({ agents, value, onChange, label = 'Agent' }: {
  agents: Record<string, AgentStatus> | null
  value: string
  onChange: (agent: string) => void
  label?: string
}) {
  const names = agents ? Object.keys(agents) : [value]
  return (
    <select className={field} value={value}
      disabled={!agents} onChange={(event) => onChange(event.target.value)} aria-label={label}>
      {names.map((name) => {
        const status = agents?.[name]
        const ready = status?.installed && status.logged_in
        return <option key={name} value={name}>{status?.title ?? name}{status && !ready ? ' (not set up)' : ''}</option>
      })}
    </select>
  )
}

const input = 'h-10 min-w-0 rounded-full bg-card px-4'

function AddProject({ agents }: { agents: Record<string, AgentStatus> | null }) {
  const [path, setPath] = useState('')
  const [name, setName] = useState('')
  const [chosen, setChosen] = useState<string | null>(null)
  const [browsing, setBrowsing] = useState(false)
  // Until one is chosen: the first agent that is set up, else the first.
  const names = agents ? Object.keys(agents) : []
  const agent = chosen ?? names.find((n) => agents?.[n].installed && agents[n].logged_in) ?? names[0]
  const add = async (event: React.FormEvent) => {
    event.preventDefault()
    const ok = await attempt(() => api.addProject({
      path: path.trim(), name: name.trim() || undefined, agent,
    }), 'Could not add the project')
    if (ok) {
      setPath(''); setName('')
    }
  }
  return (
    <form onSubmit={add} className="grid min-w-0 gap-2 sm:grid-cols-[2fr_1fr_auto]">
      <div className="flex min-w-0 gap-2">
        <Input className={input} placeholder="/home/you/code/project" value={path} onChange={(e) => setPath(e.target.value)} aria-label="Project directory" />
        <Button type="button" variant="outline" className="h-10 shrink-0 rounded-full px-4" aria-expanded={browsing}
          onClick={() => setBrowsing(!browsing)}>Browse</Button>
      </div>
      {browsing && (
        <div className="min-w-0 sm:order-1 sm:col-span-3">
          <FolderBrowser start={path.trim()} onClose={() => setBrowsing(false)} onPick={(picked, folder) => {
            setPath(picked)
            if (!name.trim()) setName(folder)
            setBrowsing(false)
          }} />
        </div>
      )}
      <Input className={input} placeholder="Name (optional)" value={name} onChange={(e) => setName(e.target.value)} aria-label="Project name" />
      <Button type="submit" className="h-10 rounded-full px-5" disabled={!path.trim() || !agent}>Add</Button>
      <label className="flex items-center gap-2 text-xs text-muted-foreground sm:order-2 sm:col-span-3">
        Agent
        <AgentSelect agents={agents} value={agent ?? ''} onChange={setChosen} label="Agent for the new project" />
      </label>
    </form>
  )
}
