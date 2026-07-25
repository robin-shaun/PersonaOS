import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import App from "./App";
import type {
  Account,
  AnswerResult,
  AuthenticatedSession,
  MemoryBundle,
  Persona,
} from "./types";

const account: Account = {
  id: "local-user",
  username: "local-admin",
  display_name: "Local Admin",
  role: "admin",
  status: "active",
  created_at: "2026-07-25T00:00:00+00:00",
  updated_at: "2026-07-25T00:00:00+00:00",
  password_changed_at: "2026-07-25T00:00:00+00:00",
  last_login_at: "2026-07-25T00:00:00+00:00",
};

const authenticatedSession: AuthenticatedSession = {
  account,
  session: {
    id: "session-1",
    idle_expires_at: "2026-07-25T01:00:00+00:00",
    absolute_expires_at: "2026-07-25T12:00:00+00:00",
    reauthenticated_at: "2026-07-25T00:00:00+00:00",
  },
  csrf_token: "csrf-test-token",
  reauthentication_window_seconds: 300,
};

const persona: Persona = {
  id: "persona-1",
  owner_id: "local-user",
  display_name: "测试人物",
  description: "只依据已确认资料回答。",
  simulation_notice: "这是基于授权资料的模拟智能体，不是现实中的本人。",
  allowed_model_boundaries: ["local"],
  status: "active",
  version: 1,
  created_at: "2026-07-25T00:00:00+00:00",
  updated_at: "2026-07-25T00:00:00+00:00",
};

const candidate: MemoryBundle = {
  memory: {
    id: "memory-1",
    persona_id: persona.id,
    source_document_id: "document-1",
    memory_type: "episodic",
    status: "candidate",
    epistemic_status: "source_verified",
    current_version_id: "version-1",
    confidence: 0.9,
    importance: 0.7,
    sensitivity: "private",
    visibility: "owner",
    event_at: null,
    confirmed_at: null,
    created_at: "2026-07-25T00:00:00+00:00",
    updated_at: "2026-07-25T00:00:00+00:00",
  },
  current_version: {
    id: "version-1",
    memory_id: "memory-1",
    version: 1,
    raw_content: "2025-03-04，我加入 PersonaOS 项目。",
    structured_summary: "加入 PersonaOS 项目",
    metadata_snapshot: { source_bound: true, user_confirmed: false },
    created_by_type: "system",
    created_by_id: "rules",
    change_reason: null,
    extractor_name: "rules",
    extractor_version: "1",
    created_at: "2026-07-25T00:00:00+00:00",
  },
  versions: [],
  evidence: [
    {
      evidence: {
        id: "evidence-1",
        relation: "supports",
        excerpt: "2025-03-04，我加入 PersonaOS 项目。",
        excerpt_sha256: "abc",
        locator_snapshot: { line_start: 3, line_end: 3 },
      },
      source_document: {
        id: "document-1",
        persona_id: persona.id,
        task_id: "task-1",
        source_type: "uploaded_text",
        original_filename: "career.md",
        media_type: "text/markdown",
        language: "zh-CN",
        content_sha256: "a".repeat(64),
        byte_size: 100,
        status: "ready",
        ingestion_version: "text-v1",
        error: null,
        created_at: "2026-07-25T00:00:00+00:00",
        updated_at: "2026-07-25T00:00:00+00:00",
        processed_at: "2026-07-25T00:00:00+00:00",
      },
      document_chunk: {
        id: "chunk-1",
        document_id: "document-1",
        ordinal: 0,
        content: "2025-03-04，我加入 PersonaOS 项目。",
        content_sha256: "abc",
        char_start: 0,
        char_end: 30,
        line_start: 3,
        line_end: 3,
        locator: { line_start: 3, line_end: 3 },
      },
    },
  ],
};

function json(value: unknown, status = 200): Response {
  return new Response(JSON.stringify(value), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function baseRoutes({
  personas = [persona],
  candidates = [],
  answer,
  authenticated = true,
  setupRequired = false,
}: {
  personas?: Persona[];
  candidates?: MemoryBundle[];
  answer?: AnswerResult;
  authenticated?: boolean;
  setupRequired?: boolean;
} = {}) {
  let signedIn = authenticated;
  const calls: Array<{
    method: string;
    path: string;
    body: unknown;
    csrf: string | null;
  }> = [];
  const fetchMock = vi.fn(
    async (input: RequestInfo | URL, init: RequestInit = {}) => {
      const url = new URL(String(input), "http://test");
      const method = init.method ?? "GET";
      const headers = new Headers(init.headers);
      const body =
        typeof init.body === "string" ? JSON.parse(init.body) : init.body;
      calls.push({
        method,
        path: `${url.pathname}${url.search}`,
        body,
        csrf: headers.get("X-CSRF-Token"),
      });

      if (url.pathname === "/health") {
        return json({
          status: "ok",
          version: "0.12.0",
          runtime: "rules-v1",
          persona_identity_mode: "trusted_local_accounts",
          account_setup_required: setupRequired,
          persona_blob_encryption: "AES-256-GCM",
          persona_embedding_space_id: "space-1",
        });
      }
      if (url.pathname === "/api/v1/auth/status") {
        return json({
          mode: "trusted_local_accounts",
          setup_required: setupRequired,
          cookie_secure: false,
          local_only: true,
        });
      }
      if (url.pathname === "/api/v1/auth/session") {
        return signedIn
          ? json(authenticatedSession)
          : json({ detail: "authentication required" }, 401);
      }
      if (
        url.pathname === "/api/v1/auth/login" &&
        method === "POST"
      ) {
        signedIn = true;
        return json(authenticatedSession);
      }
      if (
        url.pathname === "/api/v1/auth/reauthenticate" &&
        method === "POST"
      ) {
        return json({
          ...authenticatedSession,
          csrf_token: "rotated-csrf-token",
          session: {
            ...authenticatedSession.session,
            reauthenticated_at: "2026-07-25T00:05:00+00:00",
          },
        });
      }
      if (url.pathname === "/api/v1/accounts" && method === "GET") {
        return json([account]);
      }
      if (url.pathname === "/api/v1/accounts" && method === "POST") {
        const payload = body as {
          username: string;
          display_name: string;
          role: "admin" | "member";
        };
        return json(
          {
            ...account,
            id: "member-2",
            username: payload.username,
            display_name: payload.display_name,
            role: payload.role,
          },
          201,
        );
      }
      if (url.pathname === "/api/v1/personas" && method === "GET") {
        return json(personas);
      }
      if (url.pathname === "/api/v1/personas" && method === "POST") {
        const payload = body as {
          display_name: string;
          description: string;
        };
        return json(
          {
            ...persona,
            id: "created-persona",
            display_name: payload.display_name,
            description: payload.description,
          },
          201,
        );
      }
      if (url.pathname.endsWith("/documents") && method === "GET") {
        return json([]);
      }
      if (
        url.pathname.endsWith("/memory-candidates") &&
        method === "GET"
      ) {
        return json(candidates);
      }
      if (url.pathname.endsWith("/memories") && method === "GET") {
        return json([]);
      }
      if (url.pathname.endsWith("/audit-events") && method === "GET") {
        return json([]);
      }
      if (
        url.pathname === "/api/v1/memory-candidates/memory-1/review" &&
        method === "POST"
      ) {
        return json({
          ...candidate,
          memory: { ...candidate.memory, status: "confirmed" },
          current_version: {
            ...candidate.current_version,
            version: 2,
            metadata_snapshot: {
              ...candidate.current_version.metadata_snapshot,
              user_confirmed: true,
            },
          },
          indexing: { created: true, embedding_space_id: "space-1" },
        });
      }
      if (
        url.pathname === `/api/v1/personas/${persona.id}/conversations` &&
        method === "POST"
      ) {
        return json(
          {
            id: "conversation-1",
            persona_id: persona.id,
            title: "加入时间",
            status: "active",
            created_at: "2026-07-25T00:00:00+00:00",
            updated_at: "2026-07-25T00:00:00+00:00",
          },
          201,
        );
      }
      if (
        url.pathname ===
          "/api/v1/conversations/conversation-1/messages" &&
        method === "POST" &&
        answer
      ) {
        return json(answer, 201);
      }
      throw new Error(`Unhandled request: ${method} ${url.pathname}${url.search}`);
    },
  );
  vi.stubGlobal("fetch", fetchMock);
  return { calls, fetchMock };
}

describe("PersonaOS Web", () => {
  beforeEach(() => {
    localStorage.clear();
    vi.clearAllMocks();
  });

  it("logs in before loading protected persona data", async () => {
    const { calls } = baseRoutes({ authenticated: false });
    const user = userEvent.setup();
    render(<App />);

    expect(
      await screen.findByRole("heading", {
        name: "登录本地工作区",
      }),
    ).toBeInTheDocument();
    await user.type(screen.getByLabelText("用户名"), "local-admin");
    await user.type(
      screen.getByLabelText("密码"),
      "test-strong-password-123",
    );
    await user.click(screen.getByRole("button", { name: "登录" }));

    expect(
      await screen.findByRole("heading", { name: "你好，这是 测试人物" }),
    ).toBeInTheDocument();
    const loginCall = calls.find(
      (call) =>
        call.method === "POST" && call.path === "/api/v1/auth/login",
    );
    expect(loginCall?.body).toEqual({
      username: "local-admin",
      password: "test-strong-password-123",
    });
    expect(
      calls.find(
        (call) =>
          call.method === "GET" && call.path === "/api/v1/personas",
      ),
    ).toBeDefined();
  });

  it("shows trusted-host bootstrap instructions without probing data", async () => {
    const { calls } = baseRoutes({
      authenticated: false,
      setupRequired: true,
    });
    render(<App />);

    expect(
      await screen.findByRole("heading", {
        name: "先创建首个管理员",
      }),
    ).toBeInTheDocument();
    expect(screen.getByText(/python -m apps.admin/)).toBeInTheDocument();
    expect(
      calls.some((call) => call.path === "/api/v1/auth/session"),
    ).toBe(false);
    expect(
      calls.some((call) => call.path === "/api/v1/personas"),
    ).toBe(false);
  });

  it("rotates CSRF before an administrator creates an account", async () => {
    const { calls } = baseRoutes();
    const user = userEvent.setup();
    render(<App />);

    await screen.findByRole("heading", { name: "你好，这是 测试人物" });
    await user.click(
      screen.getByRole("button", { name: /账户身份与会话/ }),
    );
    expect(
      await screen.findByRole("heading", { name: "账户与会话" }),
    ).toBeInTheDocument();
    await user.click(
      screen.getByRole("button", { name: "重新验证身份" }),
    );
    await user.type(
      screen.getByLabelText("当前密码"),
      "test-strong-password-123",
    );
    await user.click(
      screen.getByRole("button", { name: "验证并轮换会话" }),
    );
    await waitFor(() =>
      expect(
        screen.queryByRole("dialog", { name: "重新验证身份" }),
      ).not.toBeInTheDocument(),
    );

    await user.type(screen.getByLabelText("用户名"), "new-member");
    await user.type(screen.getByLabelText("显示名称"), "New Member");
    await user.type(
      screen.getByLabelText("初始密码（至少 15 字符）"),
      "another-strong-password-123",
    );
    await user.click(screen.getByRole("button", { name: "创建账户" }));

    expect(await screen.findByText("@new-member")).toBeInTheDocument();
    const accountCreate = calls.find(
      (call) =>
        call.method === "POST" && call.path === "/api/v1/accounts",
    );
    expect(accountCreate?.csrf).toBe("rotated-csrf-token");
  });

  it("creates the first persona and opens its evidence workspace", async () => {
    const { calls } = baseRoutes({ personas: [] });
    const user = userEvent.setup();
    render(<App />);

    expect(
      await screen.findByRole("heading", {
        name: /让记忆有来源/,
      }),
    ).toBeInTheDocument();
    await user.type(screen.getByLabelText("人物名称"), "我的分身");
    await user.type(screen.getByLabelText("人物说明"), "测试边界");
    await user.click(
      screen.getByRole("button", { name: "创建人物空间" }),
    );

    expect(
      await screen.findByRole("heading", {
        name: "你好，这是 我的分身",
      }),
    ).toBeInTheDocument();
    expect(screen.getByText(/不是现实中的本人/)).toBeInTheDocument();
    const createCall = calls.find(
      (call) => call.method === "POST" && call.path === "/api/v1/personas",
    );
    expect(createCall?.body).toEqual({
      display_name: "我的分身",
      description: "测试边界",
    });
    expect(createCall?.csrf).toBe("csrf-test-token");
  });

  it("keeps a candidate behind an explicit human review gate", async () => {
    const { calls } = baseRoutes({ candidates: [candidate] });
    const user = userEvent.setup();
    render(<App />);

    await screen.findByRole("heading", { name: "你好，这是 测试人物" });
    await user.click(screen.getByRole("button", { name: /审核候选判断/ }));

    expect(
      await screen.findByDisplayValue(
        "2025-03-04，我加入 PersonaOS 项目。",
      ),
    ).toBeInTheDocument();
    expect(screen.getByText("career.md")).toBeInTheDocument();
    await user.type(
      screen.getByPlaceholderText("例如：原始资料可验证，表述准确"),
      "原文可验证",
    );
    await user.click(
      screen.getByRole("button", { name: "确认并索引" }),
    );

    expect(
      await screen.findByRole("heading", { name: "没有待审核候选" }),
    ).toBeInTheDocument();
    const reviewCall = calls.find((call) =>
      call.path.includes("/memory-candidates/memory-1/review"),
    );
    expect(reviewCall?.body).toEqual({
      action: "confirm",
      edited_content: null,
      reason: "原文可验证",
    });
  });

  it("renders a resolvable source citation beside the answer", async () => {
    const userMessage = {
      id: "message-user",
      conversation_id: "conversation-1",
      persona_id: persona.id,
      role: "user" as const,
      content: "我什么时候加入 PersonaOS？",
      answer_status: "not_applicable" as const,
      claims: [],
      uncertainty: {},
      simulation_notice: null,
      created_at: "2026-07-25T00:00:00+00:00",
    };
    const assistantMessage = {
      ...userMessage,
      id: "message-assistant",
      role: "assistant" as const,
      content: "你在 2025-03-04 加入 PersonaOS。[C1]",
      answer_status: "answered" as const,
      claims: [{ text: "加入时间", citation_ids: ["C1"] }],
      simulation_notice: persona.simulation_notice,
    };
    const answer: AnswerResult = {
      user_message: userMessage,
      assistant_message: assistantMessage,
      citations: [
        {
          citation: {
            id: "citation-1",
            citation_id: "C1",
            memory_id: "memory-1",
            memory_version_id: "version-2",
            excerpt: "2025-03-04，我加入 PersonaOS 项目。",
            rank: 1,
            claim_indexes: [0],
          },
          memory: {
            id: "memory-1",
            memory_type: "episodic",
            status: "confirmed",
            epistemic_status: "source_verified",
            sensitivity: "private",
            version: 2,
            structured_summary: "加入 PersonaOS 项目",
          },
          evidence: {
            id: "evidence-1",
            relation: "supports",
            excerpt_sha256: "abc",
          },
          source: {
            id: "document-1",
            filename: "career.md",
            media_type: "text/markdown",
            content_sha256: "a".repeat(64),
            locator: { line_start: 3, line_end: 3 },
            excerpt: "2025-03-04，我加入 PersonaOS 项目。",
            chunk_ordinal: 0,
          },
        },
      ],
      retrieval_run: {
        id: "retrieval-1",
        status: "completed",
        candidates: [],
        filters: { memory_status: "confirmed" },
        embedding_space_id: "space-1",
      },
      model_call: {
        id: "model-call-1",
        provider: "local",
        model_name: "evidence-only",
        model_version: "1",
        data_boundary: "local",
        status: "completed",
        latency_ms: 1,
      },
    };
    baseRoutes({ answer });
    const user = userEvent.setup();
    render(<App />);

    await screen.findByRole("heading", { name: "你好，这是 测试人物" });
    await user.click(screen.getByRole("button", { name: /问答引用与不确定性/ }));
    await user.type(
      screen.getByPlaceholderText("询问一件能从已确认资料中回答的事…"),
      "我什么时候加入 PersonaOS？",
    );
    await user.click(screen.getByRole("button", { name: "发送问题" }));

    expect(
      await screen.findByText("你在 2025-03-04 加入 PersonaOS。[C1]"),
    ).toBeInTheDocument();
    const citation = screen.getByText("career.md").closest("details");
    expect(citation).not.toBeNull();
    await user.click(within(citation!).getByText("career.md"));
    expect(
      within(citation!).getByText(
        "2025-03-04，我加入 PersonaOS 项目。",
      ),
    ).toBeInTheDocument();
    expect(screen.getByText("本机边界")).toBeInTheDocument();
  });
});
