import {
  useCallback,
  useEffect,
  useMemo,
  useState,
  type FormEvent,
} from "react";

import { api } from "./api";
import {
  DEMO_PERSONA_DESCRIPTION,
  DEMO_PERSONA_NAME,
  demoFile,
} from "./demo";
import { AuditPage } from "./pages/AuditPage";
import { ChatPage } from "./pages/ChatPage";
import { DashboardPage } from "./pages/DashboardPage";
import { DocumentsPage } from "./pages/DocumentsPage";
import { MemoriesPage } from "./pages/MemoriesPage";
import { ReviewPage } from "./pages/ReviewPage";
import { TasksPage } from "./pages/TasksPage";
import type { Health, Persona } from "./types";
import {
  EmptyState,
  ErrorState,
  Icon,
  type IconName,
  LoadingState,
  LogoMark,
  type NoticeTone,
  StatusPill,
} from "./ui";
import { MILESTONE, VERSION } from "./version";

type Page =
  | "overview"
  | "documents"
  | "review"
  | "memories"
  | "chat"
  | "tasks"
  | "audit";

const selectedPersonaKey = "personaos.selected-persona";

const navigation: Array<{
  id: Page;
  label: string;
  caption: string;
  icon: IconName;
}> = [
  { id: "overview", label: "总览", caption: "身份与边界", icon: "home" },
  { id: "documents", label: "资料", caption: "导入与分块", icon: "file" },
  { id: "review", label: "审核", caption: "候选判断", icon: "review" },
  { id: "memories", label: "记忆", caption: "版本与关系", icon: "memory" },
  { id: "chat", label: "问答", caption: "引用与不确定性", icon: "chat" },
  { id: "tasks", label: "任务", caption: "执行轨迹", icon: "task" },
  { id: "audit", label: "审计", caption: "事件与导出", icon: "audit" },
];

function CreatePersona({
  busy,
  onCreate,
}: {
  busy: boolean;
  onCreate: (name: string, description: string) => void;
}) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (name.trim()) onCreate(name.trim(), description.trim());
  };

  return (
    <form className="create-persona-form" onSubmit={submit}>
      <label className="field">
        <span>人物名称</span>
        <input
          autoComplete="off"
          maxLength={200}
          onChange={(event) => setName(event.target.value)}
          placeholder="例如：我的证据分身"
          required
          value={name}
        />
      </label>
      <label className="field">
        <span>人物说明</span>
        <textarea
          maxLength={10000}
          onChange={(event) => setDescription(event.target.value)}
          placeholder="说明这个人物空间的用途和回答边界"
          rows={3}
          value={description}
        />
      </label>
      <button
        className="button button-primary button-wide"
        disabled={busy || !name.trim()}
        type="submit"
      >
        <Icon name="plus" size={18} />
        {busy ? "正在创建…" : "创建人物空间"}
      </button>
    </form>
  );
}

function Welcome({
  creating,
  launchingDemo,
  onCreate,
  onDemo,
}: {
  creating: boolean;
  launchingDemo: boolean;
  onCreate: (name: string, description: string) => void;
  onDemo: () => void;
}) {
  return (
    <main className="welcome">
      <section className="welcome-hero">
        <p className="eyebrow">Evidence before imitation</p>
        <h1>
          让记忆有来源，
          <br />
          让回答有边界。
        </h1>
        <p>
          PersonaOS 将授权资料、候选、人工确认版本与原始来源分开保存。
          它模拟一个人的知识与表达，但不会声称自己就是现实中的本人。
        </p>
        <ol className="welcome-steps">
          <li>
            <span>1</span>
            创建人物档案
          </li>
          <li>
            <span>2</span>
            导入授权资料
          </li>
          <li>
            <span>3</span>
            人工确认记忆
          </li>
          <li>
            <span>4</span>
            带引用地提问
          </li>
        </ol>
      </section>

      <section className="onboarding-card">
        <header>
          <span className="onboarding-number">01</span>
          <div>
            <h2>创建第一个人物</h2>
            <p>默认仅允许本机模型；之后可以逐项授权其他数据边界。</p>
          </div>
        </header>
        <CreatePersona busy={creating} onCreate={onCreate} />
        <div className="or-divider">
          <span>或者</span>
        </div>
        <button
          className="demo-button"
          disabled={launchingDemo}
          onClick={onDemo}
          type="button"
        >
          <span className="demo-glyph">D</span>
          <span>
            <strong>
              {launchingDemo ? "正在准备演示…" : "载入虚构演示人物"}
            </strong>
            <small>无需 API Key，不调用付费模型</small>
          </span>
          <Icon name="arrow" />
        </button>
      </section>
    </main>
  );
}

export default function App() {
  const [health, setHealth] = useState<Health | null>(null);
  const [personas, setPersonas] = useState<Persona[]>([]);
  const [selectedPersonaId, setSelectedPersonaId] = useState(
    () => localStorage.getItem(selectedPersonaKey) ?? "",
  );
  const [page, setPage] = useState<Page>("overview");
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [creating, setCreating] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);
  const [launchingDemo, setLaunchingDemo] = useState(false);
  const [taskFocusId, setTaskFocusId] = useState("");
  const [notice, setNotice] = useState<{
    message: string;
    tone: NoticeTone;
  } | null>(null);

  const notify = useCallback(
    (message: string, tone: NoticeTone = "info") => {
      setNotice({ message, tone });
    },
    [],
  );

  useEffect(() => {
    if (!notice) return undefined;
    const timer = window.setTimeout(() => setNotice(null), 5000);
    return () => window.clearTimeout(timer);
  }, [notice]);

  const load = async () => {
    setLoading(true);
    setLoadError("");
    try {
      const [healthResult, personaResult] = await Promise.all([
        api.health(),
        api.listPersonas(),
      ]);
      setHealth(healthResult);
      setPersonas(personaResult);
      setSelectedPersonaId((current) => {
        const selectedStillExists = personaResult.some(
          (persona) => persona.id === current,
        );
        const next = selectedStillExists
          ? current
          : (personaResult[0]?.id ?? "");
        if (next) localStorage.setItem(selectedPersonaKey, next);
        return next;
      });
    } catch (error) {
      setLoadError(
        error instanceof Error ? error.message : "无法连接 PersonaOS API",
      );
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
  }, []);

  const selectedPersona = useMemo(
    () =>
      personas.find((persona) => persona.id === selectedPersonaId) ?? null,
    [personas, selectedPersonaId],
  );

  const selectPersona = (personaId: string) => {
    setSelectedPersonaId(personaId);
    localStorage.setItem(selectedPersonaKey, personaId);
    setPage("overview");
    setTaskFocusId("");
  };

  const createPersona = async (name: string, description: string) => {
    setCreating(true);
    try {
      const persona = await api.createPersona(name, description);
      setPersonas((current) => [...current, persona]);
      selectPersona(persona.id);
      setCreateOpen(false);
      notify("人物空间已创建，默认只允许本机模型。", "success");
    } catch (error) {
      notify(error instanceof Error ? error.message : "创建人物失败", "danger");
    } finally {
      setCreating(false);
    }
  };

  const launchDemo = async () => {
    setLaunchingDemo(true);
    try {
      let persona = personas.find(
        (item) => item.display_name === DEMO_PERSONA_NAME,
      );
      if (!persona) {
        persona = await api.createPersona(
          DEMO_PERSONA_NAME,
          DEMO_PERSONA_DESCRIPTION,
        );
        setPersonas((current) => [...current, persona!]);
      }
      const upload = await api.uploadDocument(
        persona.id,
        demoFile(),
        "zh-CN",
      );
      selectPersona(persona.id);
      setPage("documents");
      notify(
        upload.document_created
          ? "虚构演示资料已加密保存，Worker 正在生成候选。"
          : "演示资料已存在，已打开人物空间。",
        "success",
      );
    } catch (error) {
      notify(
        error instanceof Error ? error.message : "演示数据载入失败",
        "danger",
      );
    } finally {
      setLaunchingDemo(false);
    }
  };

  const updatePersona = (updated: Persona) => {
    setPersonas((current) =>
      current.map((persona) =>
        persona.id === updated.id ? updated : persona,
      ),
    );
  };

  const navigate = (target: string) => setPage(target as Page);

  const openTask = (taskId: string) => {
    setTaskFocusId(taskId);
    setPage("tasks");
  };

  if (loading) {
    return (
      <div className="boot-screen">
        <LogoMark />
        <LoadingState label="正在连接本地 PersonaOS…" />
      </div>
    );
  }

  if (loadError) {
    return (
      <div className="boot-screen">
        <LogoMark />
        <ErrorState message={loadError} retry={() => void load()} />
        <p className="boot-hint">
          请确认 API 已在本机启动，或通过 Docker Compose 打开 Web 服务。
        </p>
      </div>
    );
  }

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <LogoMark />
          <span>
            <strong>PersonaOS</strong>
            <small>Evidence Workspace</small>
          </span>
        </div>

        {personas.length > 0 ? (
          <div className="persona-switcher">
            <label className="persona-select">
              <span>当前人物</span>
              <select
                onChange={(event) => selectPersona(event.target.value)}
                value={selectedPersonaId}
              >
                {personas.map((persona) => (
                  <option key={persona.id} value={persona.id}>
                    {persona.display_name}
                  </option>
                ))}
              </select>
            </label>
            <button
              aria-label="创建新人物"
              className="persona-add"
              onClick={() => setCreateOpen(true)}
              type="button"
            >
              <Icon name="plus" size={17} />
            </button>
          </div>
        ) : null}

        <nav aria-label="主导航">
          {navigation.map((item) => (
            <button
              aria-current={page === item.id ? "page" : undefined}
              className={page === item.id ? "is-active" : ""}
              disabled={!selectedPersona}
              key={item.id}
              onClick={() => setPage(item.id)}
              type="button"
            >
              <span className="nav-icon">
                <Icon name={item.icon} size={19} />
              </span>
              <span>
                <strong>{item.label}</strong>
                <small>{item.caption}</small>
              </span>
            </button>
          ))}
        </nav>

        <div className="sidebar-footer">
          <div className="service-state">
            <span className={health?.status === "ok" ? "is-online" : ""} />
            <p>
              <strong>本地服务{health?.status === "ok" ? "正常" : "异常"}</strong>
              <small>{health?.runtime ?? "runtime unknown"}</small>
            </p>
          </div>
          <p className="version">
            {MILESTONE} · v{VERSION}
          </p>
        </div>
      </aside>

      <section className="workspace">
        {personas.length === 0 ? (
          <Welcome
            creating={creating}
            launchingDemo={launchingDemo}
            onCreate={(name, description) =>
              void createPersona(name, description)
            }
            onDemo={() => void launchDemo()}
          />
        ) : selectedPersona ? (
          <main className="page" key={`${selectedPersona.id}:${page}`}>
            {page === "overview" ? (
              <DashboardPage
                health={health}
                notify={notify}
                onNavigate={navigate}
                onPersonaUpdated={updatePersona}
                persona={selectedPersona}
              />
            ) : null}
            {page === "documents" ? (
              <DocumentsPage
                notify={notify}
                onNavigate={navigate}
                onOpenTask={openTask}
                persona={selectedPersona}
              />
            ) : null}
            {page === "review" ? (
              <ReviewPage
                notify={notify}
                onNavigate={navigate}
                persona={selectedPersona}
              />
            ) : null}
            {page === "memories" ? (
              <MemoriesPage notify={notify} persona={selectedPersona} />
            ) : null}
            {page === "chat" ? (
              <ChatPage notify={notify} persona={selectedPersona} />
            ) : null}
            {page === "tasks" ? (
              <TasksPage
                initialTaskId={taskFocusId}
                notify={notify}
                persona={selectedPersona}
              />
            ) : null}
            {page === "audit" ? (
              <AuditPage notify={notify} persona={selectedPersona} />
            ) : null}
          </main>
        ) : (
          <main className="page">
            <EmptyState
              description="重新加载人物列表后再试。"
              title="没有找到所选人物"
            />
          </main>
        )}
      </section>

      {notice ? (
        <div className={`toast toast-${notice.tone}`} role="status">
          <span>{notice.message}</span>
          <button
            aria-label="关闭通知"
            onClick={() => setNotice(null)}
            type="button"
          >
            <Icon name="close" size={16} />
          </button>
        </div>
      ) : null}

      {createOpen ? (
        <div
          className="modal-backdrop"
          onMouseDown={() => !creating && setCreateOpen(false)}
          role="presentation"
        >
          <section
            aria-labelledby="create-persona-title"
            aria-modal="true"
            className="modal create-modal"
            onMouseDown={(event) => event.stopPropagation()}
            role="dialog"
          >
            <button
              aria-label="关闭"
              className="icon-button modal-close"
              disabled={creating}
              onClick={() => setCreateOpen(false)}
              type="button"
            >
              <Icon name="close" />
            </button>
            <span className="create-symbol">
              <Icon name="plus" size={24} />
            </span>
            <h2 id="create-persona-title">创建新人物空间</h2>
            <p className="modal-description">
              每个人物拥有独立资料、记忆、会话和审计边界。
            </p>
            <CreatePersona
              busy={creating}
              onCreate={(name, description) =>
                void createPersona(name, description)
              }
            />
          </section>
        </div>
      ) : null}
    </div>
  );
}
