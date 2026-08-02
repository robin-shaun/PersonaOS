import type {
  Account,
  AnswerResult,
  AuditEvent,
  AuthenticatedSession,
  AuthenticationStatus,
  CitationBundle,
  Conversation,
  ConversationMessage,
  DocumentBundle,
  Health,
  MemoryBundle,
  MemoryRelation,
  MemoryRelationKind,
  MemoryStatus,
  ModelBoundary,
  Persona,
  PersonaExport,
  PersonaImportResult,
  Sensitivity,
  SourceDocument,
  Task,
  TaskBundle,
  UploadResult,
} from "./types";

const configuredBase = (import.meta.env.VITE_API_BASE_URL ?? "").trim();
const API_BASE = configuredBase.replace(/\/+$/, "");
const unsafeMethods = new Set(["POST", "PUT", "PATCH", "DELETE"]);
let csrfToken = "";

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    message: string,
    public readonly payload?: unknown,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

function requestId(): string {
  const suffix =
    typeof crypto !== "undefined" && "randomUUID" in crypto
      ? crypto.randomUUID()
      : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
  return `web-${suffix}`.slice(0, 100);
}

function errorMessage(payload: unknown, status: number): string {
  if (payload && typeof payload === "object" && "detail" in payload) {
    if (typeof payload.detail === "string") {
      return payload.detail;
    }
    if (Array.isArray(payload.detail)) {
      const messages = payload.detail
        .map((item) => {
          if (!item || typeof item !== "object" || !("msg" in item)) return "";
          const location =
            "loc" in item && Array.isArray(item.loc)
              ? item.loc.slice(1).join(".")
              : "";
          return `${location ? `${location}: ` : ""}${String(item.msg)}`;
        })
        .filter(Boolean);
      if (messages.length > 0) return messages.join("；");
    }
  }
  return `请求失败（HTTP ${status}）`;
}

export async function request<T>(
  path: string,
  init: RequestInit = {},
): Promise<T> {
  const headers = new Headers(init.headers);
  const method = (init.method ?? "GET").toUpperCase();
  headers.set("Accept", "application/json");
  headers.set("X-Request-ID", requestId());
  if (unsafeMethods.has(method) && csrfToken) {
    headers.set("X-CSRF-Token", csrfToken);
  }
  if (init.body && !(init.body instanceof FormData)) {
    headers.set("Content-Type", "application/json");
  }
  const response = await fetch(`${API_BASE}${path}`, {
    ...init,
    credentials: "same-origin",
    headers,
  });
  const contentType = response.headers.get("content-type") ?? "";
  const payload: unknown = contentType.includes("application/json")
    ? await response.json()
    : await response.text();
  if (!response.ok) {
    const invalidSession =
      response.status === 401 &&
      path !== "/api/v1/auth/login" &&
      path !== "/api/v1/auth/reauthenticate";
    if (invalidSession) {
      csrfToken = "";
      window.dispatchEvent(
        new CustomEvent("personaos:authentication-required", {
          detail: { path },
        }),
      );
    }
    if (response.status === 428) {
      window.dispatchEvent(
        new CustomEvent("personaos:reauthentication-required", {
          detail: { path },
        }),
      );
    }
    throw new ApiError(response.status, errorMessage(payload, response.status), payload);
  }
  return payload as T;
}

export const api = {
  health: () => request<Health>("/health"),

  authenticationStatus: () =>
    request<AuthenticationStatus>("/api/v1/auth/status"),

  getSession: async () => {
    const result = await request<AuthenticatedSession>("/api/v1/auth/session");
    csrfToken = result.csrf_token;
    return result;
  },

  login: async (username: string, password: string) => {
    const result = await request<AuthenticatedSession>("/api/v1/auth/login", {
      method: "POST",
      body: JSON.stringify({ username, password }),
    });
    csrfToken = result.csrf_token;
    return result;
  },

  reauthenticate: async (password: string) => {
    const result = await request<AuthenticatedSession>(
      "/api/v1/auth/reauthenticate",
      {
        method: "POST",
        body: JSON.stringify({ password }),
      },
    );
    csrfToken = result.csrf_token;
    return result;
  },

  logout: async () => {
    try {
      await request<void>("/api/v1/auth/logout", { method: "POST" });
    } finally {
      csrfToken = "";
    }
  },

  listAccounts: () => request<Account[]>("/api/v1/accounts"),

  createAccount: (
    username: string,
    displayName: string,
    password: string,
    role: "admin" | "member",
  ) =>
    request<Account>("/api/v1/accounts", {
      method: "POST",
      body: JSON.stringify({
        username,
        display_name: displayName,
        password,
        role,
      }),
    }),

  listPersonas: () => request<Persona[]>("/api/v1/personas"),

  createPersona: (displayName: string, description: string) =>
    request<Persona>("/api/v1/personas", {
      method: "POST",
      body: JSON.stringify({
        display_name: displayName,
        description,
      }),
    }),

  importPersona: (personaExport: PersonaExport) =>
    request<PersonaImportResult>("/api/v1/personas/import", {
      method: "POST",
      body: JSON.stringify(personaExport),
    }),

  updateModelPolicy: (
    personaId: string,
    allowedModelBoundaries: ModelBoundary[],
    externalDataAcknowledged: boolean,
  ) =>
    request<Persona>(
      `/api/v1/personas/${encodeURIComponent(personaId)}/model-policy`,
      {
        method: "PATCH",
        body: JSON.stringify({
          allowed_model_boundaries: allowedModelBoundaries,
          external_data_acknowledged: externalDataAcknowledged,
        }),
      },
    ),

  listDocuments: (personaId: string) =>
    request<SourceDocument[]>(
      `/api/v1/personas/${encodeURIComponent(personaId)}/documents`,
    ),

  getDocument: (documentId: string) =>
    request<DocumentBundle>(
      `/api/v1/documents/${encodeURIComponent(documentId)}`,
    ),

  uploadDocument: (personaId: string, file: File, language?: string) => {
    const form = new FormData();
    form.append("file", file);
    const query = language?.trim()
      ? `?language=${encodeURIComponent(language.trim())}`
      : "";
    return request<UploadResult>(
      `/api/v1/personas/${encodeURIComponent(personaId)}/documents${query}`,
      { method: "POST", body: form },
    );
  },

  deleteDocument: (documentId: string) =>
    request<Record<string, unknown>>(
      `/api/v1/documents/${encodeURIComponent(documentId)}?confirm=true`,
      { method: "DELETE" },
    ),

  listCandidates: (personaId: string) =>
    request<MemoryBundle[]>(
      `/api/v1/personas/${encodeURIComponent(personaId)}/memory-candidates`,
    ),

  reviewCandidate: (
    memoryId: string,
    action: "confirm" | "reject",
    editedContent?: string,
    reason?: string,
  ) =>
    request<MemoryBundle>(
      `/api/v1/memory-candidates/${encodeURIComponent(memoryId)}/review`,
      {
        method: "POST",
        body: JSON.stringify({
          action,
          edited_content: editedContent || null,
          reason: reason?.trim() || null,
        }),
      },
    ),

  listMemories: (personaId: string, status: MemoryStatus = "confirmed") =>
    request<MemoryBundle[]>(
      `/api/v1/personas/${encodeURIComponent(personaId)}/memories?status=${encodeURIComponent(status)}`,
    ),

  getMemory: (memoryId: string) =>
    request<MemoryBundle>(
      `/api/v1/memories/${encodeURIComponent(memoryId)}`,
    ),

  updateMemory: (
    memoryId: string,
    expectedVersion: number,
    content: string,
    sensitivity: Sensitivity,
    reason: string,
  ) =>
    request<MemoryBundle>(
      `/api/v1/memories/${encodeURIComponent(memoryId)}`,
      {
        method: "PATCH",
        body: JSON.stringify({
          expected_version: expectedVersion,
          content,
          sensitivity,
          reason,
        }),
      },
    ),

  deleteMemory: (memoryId: string) =>
    request<Record<string, unknown>>(
      `/api/v1/memories/${encodeURIComponent(memoryId)}?confirm=true`,
      { method: "DELETE" },
    ),

  listMemoryRelations: (memoryId: string) =>
    request<MemoryRelation[]>(
      `/api/v1/memories/${encodeURIComponent(memoryId)}/relations`,
    ),

  createMemoryRelation: (
    personaId: string,
    fromMemoryId: string,
    toMemoryId: string,
    relation: MemoryRelationKind,
    confidence: number,
    evidenceMemoryVersionIds: string[],
  ) =>
    request<{ relation: MemoryRelation; created: boolean }>(
      `/api/v1/personas/${encodeURIComponent(personaId)}/memory-relations`,
      {
        method: "POST",
        body: JSON.stringify({
          from_memory_id: fromMemoryId,
          to_memory_id: toMemoryId,
          relation,
          confidence,
          evidence_memory_version_ids: evidenceMemoryVersionIds,
        }),
      },
    ),

  deleteMemoryRelation: (relationId: string) =>
    request<Record<string, unknown>>(
      `/api/v1/memory-relations/${encodeURIComponent(relationId)}?confirm=true`,
      { method: "DELETE" },
    ),

  reindexMemories: (personaId: string) =>
    request<{
      task_id: string;
      queue_job_id: string;
      created: boolean;
      idempotency_replayed: boolean;
      embedding_space_id: string;
    }>(
      `/api/v1/personas/${encodeURIComponent(personaId)}/memories/reindex`,
      {
        method: "POST",
        headers: {
          "Idempotency-Key": `web-reindex-${personaId}-${Date.now()}`,
        },
      },
    ),

  createConversation: (personaId: string, title?: string) =>
    request<Conversation>(
      `/api/v1/personas/${encodeURIComponent(personaId)}/conversations`,
      {
        method: "POST",
        body: JSON.stringify({ title: title?.trim() || null }),
      },
    ),

  listMessages: (conversationId: string) =>
    request<ConversationMessage[]>(
      `/api/v1/conversations/${encodeURIComponent(conversationId)}/messages`,
    ),

  askQuestion: (conversationId: string, content: string, topK: number) =>
    request<AnswerResult>(
      `/api/v1/conversations/${encodeURIComponent(conversationId)}/messages`,
      {
        method: "POST",
        body: JSON.stringify({ content, top_k: topK }),
      },
    ),

  getCitations: (messageId: string) =>
    request<CitationBundle[]>(
      `/api/v1/messages/${encodeURIComponent(messageId)}/citations`,
    ),

  listAuditEvents: (personaId: string, limit = 200) =>
    request<AuditEvent[]>(
      `/api/v1/personas/${encodeURIComponent(personaId)}/audit-events?limit=${limit}`,
    ),

  exportPersona: (personaId: string, includeRawSources: boolean) =>
    request<PersonaExport>(
      `/api/v1/personas/${encodeURIComponent(personaId)}/export`,
      {
        method: "POST",
        body: JSON.stringify({ include_raw_sources: includeRawSources }),
      },
    ),

  listTasks: (limit = 100) =>
    request<Task[]>(`/api/v1/tasks?limit=${limit}`),

  getTask: (taskId: string) =>
    request<TaskBundle>(`/api/v1/tasks/${encodeURIComponent(taskId)}`),

  retryTask: (taskId: string) =>
    request<TaskBundle>(
      `/api/v1/tasks/${encodeURIComponent(taskId)}/retry`,
      { method: "POST" },
    ),

  cancelTask: (taskId: string, reason: string) =>
    request<TaskBundle>(
      `/api/v1/tasks/${encodeURIComponent(taskId)}/cancel`,
      {
        method: "POST",
        body: JSON.stringify({ reason }),
      },
    ),
};
