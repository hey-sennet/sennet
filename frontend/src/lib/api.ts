// Types mirror src/agent_talk/db.py rows and server.py payloads. Statuses, kinds and
// modes are the agent contract's own words (src/agent_talk/agents/base.py), never a provider's.

// background: the turn is over, but work the agent started goes on (it may report it in a turn of its own).
export type TaskStatus = 'queued' | 'running' | 'awaiting_approval' | 'background' | 'completed' | 'failed' | 'cancelled' | 'unknown'
export type Decision = 'approve' | 'approve_always' | 'decline' | 'cancel'
export type ApprovalMode = 'ask' | 'auto' | 'never'
export type Access = 'read-only' | 'workspace' | 'full'
export type TurnStatus = 'completed' | 'cancelled' | 'failed'
export type ItemStatus = 'running' | 'completed' | 'failed' | 'declined'
export type StepStatus = 'pending' | 'active' | 'done'

export interface Project {
  id: number
  name: string
  path: string
  aliases: string[]
  agent: string
  approval_mode: ApprovalMode | null
  access: Access | null
  project_config: boolean
  model: string | null // the agent's model id for new tasks; null for the agent's default
  effort: string | null
}

export interface Task {
  id: number
  project_id: number
  agent: string
  title: string
  status: TaskStatus
  resume_token: string | null
  model: string | null // overrides the project's; null follows it
  effort: string | null
  error: string | null
  created_at: number
  updated_at: number
}

export interface Message {
  id: number
  task_id: number
  role: 'user' | 'agent'
  text: string
  state: 'queued' | 'sent' | 'done' | 'cancelled'
  created_at: number
}

export interface TaskEvent {
  id: number
  kind: string
  data: Record<string, any>
  created_at: number
}

export interface TaskDetail extends Task {
  messages: Message[]
  events: TaskEvent[]
}

export interface FileChange {
  path: string
  kind: 'add' | 'delete' | 'update' | 'move'
  diff: string
}

// The provider-neutral request (ApprovalRequest in agents/base.py) as stored.
export interface ApprovalDetail {
  command: string | null
  read_only: boolean | null
  paths: string[]
  changes: FileChange[]
  tool: string | null
  tool_input: string | null
  reason: string | null
  network: boolean
  title: string | null
  extra: Record<string, unknown>
}

export interface Approval {
  id: number
  task_id: number
  kind: 'command' | 'edit' | 'tool' | 'access'
  risk: 'read' | 'edit' | 'execute' | 'network' | 'destructive'
  summary: string
  detail: ApprovalDetail
  status: 'pending' | 'approved' | 'declined' | 'cancelled'
  decided_by: string | null
  can_always: boolean
}

export interface Rule {
  id: number
  project_id: number
  kind: 'command' | 'edit' | 'tool'
  pattern: string
}

export interface State {
  version: string // the server's; the client offers a reload when it changes under it
  projects: Project[]
  tasks: Task[]
  approvals: Approval[]
  rules: Rule[]
  settings: { approvals: { mode: ApprovalMode; access: Access }; tasks: { max_concurrent: number } }
}

export interface Capabilities {
  interrupt: boolean
  resume: boolean
  live_approvals: boolean
  approval_modes: ApprovalMode[]
  access_levels: Access[]
  background_turns: boolean
  project_config: boolean
  live_model_change: boolean
}

/** A task's own model and effort; null for either follows the project. */
export interface ModelChoice {
  model: string | null
  effort: string | null
}

export interface ModelOption {
  id: string
  title: string
  efforts: string[]
  default_effort: string | null
  default: boolean
}

export interface AgentStatus {
  title: string
  installed: boolean | null
  logged_in: boolean | null
  detail: string
  capabilities: Capabilities
  models: ModelOption[]
}

// A paired browser (src/agent_talk/access.py).
export interface Device {
  id: number
  name: string
  paired_by: string // "the terminal", or the name of the device that showed the code
  created_at: number
  last_seen_at: number
  current: boolean // the device asking
}

/** A code another device can pair with: a link (and its QR code) or a code to type. */
export interface Pairing {
  link: string
  code: string
  expires_at: number
  qr: string // an SVG data URI
  local_only: boolean // the link only works on this computer: no public address is set
}

export type ServerEvent =
  | { type: 'task'; task: Task }
  | { type: 'message'; task_id: number; message: Message }
  | { type: 'delta'; task_id: number; item_id: string; delta: string }
  | { type: 'event'; task_id: number; event: TaskEvent }
  | { type: 'approval'; approval: Approval }
  | { type: 'project'; project: Project }
  | { type: 'project_removed'; project_id: number }
  | { type: 'rules'; rules: Rule[] }
  | { type: 'devices'; paired: Omit<Device, 'current'> | null }

export class ApiError extends Error {}

// This browser isn't paired, or was unpaired: the app shows the pairing screen (lib/pairing.ts).
let onUnpaired: () => void = () => {}
export function whenUnpaired(handler: () => void) {
  onUnpaired = handler
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const response = await fetch(path, {
    method,
    headers: { 'Content-Type': 'application/json', 'X-Agent-Talk': '1' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  if (response.status === 401 && path !== '/api/pair') onUnpaired()
  if (!response.ok) {
    let detail = response.statusText
    try {
      detail = (await response.json()).detail ?? detail
    } catch {
      // not JSON
    }
    throw new ApiError(typeof detail === 'string' ? detail : 'Request failed')
  }
  return response.status === 204 ? (undefined as T) : response.json()
}

export const api = {
  state: () => request<State>('GET', '/api/state'),
  agents: () => request<Record<string, AgentStatus>>('GET', '/api/agents'),
  task: (id: number) => request<TaskDetail>('GET', `/api/tasks/${id}`),
  createTask: (project_id: number, text: string, choice?: ModelChoice) =>
    request<Task>('POST', '/api/tasks', { project_id, text, ...choice }),
  followUp: (id: number, text: string) => request<Task>('POST', `/api/tasks/${id}/messages`, { text }),
  cancel: (id: number) => request<Task>('POST', `/api/tasks/${id}/cancel`),
  decide: (id: number, decision: Decision) => request<Approval>('POST', `/api/approvals/${id}`, { decision }),
  addProject: (body: { path: string; name?: string; aliases: string[]; agent?: string }) =>
    request<Project>('POST', '/api/projects', body),
  chooseModel: (id: number, model: string | null, effort: string | null) =>
    request<Task>('PATCH', `/api/tasks/${id}`, { model, effort }),
  updateProject: (id: number, body: Partial<Pick<Project, 'name' | 'aliases' | 'agent' | 'approval_mode' | 'project_config' | 'model' | 'effort'>>) =>
    request<Project>('PATCH', `/api/projects/${id}`, body),
  removeProject: (id: number) => request<void>('DELETE', `/api/projects/${id}`),
  removeRule: (id: number) => request<void>('DELETE', `/api/rules/${id}`),
  pair: (code: string) => request<Device>('POST', '/api/pair', { code }),
  devices: () => request<Device[]>('GET', '/api/devices'),
  newPairing: () => request<Pairing>('POST', '/api/devices/pairing'),
  renameDevice: (id: number, name: string) => request<Device>('PATCH', `/api/devices/${id}`, { name }),
  revokeDevice: (id: number) => request<void>('DELETE', `/api/devices/${id}`),
}
