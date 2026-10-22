// Types mirror src/agent_talk/db.py rows and server.py payloads. Statuses, kinds and
// modes are the agent contract's own words (src/agent_talk/agents/base.py), never a provider's.

export type TaskStatus = 'queued' | 'running' | 'awaiting_approval' | 'completed' | 'failed' | 'cancelled' | 'unknown'
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
}

export interface Task {
  id: number
  project_id: number
  agent: string
  title: string
  status: TaskStatus
  resume_token: string | null
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
}

export interface AgentStatus {
  installed: boolean | null
  logged_in: boolean | null
  detail: string
  capabilities: Capabilities
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

export class ApiError extends Error {}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const response = await fetch(path, {
    method,
    headers: { 'Content-Type': 'application/json', 'X-Agent-Talk': '1' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
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
  createTask: (project_id: number, text: string) => request<Task>('POST', '/api/tasks', { project_id, text }),
  followUp: (id: number, text: string) => request<Task>('POST', `/api/tasks/${id}/messages`, { text }),
  cancel: (id: number) => request<Task>('POST', `/api/tasks/${id}/cancel`),
  decide: (id: number, decision: Decision) => request<Approval>('POST', `/api/approvals/${id}`, { decision }),
  addProject: (body: { path: string; name?: string; aliases: string[] }) => request<Project>('POST', '/api/projects', body),
  updateProject: (id: number, body: Partial<Pick<Project, 'name' | 'aliases' | 'approval_mode'>>) =>
    request<Project>('PATCH', `/api/projects/${id}`, body),
  removeProject: (id: number) => request<void>('DELETE', `/api/projects/${id}`),
  removeRule: (id: number) => request<void>('DELETE', `/api/rules/${id}`),
}
