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
  outcome?: string;
  due_date: string | null;
  status: TaskStatus;
  is_important?: boolean;
  bitrix_task_id: string;
  agency_bitrix_task_id?: string;
  sync_status: SyncStatus;
  sync_error?: string;
  created_by?: number | null;
  created_by_name?: string | null;
  created_by_role?: "agency" | "client" | "unknown" | null;
  // Lightweight activity signals — the full chat thread is loaded lazily via
  // GET /api/tasks/{id}/thread/ (see ThreadPage), never inlined on the task.
  comments_count?: number;
  last_comment_id?: number;
  files_count?: number;
  last_file_id?: number;
  total_tracked_seconds?: number;
  active_timer?: TimeEntry | null;
  deal_paid_hours?: number | null;
  deal_remaining_hours?: number | null;
  created_at: string;
  updated_at: string;
};

export type ThreadItem =
  | { kind: "comment"; at: string; comment: Comment }
  | { kind: "file"; at: string; file: Attachment };

export type ThreadPage = {
  items: ThreadItem[];
  has_more: boolean;
};

export type Paginated<T> = {
  count: number;
  next: string | null;
  previous: string | null;
  results: T[];
};

export type TaskCounts = {
  all: number;
  todo: number;
  in_progress: number;
  done: number;
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

export type WorkReportStatus =
  | "draft"
  | "pending_client"
  | "disputed"
  | "accepted"
  | "paid";

export type WorkReportTaskRow = {
  id: number;
  title: string;
  status: TaskStatus;
  tracked_seconds: number;
  outcome?: string;
  disputed?: boolean;
};

export type WorkReportProjectBlock = {
  id: number;
  name: string;
  total_tracked_seconds: number;
  tasks: WorkReportTaskRow[];
};

export type WorkReportEvent = {
  id: number;
  kind: "created" | "sent" | "accepted" | "disputed" | "paid" | "reopened";
  actor: number | null;
  actor_name: string;
  payload: Record<string, unknown>;
  created_at: string;
};

export type WorkReportDisputeItem = {
  id: number;
  task: number;
  task_title: string;
  note: string;
  created_at: string;
};

export type WorkReportDealHours = {
  deal_id: string;
  deal_title: string;
  paid_hours: number | null;
  remaining_hours: number | null;
} | null;

export type WorkReport = {
  id: number;
  portal_id: number;
  portal_name?: string;
  project?: number | null;
  project_ids?: number[];
  project_names?: string[];
  projects_count?: number;
  status: WorkReportStatus;
  created_by: number | null;
  created_by_name: string;
  client_comment: string;
  sent_at: string | null;
  accepted_at: string | null;
  paid_at: string | null;
  created_at: string;
  updated_at: string;
  is_active: boolean;
  projects_detail?: WorkReportProjectBlock[];
  total_tracked_seconds: number;
  deal_hours?: WorkReportDealHours;
  events?: WorkReportEvent[];
  dispute_items?: WorkReportDisputeItem[];
  dispute_count?: number;
};

export type SupportTicketStatus = "open" | "closed";

export type SupportTicketMessage = {
  id: number;
  ticket: number;
  author: number | null;
  author_name: string;
  text: string;
  created_at: string;
};

export type SupportTicket = {
  id: number;
  portal: number;
  subject: string;
  body?: string;
  status: SupportTicketStatus;
  project: number | null;
  project_name?: string;
  task: number | null;
  task_title?: string;
  created_by: number | null;
  created_by_name: string;
  message_count?: number;
  messages?: SupportTicketMessage[];
  created_at: string;
  updated_at: string;
  closed_at: string | null;
};

export type DealBinding = {
  id: number;
  agency_portal: number;
  client_portal: Portal;
  deal_id: string;
  deal_title: string;
  category_id: string;
  stage_id?: string;
  stage_semantic?: string;
  is_won?: boolean;
  paid_hours: string | number | null;
  remaining_hours: string | number | null;
  hours_credit?: string | number | null;
  hours_credit_source_deal_id?: string;
  hours_credit_source_title?: string;
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

export const API_BASE = import.meta.env.VITE_API_URL?.replace(/\/$/, "") || "";

// Must match AuthContext's storage key so the refresh flow shares one session.
const AUTH_STORAGE_KEY = "nextgen_auth";
export const AUTH_REFRESHED_EVENT = "nextgen-auth-refreshed";
export const AUTH_EXPIRED_EVENT = "nextgen-auth-expired";

function readStoredSession(): AuthSession | null {
  try {
    const raw = localStorage.getItem(AUTH_STORAGE_KEY);
    return raw ? (JSON.parse(raw) as AuthSession) : null;
  } catch {
    return null;
  }
}

function writeStoredTokens(access: string, refresh: string): void {
  const s = readStoredSession();
  if (!s) return;
  s.access = access;
  s.refresh = refresh;
  try {
    localStorage.setItem(AUTH_STORAGE_KEY, JSON.stringify(s));
  } catch {
    // ignore quota / privacy-mode errors
  }
}

// Single-flight refresh: many requests may 401 at once; only one hits the
// refresh endpoint, the rest await the same promise.
let refreshInFlight: Promise<string | null> | null = null;

export function refreshAccessToken(): Promise<string | null> {
  if (refreshInFlight) return refreshInFlight;
  const refresh = readStoredSession()?.refresh;
  if (!refresh) return Promise.resolve(null);
  refreshInFlight = (async () => {
    try {
      const res = await fetch(`${API_BASE}/api/auth/refresh/`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ refresh }),
      });
      if (!res.ok) {
        // Refresh token expired/blacklisted → session is over.
        window.dispatchEvent(new CustomEvent(AUTH_EXPIRED_EVENT));
        return null;
      }
      const data = (await res.json()) as { access: string; refresh?: string };
      const nextRefresh = data.refresh ?? refresh;
      writeStoredTokens(data.access, nextRefresh);
      window.dispatchEvent(
        new CustomEvent(AUTH_REFRESHED_EVENT, {
          detail: { access: data.access, refresh: nextRefresh },
        })
      );
      return data.access;
    } catch {
      return null;
    } finally {
      refreshInFlight = null;
    }
  })();
  return refreshInFlight;
}

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
  const send = (bearer?: string | null) => {
    const headers = new Headers(options.headers || {});
    if (bearer) headers.set("Authorization", `Bearer ${bearer}`);
    if (!(options.body instanceof FormData) && !headers.has("Content-Type") && options.body) {
      headers.set("Content-Type", "application/json");
    }
    return fetch(`${API_BASE}${path}`, { ...options, headers });
  };

  let res = await send(token);
  // Transparently refresh a short-lived access token once on 401, then retry.
  if (res.status === 401 && token) {
    const fresh = await refreshAccessToken();
    if (fresh) res = await send(fresh);
  }
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
