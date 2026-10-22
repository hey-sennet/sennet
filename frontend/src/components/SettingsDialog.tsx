import { useEffect, useState } from 'react'
import { Trash2, X } from 'lucide-react'
import { toast } from 'sonner'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { api, type AgentStatus, type ApprovalMode, type Project, type Rule, type State } from '@/lib/api'

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

export function SettingsDialog({ open, onOpenChange, projects, rules, settings }: {
  open: boolean
  onOpenChange: (open: boolean) => void
  projects: Project[]
  rules: Rule[]
  settings: State['settings'] | null
}) {
  const [agents, setAgents] = useState<Record<string, AgentStatus> | null>(null)

  useEffect(() => {
    if (open) api.agents().then(setAgents).catch(() => setAgents(null))
  }, [open])

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[90dvh] overflow-y-auto sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle>Settings</DialogTitle>
          <DialogDescription>Projects, approvals and agents on this computer.</DialogDescription>
        </DialogHeader>

        <Section title="Agents">
          {!agents && <p className="text-sm text-muted-foreground">Checking…</p>}
          {agents && Object.entries(agents).map(([name, status]) => (
            <div key={name} className="flex items-baseline justify-between gap-4 text-sm">
              <span className="font-medium capitalize">{name}</span>
              <span className={status.installed && status.logged_in ? 'text-foreground' : 'text-warn'}>{status.detail}</span>
            </div>
          ))}
        </Section>

        <Section title="Projects" hint="The spoken name and aliases are how you'll pick a project by voice.">
          <div className="flex flex-col gap-2">
            {projects.map((project) => (
              <ProjectRow key={project.id} project={project} defaultMode={settings?.approvals.mode ?? 'ask'}
                supported={agents?.[project.agent]?.capabilities.approval_modes} />
            ))}
            {!projects.length && <p className="text-sm text-muted-foreground">No projects yet.</p>}
          </div>
          <AddProject />
        </Section>

        <Section title="Remembered approvals" hint={'Created when you choose "Always". Destructive actions are always asked.'}>
          {!rules.length && <p className="text-sm text-muted-foreground">None yet.</p>}
          {rules.map((rule) => (
            <div key={rule.id} className="flex items-center justify-between gap-3 text-sm">
              <span>
                <span className="text-muted-foreground">{projects.find((p) => p.id === rule.project_id)?.name}: </span>
                {rule.kind === 'edit' ? 'all file edits' : <code className="font-mono text-xs">{rule.pattern} …</code>}
              </span>
              <Button variant="ghost" size="icon-sm" aria-label="Forget this approval"
                onClick={() => attempt(() => api.removeRule(rule.id), 'Could not remove the rule')}>
                <X />
              </Button>
            </div>
          ))}
        </Section>
      </DialogContent>
    </Dialog>
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

function ProjectRow({ project, defaultMode, supported }: {
  project: Project
  defaultMode: ApprovalMode
  supported?: ApprovalMode[]  // what the project's agent can do; unknown until the agents load
}) {
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
    </div>
  )
}

function AddProject() {
  const [path, setPath] = useState('')
  const [name, setName] = useState('')
  const [aliases, setAliases] = useState('')
  const add = async (event: React.FormEvent) => {
    event.preventDefault()
    const ok = await attempt(() => api.addProject({
      path: path.trim(), name: name.trim() || undefined,
      aliases: aliases.split(',').map((a) => a.trim()).filter(Boolean),
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
      <Button type="submit" disabled={!path.trim()}>Add</Button>
    </form>
  )
}
