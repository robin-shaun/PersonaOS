import { useEffect, useState, type FormEvent } from "react";

import { api } from "../api";
import type { Health, ModelBoundary, Persona } from "../types";
import {
  ErrorState,
  Icon,
  LoadingState,
  PageHeader,
  StatusPill,
  formatDate,
  statusLabel,
  type Notify,
} from "../ui";

interface DashboardPageProps {
  persona: Persona;
  health: Health | null;
  onPersonaUpdated: (persona: Persona) => void;
  onNavigate: (page: string) => void;
  notify: Notify;
}

interface Counts {
  documents: number;
  candidates: number;
  memories: number;
  audits: number;
}

const boundaryCopy: Record<
  ModelBoundary,
  { label: string; detail: string; warning?: string }
> = {
  local: {
    label: "本机模型",
    detail: "可使用公开、私密与受限记忆；原始证据保持在本机。",
  },
  private_network: {
    label: "私有网络",
    detail: "只发送允许的记忆摘要，不发送原始证据；受限记忆被硬过滤。",
  },
  external: {
    label: "外部服务",
    detail: "仅允许公开记忆摘要；不会发送原始文件、定位或证据摘录。",
    warning: "首次启用需要明确确认数据边界。",
  },
};

export function DashboardPage({
  persona,
  health,
  onPersonaUpdated,
  onNavigate,
  notify,
}: DashboardPageProps) {
  const [counts, setCounts] = useState<Counts | null>(null);
  const [error, setError] = useState("");
  const [boundaries, setBoundaries] = useState<ModelBoundary[]>(
    persona.allowed_model_boundaries,
  );
  const [savingPolicy, setSavingPolicy] = useState(false);

  const loadCounts = async () => {
    setError("");
    try {
      const [documents, candidates, memories, audits] = await Promise.all([
        api.listDocuments(persona.id),
        api.listCandidates(persona.id),
        api.listMemories(persona.id),
        api.listAuditEvents(persona.id, 500),
      ]);
      setCounts({
        documents: documents.length,
        candidates: candidates.length,
        memories: memories.length,
        audits: audits.length,
      });
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "读取概览失败");
    }
  };

  useEffect(() => {
    setBoundaries(persona.allowed_model_boundaries);
    setCounts(null);
    void loadCounts();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [persona.id, persona.allowed_model_boundaries.join(",")]);

  const toggleBoundary = (boundary: ModelBoundary) => {
    setBoundaries((current) => {
      if (current.includes(boundary)) {
        return current.length === 1
          ? current
          : current.filter((item) => item !== boundary);
      }
      return [...current, boundary];
    });
  };

  const savePolicy = async (event: FormEvent) => {
    event.preventDefault();
    const enablingExternal =
      boundaries.includes("external") &&
      !persona.allowed_model_boundaries.includes("external");
    if (
      enablingExternal &&
      !window.confirm(
        "外部服务只能接收公开记忆摘要，但数据仍会离开本机。确认启用这个边界吗？",
      )
    ) {
      return;
    }
    setSavingPolicy(true);
    try {
      const updated = await api.updateModelPolicy(
        persona.id,
        boundaries,
        enablingExternal,
      );
      onPersonaUpdated(updated);
      notify("模型数据边界已更新。", "success");
    } catch (saveError) {
      notify(
        saveError instanceof Error ? saveError.message : "边界更新失败",
        "danger",
      );
    } finally {
      setSavingPolicy(false);
    }
  };

  return (
    <div className="page-stack">
      <PageHeader
        eyebrow="人物总览"
        title={`你好，这是 ${persona.display_name}`}
        description="从资料到长期记忆，每一步都保留来源、人工判断和审计记录。"
        actions={
          <button
            className="button button-secondary"
            onClick={() => void loadCounts()}
            type="button"
          >
            <Icon name="refresh" size={17} />
            刷新
          </button>
        }
      />

      <section className="truth-banner">
        <div className="truth-emblem">
          <Icon name="shield" size={25} />
        </div>
        <div>
          <strong>真实性边界</strong>
          <p>{persona.simulation_notice}</p>
        </div>
      </section>

      {error ? (
        <ErrorState message={error} retry={() => void loadCounts()} />
      ) : !counts ? (
        <LoadingState label="正在汇总人物空间…" />
      ) : (
        <section className="stat-grid" aria-label="人物空间统计">
          <button
            className="stat-card"
            onClick={() => onNavigate("documents")}
            type="button"
          >
            <span className="stat-number">{counts.documents}</span>
            <span className="stat-label">份授权资料</span>
            <Icon name="arrow" size={17} />
          </button>
          <button
            className="stat-card stat-card-accent"
            onClick={() => onNavigate("review")}
            type="button"
          >
            <span className="stat-number">{counts.candidates}</span>
            <span className="stat-label">条待审核候选</span>
            <Icon name="arrow" size={17} />
          </button>
          <button
            className="stat-card"
            onClick={() => onNavigate("memories")}
            type="button"
          >
            <span className="stat-number">{counts.memories}</span>
            <span className="stat-label">条已确认记忆</span>
            <Icon name="arrow" size={17} />
          </button>
          <button
            className="stat-card"
            onClick={() => onNavigate("audit")}
            type="button"
          >
            <span className="stat-number">{counts.audits}</span>
            <span className="stat-label">项审计事件</span>
            <Icon name="arrow" size={17} />
          </button>
        </section>
      )}

      <div className="two-column-grid">
        <section className="card">
          <div className="card-heading">
            <div>
              <p className="eyebrow">Identity</p>
              <h2>人物档案</h2>
            </div>
            <StatusPill value={persona.status} />
          </div>
          <dl className="definition-list">
            <div>
              <dt>说明</dt>
              <dd>{persona.description || "尚未填写人物说明"}</dd>
            </div>
            <div>
              <dt>人物 ID</dt>
              <dd className="mono">{persona.id}</dd>
            </div>
            <div>
              <dt>档案版本</dt>
              <dd>v{persona.version}</dd>
            </div>
            <div>
              <dt>创建时间</dt>
              <dd>{formatDate(persona.created_at)}</dd>
            </div>
          </dl>
        </section>

        <section className="card system-card">
          <div className="card-heading">
            <div>
              <p className="eyebrow">Runtime</p>
              <h2>本地运行状态</h2>
            </div>
            <StatusPill
              label={health?.status === "ok" ? "服务正常" : "检查中"}
              value={health?.status ?? "pending"}
            />
          </div>
          <dl className="definition-list">
            <div>
              <dt>Agent Runtime</dt>
              <dd>{health?.runtime ?? "—"}</dd>
            </div>
            <div>
              <dt>原始资料加密</dt>
              <dd>{health?.persona_blob_encryption ?? "—"}</dd>
            </div>
            <div>
              <dt>身份模式</dt>
              <dd>{health?.persona_identity_mode ?? "—"}</dd>
            </div>
            <div>
              <dt>Embedding 空间</dt>
              <dd className="mono compact">
                {health?.persona_embedding_space_id ?? "—"}
              </dd>
            </div>
          </dl>
        </section>
      </div>

      <section className="card policy-card">
        <div className="card-heading">
          <div>
            <p className="eyebrow">Data boundary</p>
            <h2>模型数据边界</h2>
            <p>边界会在检索前硬过滤；勾选并不等于已配置对应模型。</p>
          </div>
        </div>
        <form onSubmit={savePolicy}>
          <div className="policy-options">
            {(Object.keys(boundaryCopy) as ModelBoundary[]).map((boundary) => {
              const item = boundaryCopy[boundary];
              const checked = boundaries.includes(boundary);
              return (
                <label
                  className={`policy-option ${checked ? "is-selected" : ""}`}
                  key={boundary}
                >
                  <input
                    checked={checked}
                    onChange={() => toggleBoundary(boundary)}
                    type="checkbox"
                  />
                  <span className="policy-check" />
                  <span>
                    <strong>{item.label}</strong>
                    <small>{item.detail}</small>
                    {item.warning ? (
                      <em>{item.warning}</em>
                    ) : null}
                  </span>
                </label>
              );
            })}
          </div>
          <div className="form-footer">
            <p>
              当前允许：
              {boundaries.map((item) => statusLabel(item)).join("、")}
            </p>
            <button
              className="button button-primary"
              disabled={
                savingPolicy ||
                JSON.stringify([...boundaries].sort()) ===
                  JSON.stringify([...persona.allowed_model_boundaries].sort())
              }
              type="submit"
            >
              {savingPolicy ? "正在保存…" : "保存边界"}
            </button>
          </div>
        </form>
      </section>
    </div>
  );
}
