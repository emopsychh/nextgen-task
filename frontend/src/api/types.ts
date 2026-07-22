export type PortalRole = "agency" | "client" | "unknown";

export type Portal = {
  id: number;
  member_id: string;
  domain: string;
  role: PortalRole;
  name: string;
  is_active: boolean;
};

export type BitrixUser = {
  id: number;
  bitrix_id: string;
  display_name: string;
  name: string;
  last_name: string;
  email: string;
  avatar_url: string;
  is_admin: boolean;
};

export type Project = {
  id: number;
  portal: number;
  portal_name: string;
  name: string;
  description: string;
  is_active: boolean;
  bitrix_task_id?: string;
  bitrix_group_id?: string;
  tasks_count: number;
  done_count: number;
};

export type ActivityType =
  | "project_created"
  | "task_created"
  | "task_updated"
  | "comment"
  | "attachment";

export type ActivityEvent = {
  id: string;
  type: ActivityType;
  title: string;
  subtitle: string | null;
  project_name: string | null;
  task_title: string | null;
  at: string;
  project_id: number | null;
  task_id: number | null;
};

export type TaskStatus = "todo" | "in_progress" | "done";
export type SyncStatus = "pending" | "synced" | "error" | "skipped";

export type Comment = {
  id: number;
  task: number;
  author: number | null;
  author_name: string;
  author_display: string;
  text: string;
  is_system?: boolean;
  attachments?: Attachment[];
  created_at: string;
};

export type Attachment = {
  id: number;
  task: number | null;
  comment: number | null;
  url: string | null;
  original_name: string;
  created_at: string;
};

export type Task = {
  id: number;
  project: number;
  project_name: string;
  portal_id: number;
  title: string;
  description: string;
  due_date: string | null;
  status: TaskStatus;
  bitrix_task_id: string;
  agency_bitrix_task_id?: string;
  sync_status: SyncStatus;
  sync_error?: string;
  created_by?: number | null;
  created_by_name?: string | null;
  comments_count?: number;
  comments?: Comment[];
  attachments?: Attachment[];
  total_tracked_seconds?: number;
  active_timer?: TimeEntry | null;
  deal_paid_hours?: number | null;
  deal_remaining_hours?: number | null;
  created_at: string;
  updated_at: string;
};

export type TimeEntry = {
  id: number;
  task: number;
  author: number | null;
  author_name: string | null;
  started_at: string;
  ended_at: string | null;
  duration_seconds: number;
  note: string;
  is_running: boolean;
  created_at: string;
  updated_at: string;
};

export type DealBinding = {
  id: number;
  agency_portal: number;
  client_portal: Portal;
  deal_id: string;
  deal_title: string;
  category_id: string;
  paid_hours: string | number | null;
  remaining_hours: string | number | null;
  bitrix_company_id?: string;
  is_active: boolean;
  created_at: string;
  updated_at: string;
};

export type AuthSession = {
  access: string;
  refresh: string;
  portal: Portal;
  user: BitrixUser;
};

const API_BASE = import.meta.env.VITE_API_URL?.replace(/\/$/, "") || "";

async function parseError(res: Response): Promise<string> {
  try {
    const data = await res.json();
    if (typeof data.detail === "string") return data.detail;
    return JSON.stringify(data);
  } catch {
    return res.statusText || "Request failed";
  }
}

export async function api<T>(
  path: string,
  options: RequestInit = {},
  token?: string | null
): Promise<T> {
  const headers = new Headers(options.headers || {});
  if (token) headers.set("Authorization", `Bearer ${token}`);
  if (!(options.body instanceof FormData) && !headers.has("Content-Type") && options.body) {
    headers.set("Content-Type", "application/json");
  }

  const res = await fetch(`${API_BASE}${path}`, { ...options, headers });
  if (!res.ok) {
    throw new Error(await parseError(res));
  }
  if (res.status === 204) return undefined as T;
  return res.json();
}

export function unwrapList<T>(data: T[] | { results: T[] }): T[] {
  if (Array.isArray(data)) return data;
  return data.results || [];
}
