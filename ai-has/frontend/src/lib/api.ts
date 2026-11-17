export type UserRole = "employee" | "support_agent" | "admin";

export interface Persona {
  role: UserRole;
  label: string;
  email: string;
  fullName: string;
  department: string;
}

export const PERSONAS: Record<UserRole, Persona> = {
  employee: {
    role: "employee",
    label: "Employee",
    email: "alex.chen@acme-corp.com",
    fullName: "Alex Chen",
    department: "Product Engineering",
  },
  support_agent: {
    role: "support_agent",
    label: "Support Agent",
    email: "maya.patel@acme-corp.com",
    fullName: "Maya Patel",
    department: "IT Service Desk",
  },
  admin: {
    role: "admin",
    label: "Admin",
    email: "jordan.taylor@acme-corp.com",
    fullName: "Jordan Taylor",
    department: "Enterprise IT & Security",
  },
};

export interface Citation {
  doc_id: string;
  filename: string;
  doc_code: string;
  title: string;
  section: string;
  chunk_id: string;
  score: number;
  vector_score: number;
  bm25_score: number;
  excerpt: string;
}

export interface ToolCallEvent {
  tool_name: string;
  arguments: Record<string, unknown>;
  result: Record<string, unknown>;
  status?: string;
}

export interface GuardrailEvent {
  pii_redacted: boolean;
  pii_categories: string[];
  injection_blocked: boolean;
  risk_score: number;
  matched_rules?: string[];
}

export interface ChatMessage {
  id: string;
  session_id: string;
  sender_role: "user" | "assistant" | "support_agent" | "system";
  sender_email?: string | null;
  content: string;
  pii_redacted?: boolean;
  citations: Citation[];
  tool_calls: ToolCallEvent[];
  intent?: string | null;
  latency_ms?: number | null;
  created_at?: string | null;
}

export interface ChatSession {
  id: string;
  user_email: string;
  title: string;
  status: "active" | "ai_resolved" | "escalated" | "agent_active" | "closed";
  assigned_agent_email?: string | null;
  handoff_summary?: string | null;
  deflected: boolean;
  created_at?: string | null;
  updated_at?: string | null;
  resolved_at?: string | null;
  message_count: number;
  messages?: ChatMessage[];
  ticket?: {
    id: string;
    priority: string;
    category: string;
    status: string;
  } | null;
}

export interface Ticket {
  id: string;
  session_id?: string | null;
  requester_email: string;
  requester_name: string;
  title: string;
  description: string;
  category: string;
  priority: "low" | "medium" | "high" | "critical";
  status: "open" | "in_progress" | "escalated" | "pending_approval" | "resolved" | "closed";
  assigned_agent_email?: string | null;
  ai_handoff_summary?: string | null;
  external_system?: string | null;
  external_ticket_ref?: string | null;
  resolution_notes?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
  resolved_at?: string | null;
}

export interface KBDocument {
  id: string;
  filename: string;
  title: string;
  doc_code: string;
  file_type: string;
  chunk_count: number;
  content_hash: string;
  indexed_at?: string | null;
}

export interface HealthStatus {
  status: string;
  service: string;
  version: string;
  openai_configured: boolean;
  chat_model: string;
  embedding_mode: string;
  kb_documents: number;
  kb_chunks: number;
}

export interface AnalyticsSummary {
  kpis: {
    total_sessions: number;
    total_chat_turns: number;
    deflected_turns: number;
    deflection_rate_pct: number;
    avg_latency_ms: number;
    p95_latency_ms: number;
    avg_mttr_minutes: number;
    avg_faithfulness_score: number;
    avg_relevance_score: number;
    open_tickets: number;
    escalated_tickets: number;
    resolved_tickets: number;
    pii_redactions_count: number;
    injections_blocked_count: number;
    rbac_denials_count: number;
  };
  intent_breakdown: Array<{ intent: string; count: number }>;
  performance_timeseries: Array<{
    label: string;
    timestamp: string;
    latency_ms: number;
    faithfulness: number | null;
    relevance: number | null;
  }>;
}

export interface AuditLog {
  id: string;
  event_type: string;
  severity: string;
  actor_email: string;
  actor_role: string;
  session_id?: string | null;
  action: string;
  details: Record<string, unknown>;
  created_at?: string | null;
}

export interface WebhookDelivery {
  id: string;
  target_system: string;
  endpoint_url: string;
  event_name: string;
  delivery_mode: string;
  response_status: number;
  response_body: Record<string, unknown>;
  payload: Record<string, unknown>;
  latency_ms: number;
  created_at?: string | null;
}

export const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

function authHeaders(persona: Persona): Record<string, string> {
  return {
    "X-User-Role": persona.role,
    "X-User-Email": persona.email,
    "X-User-Name": persona.fullName,
  };
}

export class ApiError extends Error {
  status: number;
  code?: string;
  requestId?: string;
  constructor(status: number, message: string, code?: string, requestId?: string) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.requestId = requestId;
  }
}

const REQUEST_TIMEOUT_MS = 30000;

const STATUS_MESSAGES: Record<number, string> = {
  401: "Your session is not authenticated. Please switch role or reload.",
  403: "You do not have permission to perform this action.",
  404: "The requested item was not found.",
  429: "Too many requests. Please wait a moment and retry.",
  500: "The server hit an unexpected error. Please try again.",
  502: "The backend is unreachable. Please try again shortly.",
  503: "The backend is temporarily unavailable. Please try again shortly.",
};

/** Turn any failed Response into an ApiError with a human-readable message. */
async function toApiError(resp: Response): Promise<ApiError> {
  let detail = STATUS_MESSAGES[resp.status] || `Request failed (HTTP ${resp.status})`;
  let code: string | undefined;
  let requestId: string | undefined = resp.headers.get("X-Request-ID") || undefined;
  try {
    const body = await resp.json();
    if (typeof body?.detail === "string") {
      detail = body.detail;
    } else if (Array.isArray(body?.detail)) {
      detail = body.detail
        .map((d: { msg?: string }) => d?.msg)
        .filter(Boolean)
        .join("; ") || detail;
    }
    code = body?.code;
    requestId = body?.request_id || requestId;
  } catch {
    // non-JSON body: keep the status-based message
  }
  return new ApiError(resp.status, detail, code, requestId);
}

/** fetch wrapper adding a timeout and mapping network failures to ApiError(0). */
async function safeFetch(url: string, init: RequestInit = {}, timeoutMs = REQUEST_TIMEOUT_MS) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetch(url, { ...init, signal: init.signal ?? controller.signal });
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") {
      throw new ApiError(0, "The request timed out. Please try again.", "timeout");
    }
    throw new ApiError(
      0,
      `Cannot reach the AI-HAS backend at ${API_BASE_URL}. Is it running?`,
      "network_error"
    );
  } finally {
    clearTimeout(timer);
  }
}

/** Format any thrown value for display, including the request id when known. */
export function describeError(err: unknown, fallback = "Something went wrong"): string {
  if (err instanceof ApiError) {
    return err.requestId ? `${err.message} (ref: ${err.requestId})` : err.message;
  }
  return err instanceof Error ? err.message : fallback;
}

async function apiRequest<T>(
  path: string,
  persona: Persona,
  options: RequestInit = {}
): Promise<T> {
  const isFormData =
    typeof FormData !== "undefined" && options.body instanceof FormData;
  const headers: Record<string, string> = {
    ...authHeaders(persona),
    ...(isFormData ? {} : { "Content-Type": "application/json" }),
    ...(options.headers as Record<string, string> | undefined),
  };

  const resp = await safeFetch(`${API_BASE_URL}${path}`, {
    ...options,
    headers,
    cache: "no-store",
  });

  if (!resp.ok) throw await toApiError(resp);
  try {
    return (await resp.json()) as T;
  } catch {
    throw new ApiError(resp.status, "The server returned an invalid response.", "bad_response");
  }
}

export async function fetchHealth(): Promise<HealthStatus> {
  const resp = await safeFetch(`${API_BASE_URL}/api/health`, { cache: "no-store" }, 8000);
  if (!resp.ok) throw await toApiError(resp);
  return resp.json() as Promise<HealthStatus>;
}

export async function listSessions(
  persona: Persona,
  statusFilter?: string
): Promise<{ sessions: ChatSession[] }> {
  const qs = statusFilter ? `?status=${encodeURIComponent(statusFilter)}` : "";
  return apiRequest(`/api/chat/sessions${qs}`, persona);
}

export async function getSession(
  persona: Persona,
  sessionId: string
): Promise<ChatSession> {
  return apiRequest(`/api/chat/sessions/${encodeURIComponent(sessionId)}`, persona);
}

export async function escalateSession(
  persona: Persona,
  sessionId: string,
  reason: string
): Promise<{ status: string; ticket_id: string; handoff_summary: string }> {
  return apiRequest(`/api/chat/sessions/${encodeURIComponent(sessionId)}/escalate`, persona, {
    method: "POST",
    body: JSON.stringify({ reason, priority: "high" }),
  });
}

export async function listEscalatedQueue(
  persona: Persona
): Promise<{ queue: ChatSession[] }> {
  return apiRequest("/api/chat/escalated", persona);
}

export async function takeoverSession(
  persona: Persona,
  sessionId: string
): Promise<ChatSession> {
  return apiRequest(`/api/chat/sessions/${encodeURIComponent(sessionId)}/takeover`, persona, {
    method: "POST",
  });
}

export async function sendAgentMessage(
  persona: Persona,
  sessionId: string,
  message: string
): Promise<{ message: ChatMessage; session_status: string }> {
  return apiRequest(
    `/api/chat/sessions/${encodeURIComponent(sessionId)}/agent-message`,
    persona,
    {
      method: "POST",
      body: JSON.stringify({ message }),
    }
  );
}

export async function resolveSession(
  persona: Persona,
  sessionId: string,
  resolutionNotes: string
): Promise<{ status: string; mttr_minutes: number; ticket_id: string | null }> {
  return apiRequest(`/api/chat/sessions/${encodeURIComponent(sessionId)}/resolve`, persona, {
    method: "POST",
    body: JSON.stringify({ resolution_notes: resolutionNotes }),
  });
}

export async function listTickets(
  persona: Persona,
  statusFilter?: string
): Promise<{ tickets: Ticket[]; total: number }> {
  const qs = statusFilter ? `?status=${encodeURIComponent(statusFilter)}` : "";
  return apiRequest(`/api/tickets${qs}`, persona);
}

export async function updateTicket(
  persona: Persona,
  ticketId: string,
  patch: Partial<{
    status: string;
    priority: string;
    assigned_agent_email: string;
    resolution_notes: string;
  }>
): Promise<{ ticket: Ticket }> {
  return apiRequest(`/api/tickets/${encodeURIComponent(ticketId)}`, persona, {
    method: "PATCH",
    body: JSON.stringify(patch),
  });
}

export async function listKBDocuments(
  persona: Persona
): Promise<{
  documents: KBDocument[];
  total_documents: number;
  total_chunks: number;
  embedding_mode: string;
}> {
  return apiRequest("/api/kb", persona);
}

export async function searchKB(
  persona: Persona,
  query: string
): Promise<{ query: string; results: Citation[] }> {
  return apiRequest(`/api/kb/search?q=${encodeURIComponent(query)}&top_k=5`, persona);
}

export async function uploadKBDocument(
  persona: Persona,
  file: File
): Promise<{ document: KBDocument; total_chunks: number }> {
  const form = new FormData();
  form.append("file", file);
  return apiRequest("/api/kb/upload", persona, {
    method: "POST",
    body: form,
  });
}

export async function reindexKB(
  persona: Persona
): Promise<{ status: string; documents_indexed: number; total_chunks: number }> {
  return apiRequest("/api/kb/reindex", persona, { method: "POST" });
}

export async function deleteKBDocument(
  persona: Persona,
  docId: string
): Promise<{ status: string; doc_id: string }> {
  return apiRequest(`/api/kb/${encodeURIComponent(docId)}`, persona, {
    method: "DELETE",
  });
}

export async function fetchAnalyticsSummary(
  persona: Persona
): Promise<AnalyticsSummary> {
  return apiRequest("/api/analytics/summary", persona);
}

export async function fetchAuditLogs(
  persona: Persona,
  eventType?: string
): Promise<{ logs: AuditLog[] }> {
  const qs = eventType ? `?event_type=${encodeURIComponent(eventType)}` : "";
  return apiRequest(`/api/analytics/audit-logs${qs}`, persona);
}

export async function fetchWebhookDeliveries(
  persona: Persona
): Promise<{ deliveries: WebhookDelivery[] }> {
  return apiRequest("/api/analytics/webhooks", persona);
}

export interface StreamCallbacks {
  onSession?: (data: { session_id: string; status: string; title: string }) => void;
  onGuardrail?: (data: GuardrailEvent) => void;
  onCitations?: (citations: Citation[]) => void;
  onToolResult?: (tool: ToolCallEvent) => void;
  onToken?: (delta: string) => void;
  onDone?: (payload: {
    message_id: string;
    session_id: string;
    session_status: string;
    intent: string;
    latency_ms: number;
    faithfulness_score?: number | null;
    relevance_score?: number | null;
    deflected: boolean;
    citations: Citation[];
    tool_calls: ToolCallEvent[];
    llm_mode: string;
  }) => void;
}

export async function streamChat(
  persona: Persona,
  message: string,
  sessionId: string | null,
  callbacks: StreamCallbacks
): Promise<void> {
  // Long timeout: only guards the initial connection/headers, not the open stream.
  const resp = await safeFetch(
    `${API_BASE_URL}/api/chat/stream`,
    {
      method: "POST",
      headers: {
        ...authHeaders(persona),
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ message, session_id: sessionId }),
    },
    60000
  );

  if (!resp.ok) throw await toApiError(resp);
  if (!resp.body) throw new ApiError(resp.status, "The server returned an empty stream.");

  const reader = resp.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";
  let streamError: ApiError | null = null;

  const dispatchFrame = (frame: string) => {
    const lines = frame.split(/\r?\n/);
    let eventName = "";
    const dataParts: string[] = [];

    for (const line of lines) {
      if (line.startsWith("event:")) {
        eventName = line.slice(6).trim();
      } else if (line.startsWith("data:")) {
        dataParts.push(line.slice(5).trim());
      }
    }

    if (!eventName || dataParts.length === 0) return;
    const rawData = dataParts.join("\n");
    let parsed: Record<string, unknown>;
    try {
      parsed = JSON.parse(rawData);
    } catch {
      return;
    }

    if (eventName === "error") {
      streamError = new ApiError(
        500,
        typeof parsed.detail === "string" ? parsed.detail : "The assistant stream failed.",
        "stream_error"
      );
      return;
    }
    if (eventName === "session" && callbacks.onSession) {
      callbacks.onSession(parsed as { session_id: string; status: string; title: string });
    } else if (eventName === "guardrail" && callbacks.onGuardrail) {
      callbacks.onGuardrail(parsed as unknown as GuardrailEvent);
    } else if (eventName === "citations" && callbacks.onCitations) {
      callbacks.onCitations((parsed.citations as Citation[]) || []);
    } else if (eventName === "tool_result" && callbacks.onToolResult) {
      callbacks.onToolResult(parsed as unknown as ToolCallEvent);
    } else if (eventName === "token" && callbacks.onToken) {
      callbacks.onToken((parsed.delta as string) || "");
    } else if (eventName === "done" && callbacks.onDone) {
      callbacks.onDone(
        parsed as unknown as Parameters<NonNullable<StreamCallbacks["onDone"]>>[0]
      );
    }
  };

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const frames = buffer.split(/\r?\n\r?\n/);
    buffer = frames.pop() ?? "";
    for (const frame of frames) {
      if (frame.trim()) dispatchFrame(frame);
    }
  }

  if (buffer.trim()) {
    dispatchFrame(buffer);
  }
}
