export type ModelBoundary = "local" | "private_network" | "external";
export type Sensitivity = "public" | "private" | "restricted";
export type MemoryStatus =
  | "candidate"
  | "confirmed"
  | "rejected"
  | "superseded"
  | "deleted";

export interface Health {
  status: string;
  version: string;
  runtime: string;
  persona_identity_mode: string;
  account_setup_required: boolean;
  persona_blob_encryption: string;
  persona_embedding_space_id: string;
}

export interface AuthenticationStatus {
  mode: "trusted_local_accounts" | "public_registration";
  setup_required: boolean;
  cookie_secure: boolean;
  local_only: boolean;
  registration_enabled: boolean;
  turnstile_site_key: string | null;
}

export interface Account {
  id: string;
  username: string;
  display_name: string;
  role: "admin" | "member";
  status: "active" | "disabled";
  created_at: string;
  updated_at: string;
  password_changed_at: string | null;
  last_login_at: string | null;
}

export interface AuthenticatedSession {
  account: Account;
  session: {
    id: string;
    idle_expires_at: string;
    absolute_expires_at: string;
    reauthenticated_at: string;
  };
  csrf_token: string;
  reauthentication_window_seconds: number;
}

export interface Persona {
  id: string;
  owner_id: string;
  display_name: string;
  description: string;
  simulation_notice: string;
  allowed_model_boundaries: ModelBoundary[];
  status: string;
  version: number;
  created_at: string;
  updated_at: string;
}

export interface SourceDocument {
  id: string;
  persona_id: string;
  task_id: string | null;
  source_type: string;
  original_filename: string;
  media_type: string;
  language: string | null;
  content_sha256: string;
  byte_size: number;
  status: string;
  ingestion_version: string;
  error: string | null;
  created_at: string;
  updated_at: string;
  processed_at: string | null;
}

export interface DocumentChunk {
  id: string;
  document_id: string;
  ordinal: number;
  content: string;
  content_sha256: string;
  char_start: number;
  char_end: number;
  line_start: number;
  line_end: number;
  locator: Record<string, unknown>;
}

export interface DocumentBundle {
  document: SourceDocument;
  chunks: DocumentChunk[];
}

export interface UploadResult {
  document: SourceDocument;
  document_created: boolean;
  blob_created: boolean;
  queue_submission: {
    task_id: string;
    queue_job_id: string;
    created: boolean;
    idempotency_replayed: boolean;
  };
}

export interface PersonaMemory {
  id: string;
  persona_id: string;
  source_document_id: string;
  memory_type: string;
  status: MemoryStatus;
  epistemic_status: string;
  current_version_id: string;
  confidence: number;
  importance: number;
  sensitivity: Sensitivity;
  visibility: string;
  event_at: string | null;
  confirmed_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface MemoryVersion {
  id: string;
  memory_id: string;
  version: number;
  raw_content: string;
  structured_summary: string;
  metadata_snapshot: Record<string, unknown>;
  created_by_type: string;
  created_by_id: string;
  change_reason: string | null;
  extractor_name: string;
  extractor_version: string;
  created_at: string;
}

export interface MemoryEvidence {
  evidence: {
    id: string;
    relation: string;
    excerpt: string;
    excerpt_sha256: string;
    locator_snapshot: Record<string, unknown>;
  };
  source_document: SourceDocument;
  document_chunk: DocumentChunk;
}

export interface MemoryBundle {
  memory: PersonaMemory;
  current_version: MemoryVersion;
  versions: MemoryVersion[];
  evidence: MemoryEvidence[];
  indexing?: {
    created: boolean;
    embedding_space_id: string;
  };
}

export type MemoryRelationKind =
  | "supports"
  | "conflicts"
  | "derived_from"
  | "supersedes"
  | "related_to";

export interface MemoryRelation {
  id: string;
  persona_id: string;
  from_memory_id: string;
  to_memory_id: string;
  relation: MemoryRelationKind;
  confidence: number;
  evidence_memory_version_ids: string[];
  created_at: string;
}

export interface AuditEvent {
  id: string;
  occurred_at: string;
  request_id: string | null;
  actor_type: string;
  actor_id: string;
  persona_id: string | null;
  action: string;
  resource_type: string;
  resource_id: string;
  outcome: string;
  risk_level: string;
  before_hash: string | null;
  after_hash: string | null;
  detail: Record<string, unknown>;
}

export interface Conversation {
  id: string;
  persona_id: string;
  title: string | null;
  status: string;
  created_at: string;
  updated_at: string;
}

export interface ConversationMessage {
  id: string;
  conversation_id: string;
  persona_id: string;
  role: "user" | "assistant";
  content: string;
  answer_status: "not_applicable" | "answered" | "no_memory";
  claims: Array<{
    text?: string;
    citation_ids?: string[];
    [key: string]: unknown;
  }>;
  uncertainty: Record<string, unknown>;
  simulation_notice: string | null;
  created_at: string;
}

export interface CitationBundle {
  citation: {
    id: string;
    citation_id: string;
    memory_id: string;
    memory_version_id: string;
    excerpt: string;
    rank: number;
    claim_indexes: number[];
  };
  memory: {
    id: string;
    memory_type: string;
    status: string;
    epistemic_status: string;
    sensitivity: string;
    version: number;
    structured_summary: string;
  };
  evidence: {
    id: string;
    relation: string;
    excerpt_sha256: string;
  };
  source: {
    id: string;
    filename: string;
    media_type: string;
    content_sha256: string;
    locator: Record<string, unknown>;
    excerpt: string;
    chunk_ordinal: number;
  };
}

export interface AnswerResult {
  user_message: ConversationMessage;
  assistant_message: ConversationMessage;
  citations: CitationBundle[];
  retrieval_run: {
    id: string;
    status: string;
    candidates: Array<Record<string, unknown>>;
    filters: Record<string, unknown>;
    embedding_space_id: string;
  };
  model_call: {
    id: string;
    provider: string;
    model_name: string;
    model_version: string;
    data_boundary: ModelBoundary;
    status: string;
    latency_ms: number;
  };
}

export interface Task {
  id: string;
  employee_id: string;
  user_id: string;
  workflow_name: string;
  status: string;
  input: Record<string, unknown>;
  final_output: Record<string, unknown> | null;
  created_at: string;
  updated_at: string;
}

export interface TaskBundle {
  task: Task;
  runs: Array<Record<string, unknown>>;
  queue_jobs: Array<Record<string, unknown>>;
  workflow_runs: Array<Record<string, unknown>>;
  approvals: Array<Record<string, unknown>>;
  artifacts: Array<Record<string, unknown>>;
  task_events: Array<Record<string, unknown>>;
  [key: string]: unknown;
}

export interface PersonaExport {
  export: Record<string, unknown>;
  manifest: {
    sha256: string;
    byte_size: number;
    included_raw_sources: boolean;
    audit_event_id: string;
  };
}

export interface PersonaImportResult {
  persona: Persona;
  restored: {
    persona_id: string;
    identity_preserved: boolean;
    source_document_count: number;
    memory_count: number;
    memory_version_count: number;
    conversation_count: number;
    audit_event_count: number;
    import_audit_event_id: string;
  };
  indexing: {
    eligible_count: number;
    indexed_count: number;
    created_count: number;
  } | null;
  manifest: {
    sha256: string;
    schema_version: string;
    identity_preserved: boolean;
    model_boundaries_reset_to_local: boolean;
  };
}
