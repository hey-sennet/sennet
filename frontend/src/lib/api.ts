// Types mirror src/agent_talk/db.py rows and server.py payloads.

export type TaskStatus = 'queued' | 'running' | 'awaiting_approval' | 'completed' | 'failed' | 'cancelled' | 'unknown'
export type Decision = 'approve' | 'approve_always' | 'decline' | 'cancel'
export type ApprovalMode = 'ask' | 'auto' | 'never'

export interface Project {
  id: number
  name: string
  path: string
  aliases: string[]
  agent: string
  approval_mode: ApprovalMode | null
  sandbox: string | null
}

export interface Task {
  id: number
  project_id: number
  agent: string
  title: string
  status: TaskStatus
  thread_id: string | null
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

export interface Approval {
  id: number
  task_id: number
  kind: 'command' | 'edit' | 'permissions'
  risk: 'read' | 'edit' | 'execute' | 'network' | 'destructive'
  summary: string
  detail: Record<string, any>
  status: 'pending' | 'approved' | 'declined' | 'cancelled'
  decided_by: string | null
}

export interface Rule {
  id: number
  project_id: number
  kind: 'command' | 'edit'
  pattern: string
}

export interface State {
  projects: Project[]
  tasks: Task[]
  approvals: Approval[]
  rules: Rule[]
  settings: { approvals: { mode: ApprovalMode; sandbox: string }; tasks: { max_concurrent: number } }
}

export interface AgentStatus {
  installed: boolean | null
  logged_in: boolean | null
  detail: string
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
