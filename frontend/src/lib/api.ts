// Types mirror src/sennet/db.py rows and server.py payloads. Statuses, kinds and
// modes are the agent contract's own words (src/sennet/agents/base.py), never a provider's.

// background: the turn is over, but work the agent started goes on (it may report it in a turn of its own).
export type TaskStatus = 'queued' | 'running' | 'awaiting_approval' | 'background' | 'completed' | 'failed' | 'cancelled' | 'unknown'
export type Decision = 'approve' | 'approve_always' | 'decline' | 'cancel'
export type ApprovalMode = 'ask' | 'auto' | 'never' | 'classifier'
export type Access = 'read-only' | 'workspace' | 'full'
export type TurnStatus = 'completed' | 'cancelled' | 'failed'
export type ItemStatus = 'running' | 'completed' | 'failed' | 'declined'
export type StepStatus = 'pending' | 'active' | 'done'

export interface Project {
  id: number
  name: string
  path: string
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
  input_tokens: number // summed over its turns; input includes the cached part
  cached_tokens: number
  output_tokens: number
  created_at: number
  updated_at: number
}

/** An image sent with a message (src/sennet/attachments.py); `attachmentUrl` shows it. */
export interface Attachment {
  id: number
  media_type: 'image/jpeg' | 'image/png' | 'image/webp'
  width: number
  height: number
  thumbnail: 'image/jpeg' | 'image/png' | 'image/webp' | null // a small copy for the timeline, if the device sent one
}

export interface Message {
  id: number
  task_id: number
  role: 'user' | 'agent'
  text: string // may be empty when images went with it
  state: 'queued' | 'sent' | 'done' | 'cancelled'
  attachments: Attachment[]
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

/** One of an account's usage limits (LimitWindow in agents/base.py). */
export interface LimitWindow {
  id: string // "session" (five hours), "weekly", ...
  title: string // "5-hour", "weekly"
  used: number // percent, 0 to 100
  resets_at: number | null // Unix seconds
}

/** An agent's limits as last reported or asked for (usage.py). */
export interface AgentLimits {
  windows: LimitWindow[]
  checked_at: number
}

/** A task found by search, with the text it matched in and the spans of it to highlight. */
export interface SearchHit {
  task: Task
  snippet: string
  highlights: [number, number][]
}

export interface State {
  version: string // the server's; the client offers a reload when it changes under it
  projects: Project[]
  tasks: Task[] // every unfinished task, and the latest finished ones
  more_tasks: boolean // older finished tasks to page through (api.earlierTasks)
  approvals: Approval[]
  rules: Rule[]
  limits: Record<string, AgentLimits> // by agent; an agent that can't say isn't listed
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
  images: boolean // takes images with a message
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

// A paired browser (src/sennet/access.py).
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

/** What notifies a device (src/sennet/push.py). */
export type PushKind = 'approval' | 'finished' | 'failed'

/** The server's key to subscribe with, and this device's subscription, if it has one. */
export interface PushState {
  key: string // base64url, the push subscription's applicationServerKey
  subscription: { endpoint: string; kinds: PushKind[] } | null
}

export interface Folder {
  name: string
  path: string
  repo: boolean // a git repository
}

export interface Folders {
  path: string
  parent: string | null
  home: string
  repo: boolean
  directories: Folder[]
  more: number // folders past the listing's limit
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
  | { type: 'limits'; agent: string; limits: AgentLimits | null }
  | { type: 'task_removed'; task_id: number }

export class ApiError extends Error {
  readonly status: number
  constructor(message: string, status: number) {
    super(message)
    this.status = status
  }
}

// This browser isn't paired, or was unpaired: the app shows the pairing screen (lib/pairing.ts).
let onUnpaired: () => void = () => {}
export function whenUnpaired(handler: () => void) {
  onUnpaired = handler
}

/** Where a message's image is shown from; only a paired device's cookie gets it. */
export const attachmentUrl = (id: number) => `/api/attachments/${id}`
/** Where its thumbnail is shown from, or the image itself without one. */
export const thumbnailUrl = (image: Attachment) => image.thumbnail ? `/api/attachments/${image.id}/thumbnail` : attachmentUrl(image.id)

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  // A Blob (an image) goes as it is; anything else as JSON.
  const blob = body instanceof Blob
  const response = await fetch(path, {
    method,
    headers: { 'Content-Type': blob ? body.type || 'application/octet-stream' : 'application/json', 'X-Sennet': '1' },
    body: body === undefined ? undefined : blob ? body : JSON.stringify(body),
  })
  if (response.status === 401 && path !== '/api/pair') onUnpaired()
  if (!response.ok) {
    let detail = response.statusText
    try {
      detail = (await response.json()).detail ?? detail
    } catch {
      // not JSON
    }
    throw new ApiError(typeof detail === 'string' ? detail : 'Request failed', response.status)
  }
  return response.status === 204 ? (undefined as T) : response.json()
}

export const api = {
  state: () => request<State>('GET', '/api/state'),
  agents: () => request<Record<string, AgentStatus>>('GET', '/api/agents'),
  /** The limits as known; the server asks the agents again for any a few minutes old, and says so in `limits` events. */
  limits: () => request<Record<string, AgentLimits>>('GET', '/api/limits'),
  task: (id: number) => request<TaskDetail>('GET', `/api/tasks/${id}`),
  /** Finished tasks older than `last`, newest first. */
  earlierTasks: (last: Task, limit = 20) =>
    request<{ tasks: Task[]; more: boolean }>('GET', `/api/tasks?before=${last.updated_at}&before_id=${last.id}&limit=${limit}`),
  deleteTask: (id: number) => request<void>('DELETE', `/api/tasks/${id}`),
  search: (query: string) => request<SearchHit[]>('GET', `/api/search?q=${encodeURIComponent(query)}`),
  /** `attachments`: ids of images uploaded for it (`uploadImage`). */
  createTask: (project_id: number, text: string, choice?: ModelChoice, attachments: number[] = []) =>
    request<Task>('POST', '/api/tasks', { project_id, text, ...choice, attachments }),
  followUp: (id: number, text: string, attachments: number[] = []) =>
    request<Task>('POST', `/api/tasks/${id}/messages`, { text, attachments }),
  /** An image for a message to come; its id goes in the message, typed or spoken. */
  uploadImage: (image: Blob) => request<Attachment>('POST', '/api/attachments', image),
  /** Its small copy, for the timeline; before the image is sent. */
  uploadThumbnail: (id: number, thumbnail: Blob) => request<Attachment>('PUT', `/api/attachments/${id}/thumbnail`, thumbnail),
  /** An image taken out before its message was sent. */
  removeAttachment: (id: number) => request<void>('DELETE', `/api/attachments/${id}`),
  cancel: (id: number) => request<Task>('POST', `/api/tasks/${id}/cancel`),
  /** A message is likely coming: the server starts the agent early. */
  prewarm: (target: { task_id?: number; project_id?: number } & Partial<ModelChoice>) =>
    request<void>('POST', '/api/prewarm', target),
  decide: (id: number, decision: Decision) => request<Approval>('POST', `/api/approvals/${id}`, { decision }),
  addProject: (body: { path: string; name?: string; agent?: string }) =>
    request<Project>('POST', '/api/projects', body),
  chooseModel: (id: number, model: string | null, effort: string | null) =>
    request<Task>('PATCH', `/api/tasks/${id}`, { model, effort }),
  updateProject: (id: number, body: Partial<Pick<Project, 'name' | 'agent' | 'approval_mode' | 'access' | 'project_config' | 'model' | 'effort'>>) =>
    request<Project>('PATCH', `/api/projects/${id}`, body),
  removeProject: (id: number) => request<void>('DELETE', `/api/projects/${id}`),
  directories: (path?: string) =>
    request<Folders>('GET', `/api/directories${path ? `?path=${encodeURIComponent(path)}` : ''}`),
  removeRule: (id: number) => request<void>('DELETE', `/api/rules/${id}`),
  pair: (code: string) => request<Device>('POST', '/api/pair', { code }),
  devices: () => request<Device[]>('GET', '/api/devices'),
  newPairing: () => request<Pairing>('POST', '/api/devices/pairing'),
  renameDevice: (id: number, name: string) => request<Device>('PATCH', `/api/devices/${id}`, { name }),
  revokeDevice: (id: number) => request<void>('DELETE', `/api/devices/${id}`),
  push: () => request<PushState>('GET', '/api/push'),
  /** A browser's PushSubscription (its toJSON), and what notifies; without kinds, the device keeps its choice. */
  subscribePush: (subscription: PushSubscriptionJSON, kinds?: PushKind[]) =>
    request<{ endpoint: string; kinds: PushKind[] }>('PUT', '/api/push', { ...subscription, kinds }),
  unsubscribePush: () => request<void>('DELETE', '/api/push'),
  testPush: () => request<void>('POST', '/api/push/test'),
}
