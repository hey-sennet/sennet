import { useEffect, useState } from 'react'
import { Trash2, X } from 'lucide-react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { refreshAgents, useAgents } from '@/lib/agents'
import { api, type AgentStatus, type ApprovalMode, type Project, type Rule, type ServerEvent, type State } from '@/lib/api'
import { Devices } from './Devices'
import { ModelPicker } from './ModelPicker'

const MODES: { value: ApprovalMode | ''; label: string }[] = [
  { value: '', label: 'Default' },
  { value: 'ask', label: 'Ask first' },
  { value: 'auto', label: 'Auto within its access' },
  { value: 'never', label: 'Never ask' },
]

const MODE_HELP: Record<ApprovalMode, string> = {
  ask: 'The agent asks before running anything outside its trusted set.',
  auto: 'The agent works freely within its access level and asks only to go beyond it.',
  never: 'Nothing is asked. Actions that need more access fail instead.',
}

async function attempt(action: () => Promise<unknown>, failure: string) {
  try {
    await action()
    return true
  } catch (error) {
    toast.error(error instanceof Error ? error.message : failure)
    return false
  }
}

export function SettingsDialog({ open, onOpenChange, projects, rules, settings, version, subscribe }: {
  open: boolean
  onOpenChange: (open: boolean) => void
  projects: Project[]
  rules: Rule[]
  settings: State['settings'] | null
  version: string | null
  subscribe: (listener: (event: ServerEvent) => void) => () => void
}) {
  const agents = useAgents()

  useEffect(() => {
    if (open) void refreshAgents()
  }, [open])

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90dvh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>Settings</DialogTitle>
          <DialogDescription>Projects, approvals, agents and paired devices on this computer.</DialogDescription>
        </DialogHeader>

        <Section title="Agents">
          {!agents && <p className="text-sm text-muted-foreground">Checking…</p>}
          {agents && Object.entries(agents).map(([name, status]) => (
            <div key={name} className="flex items-baseline justify-between gap-4 text-sm">
              <span className="font-medium">{status.title}</span>
              <span className={status.installed && status.logged_in ? 'text-foreground' : 'text-warn'}>{status.detail}</span>
            </div>
          ))}
        </Section>

        <Section title="Projects" hint="The spoken name and aliases are how you'll pick a project by voice.">
          <div className="flex flex-col gap-2">
            {projects.map((project) => (
              <ProjectRow key={project.id} project={project} defaultMode={settings?.approvals.mode ?? 'ask'} agents={agents} />
            ))}
            {!projects.length && <p className="text-sm text-muted-foreground">No projects yet.</p>}
          </div>
          <AddProject agents={agents} />
        </Section>

        <Section title="Remembered approvals" hint={'Created when you choose "Always". Destructive actions are always asked.'}>
          {!rules.length && <p className="text-sm text-muted-foreground">None yet.</p>}
          {rules.map((rule) => (
            <div key={rule.id} className="flex items-center justify-between gap-3 text-sm">
              <span>
                <span className="text-muted-foreground">{projects.find((p) => p.id === rule.project_id)?.name}: </span>
                {rule.kind === 'edit' ? 'all file edits'
                  : rule.kind === 'tool' ? <>the <code className="font-mono text-xs">{rule.pattern}</code> tool</>
                  : <code className="font-mono text-xs">{rule.pattern} …</code>}
              </span>
              <Button variant="ghost" size="icon-sm" aria-label="Forget this approval"
                onClick={() => attempt(() => api.removeRule(rule.id), 'Could not remove the rule')}>
                <X />
              </Button>
            </div>
          ))}
        </Section>

        <Section title="Devices" hint="Browsers paired with Sennet. Unpair any you no longer use.">
          <Devices subscribe={subscribe} />
        </Section>

        <About version={version} />
      </DialogContent>
    </Dialog>
  )
}

function About({ version }: { version: string | null }) {
  const link = 'underline underline-offset-2 hover:text-foreground'
  return (
    <Section title="About">
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

function Section({ title, hint, children }: { title: string; hint?: string; children: React.ReactNode }) {
  return (
    <section className="flex flex-col gap-3 border-t pt-4">
      <div>
        <h3 className="text-sm font-medium">{title}</h3>
        {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
      </div>
      {children}
    </section>
  )
}

function ProjectRow({ project, defaultMode, agents }: {
  project: Project
  defaultMode: ApprovalMode
  agents: Record<string, AgentStatus> | null  // unknown until they load
}) {
  const capabilities = agents?.[project.agent]?.capabilities
  const supported = capabilities?.approval_modes
  const mode = project.approval_mode ?? defaultMode
  const unsupported = (value: ApprovalMode | '') => !!supported && !supported.includes(value || defaultMode)
  return (
    <div className="rounded-lg border px-3 py-2.5">
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <p className="text-sm font-medium">
            {project.name}
            {project.aliases.length > 0 && <span className="font-normal text-muted-foreground"> · also {project.aliases.join(', ')}</span>}
          </p>
          <p className="truncate font-mono text-xs text-muted-foreground">{project.path}</p>
        </div>
        <Button variant="ghost" size="icon-sm" aria-label={`Remove ${project.name}`}
          onClick={() => confirm(`Remove ${project.name}? Its files are not touched.`)
            && attempt(() => api.removeProject(project.id), 'Could not remove the project')}>
          <Trash2 />
        </Button>
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
          className="rounded-md border bg-background px-2 py-1 text-xs text-foreground"
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
    <select className="rounded-md border bg-background px-2 py-1 text-xs text-foreground" value={value}
      disabled={!agents} onChange={(event) => onChange(event.target.value)} aria-label={label}>
      {names.map((name) => {
        const status = agents?.[name]
        const ready = status?.installed && status.logged_in
        return <option key={name} value={name}>{status?.title ?? name}{status && !ready ? ' (not set up)' : ''}</option>
      })}
    </select>
  )
}

function AddProject({ agents }: { agents: Record<string, AgentStatus> | null }) {
  const [path, setPath] = useState('')
  const [name, setName] = useState('')
  const [aliases, setAliases] = useState('')
  const [chosen, setChosen] = useState<string | null>(null)
  // Until one is chosen: the first agent that is set up, else the first.
  const names = agents ? Object.keys(agents) : []
  const agent = chosen ?? names.find((n) => agents?.[n].installed && agents[n].logged_in) ?? names[0]
  const add = async (event: React.FormEvent) => {
    event.preventDefault()
    const ok = await attempt(() => api.addProject({
      path: path.trim(), name: name.trim() || undefined,
      aliases: aliases.split(',').map((a) => a.trim()).filter(Boolean), agent,
    }), 'Could not add the project')
    if (ok) {
      setPath(''); setName(''); setAliases('')
    }
  }
  return (
    <form onSubmit={add} className="grid gap-2 sm:grid-cols-[2fr_1fr_1fr_auto]">
      <Input placeholder="/home/you/code/project" value={path} onChange={(e) => setPath(e.target.value)} aria-label="Project directory" />
      <Input placeholder="Name (optional)" value={name} onChange={(e) => setName(e.target.value)} aria-label="Spoken name" />
      <Input placeholder="Aliases, comma separated" value={aliases} onChange={(e) => setAliases(e.target.value)} aria-label="Aliases" />
      <Button type="submit" disabled={!path.trim() || !agent}>Add</Button>
      <label className="flex items-center gap-2 text-xs text-muted-foreground sm:col-span-4">
        Agent
        <AgentSelect agents={agents} value={agent ?? ''} onChange={setChosen} label="Agent for the new project" />
      </label>
    </form>
  )
}
