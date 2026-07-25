import { useEffect, useMemo, useState, type FormEvent } from "react";

import { api } from "../api";
import type {
  MemoryBundle,
  MemoryRelation,
  MemoryRelationKind,
  MemoryStatus,
  Persona,
  Sensitivity,
} from "../types";
import {
  ConfirmDialog,
  EmptyState,
  ErrorState,
  Icon,
  LoadingState,
  PageHeader,
  StatusPill,
  epistemicLabel,
  formatDate,
  locatorLabel,
  memoryTypeLabel,
  shortId,
  statusLabel,
  type Notify,
} from "../ui";

const relationLabels: Record<MemoryRelationKind, string> = {
  supports: "支持",
  conflicts: "冲突",
  derived_from: "派生自",
  supersedes: "取代",
  related_to: "相关",
};

function memoryTitle(bundle: MemoryBundle): string {
  return (
    bundle.current_version.structured_summary ||
    bundle.current_version.raw_content
  );
}

export function MemoriesPage({
  persona,
  notify,
}: {
  persona: Persona;
  notify: Notify;
}) {
  const [status, setStatus] = useState<MemoryStatus>("confirmed");
  const [memories, setMemories] = useState<MemoryBundle[]>([]);
  const [confirmed, setConfirmed] = useState<MemoryBundle[]>([]);
  const [selected, setSelected] = useState<MemoryBundle | null>(null);
  const [relations, setRelations] = useState<MemoryRelation[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const [reindexing, setReindexing] = useState(false);
  const [deleteBusy, setDeleteBusy] = useState(false);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [content, setContent] = useState("");
  const [sensitivity, setSensitivity] = useState<Sensitivity>("private");
  const [reason, setReason] = useState("");
  const [targetId, setTargetId] = useState("");
  const [relationKind, setRelationKind] =
    useState<MemoryRelationKind>("related_to");
  const [relationConfidence, setRelationConfidence] = useState("1");
  const [creatingRelation, setCreatingRelation] = useState(false);

  const load = async (nextStatus = status) => {
    setLoading(true);
    setError("");
    try {
      const visibleRequest = api.listMemories(persona.id, nextStatus);
      const confirmedRequest =
        nextStatus === "confirmed"
          ? visibleRequest
          : api.listMemories(persona.id, "confirmed");
      const [visible, confirmedResult] = await Promise.all([
        visibleRequest,
        confirmedRequest,
      ]);
      setMemories(visible);
      setConfirmed(confirmedResult);
      setSelected((current) => {
        if (current) {
          return (
            visible.find((item) => item.memory.id === current.memory.id) ??
            visible[0] ??
            null
          );
        }
        return visible[0] ?? null;
      });
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "读取记忆失败");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    setSelected(null);
    void load(status);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [persona.id, status]);

  useEffect(() => {
    if (!selected) {
      setContent("");
      setReason("");
      setRelations([]);
      return;
    }
    setContent(selected.current_version.raw_content);
    setSensitivity(selected.memory.sensitivity);
    setReason("");
    setTargetId("");
    if (selected.memory.status === "confirmed") {
      api
        .listMemoryRelations(selected.memory.id)
        .then(setRelations)
        .catch((relationError: unknown) =>
          notify(
            relationError instanceof Error
              ? relationError.message
              : "读取记忆关系失败",
            "danger",
          ),
        );
    } else {
      setRelations([]);
    }
  }, [selected, notify]);

  const memoryById = useMemo(
    () => new Map(confirmed.map((item) => [item.memory.id, item])),
    [confirmed],
  );
  const relationTargets = confirmed.filter(
    (item) => item.memory.id !== selected?.memory.id,
  );
  const changed = Boolean(
    selected &&
      (content.trim() !== selected.current_version.raw_content ||
        sensitivity !== selected.memory.sensitivity),
  );

  const saveMemory = async (event: FormEvent) => {
    event.preventDefault();
    if (!selected || !changed || !reason.trim()) return;
    setSaving(true);
    try {
      const updated = await api.updateMemory(
        selected.memory.id,
        selected.current_version.version,
        content.trim(),
        sensitivity,
        reason.trim(),
      );
      setSelected(updated);
      setMemories((current) =>
        current.map((item) =>
          item.memory.id === updated.memory.id ? updated : item,
        ),
      );
      setConfirmed((current) =>
        current.map((item) =>
          item.memory.id === updated.memory.id ? updated : item,
        ),
      );
      notify("已追加一个不可变记忆版本并更新索引。", "success");
    } catch (saveError) {
      notify(
        saveError instanceof Error ? saveError.message : "保存记忆失败",
        "danger",
      );
    } finally {
      setSaving(false);
    }
  };

  const createRelation = async (event: FormEvent) => {
    event.preventDefault();
    if (!selected || !targetId) return;
    setCreatingRelation(true);
    try {
      const result = await api.createMemoryRelation(
        persona.id,
        selected.memory.id,
        targetId,
        relationKind,
        Number(relationConfidence),
        [],
      );
      setRelations((current) =>
        current.some((item) => item.id === result.relation.id)
          ? current
          : [...current, result.relation],
      );
      setTargetId("");
      notify(
        result.created ? "记忆关系已创建。" : "相同关系已经存在。",
        result.created ? "success" : "info",
      );
    } catch (relationError) {
      notify(
        relationError instanceof Error
          ? relationError.message
          : "创建记忆关系失败",
        "danger",
      );
    } finally {
      setCreatingRelation(false);
    }
  };

  const removeRelation = async (relation: MemoryRelation) => {
    if (!window.confirm(`确认删除“${relationLabels[relation.relation]}”关系？`)) {
      return;
    }
    try {
      await api.deleteMemoryRelation(relation.id);
      setRelations((current) =>
        current.filter((item) => item.id !== relation.id),
      );
      notify("记忆关系已删除。", "success");
    } catch (relationError) {
      notify(
        relationError instanceof Error
          ? relationError.message
          : "删除关系失败",
        "danger",
      );
    }
  };

  const deleteMemory = async () => {
    if (!selected) return;
    setDeleteBusy(true);
    try {
      await api.deleteMemory(selected.memory.id);
      notify("记忆、全部版本、关系、向量和依赖引用已删除。", "success");
      setDeleteOpen(false);
      setSelected(null);
      await load(status);
    } catch (deleteError) {
      notify(
        deleteError instanceof Error ? deleteError.message : "删除记忆失败",
        "danger",
      );
    } finally {
      setDeleteBusy(false);
    }
  };

  const reindex = async () => {
    setReindexing(true);
    try {
      const result = await api.reindexMemories(persona.id);
      notify(
        `重建索引任务 ${shortId(result.task_id)} 已进入队列。`,
        "success",
      );
    } catch (reindexError) {
      notify(
        reindexError instanceof Error ? reindexError.message : "提交重建任务失败",
        "danger",
      );
    } finally {
      setReindexing(false);
    }
  };

  return (
    <div className="page-stack memories-page">
      <PageHeader
        eyebrow="Long-term memory"
        title="长期记忆"
        description="查看当前版本与证据，修订时追加历史；不同 embedding 空间不会混合检索。"
        actions={
          <button
            className="button button-secondary"
            disabled={reindexing}
            onClick={() => void reindex()}
            type="button"
          >
            <Icon name="refresh" size={17} />
            {reindexing ? "已提交…" : "重建当前索引"}
          </button>
        }
      />

      <div className="segmented-control" aria-label="记忆状态筛选">
        {(
          [
            ["confirmed", "已确认"],
            ["rejected", "已拒绝"],
            ["superseded", "已取代"],
          ] as Array<[MemoryStatus, string]>
        ).map(([value, label]) => (
          <button
            aria-pressed={status === value}
            className={status === value ? "is-active" : ""}
            key={value}
            onClick={() => setStatus(value)}
            type="button"
          >
            {label}
          </button>
        ))}
      </div>

      {error ? (
        <ErrorState message={error} retry={() => void load()} />
      ) : loading ? (
        <LoadingState label="正在读取记忆与版本…" />
      ) : memories.length === 0 ? (
        <EmptyState
          description={
            status === "confirmed"
              ? "先在候选审核页确认至少一条记忆。"
              : `当前没有${statusLabel(status)}记忆。`
          }
          icon="memory"
          title={`${statusLabel(status)}列表为空`}
        />
      ) : (
        <div className="memory-workbench">
          <aside className="memory-list" aria-label="记忆列表">
            <div className="list-panel-heading">
              <span>{memories.length} 条记忆</span>
              <small>{statusLabel(status)}</small>
            </div>
            {memories.map((bundle) => (
              <button
                className={`memory-row ${
                  selected?.memory.id === bundle.memory.id ? "is-active" : ""
                }`}
                key={bundle.memory.id}
                onClick={() => setSelected(bundle)}
                type="button"
              >
                <span className="memory-kind">
                  {memoryTypeLabel(bundle.memory.memory_type)}
                </span>
                <strong>{memoryTitle(bundle)}</strong>
                <span>
                  v{bundle.current_version.version} ·{" "}
                  {epistemicLabel(bundle.memory.epistemic_status)}
                </span>
              </button>
            ))}
          </aside>

          <section className="memory-detail">
            {selected ? (
              <>
                <header className="memory-detail-header">
                  <div>
                    <div className="tag-row">
                      <span className="tag tag-dark">
                        {memoryTypeLabel(selected.memory.memory_type)}
                      </span>
                      <span className="tag">
                        {epistemicLabel(selected.memory.epistemic_status)}
                      </span>
                      <StatusPill value={selected.memory.sensitivity} />
                    </div>
                    <h2>{memoryTitle(selected)}</h2>
                    <p className="mono">
                      {shortId(selected.memory.id, 18)} · 当前版本 v
                      {selected.current_version.version}
                    </p>
                  </div>
                  <button
                    aria-label="删除记忆"
                    className="icon-button danger"
                    onClick={() => setDeleteOpen(true)}
                    type="button"
                  >
                    <Icon name="trash" />
                  </button>
                </header>

                {selected.memory.status === "confirmed" ? (
                  <form className="memory-editor" onSubmit={saveMemory}>
                    <label className="field">
                      <span>当前内容</span>
                      <textarea
                        maxLength={20000}
                        onChange={(event) => setContent(event.target.value)}
                        rows={5}
                        value={content}
                      />
                    </label>
                    <div className="form-row">
                      <label className="field">
                        <span>敏感等级</span>
                        <select
                          onChange={(event) =>
                            setSensitivity(event.target.value as Sensitivity)
                          }
                          value={sensitivity}
                        >
                          <option value="public">公开</option>
                          <option value="private">私密</option>
                          <option value="restricted">受限</option>
                        </select>
                      </label>
                      <label className="field field-grow">
                        <span>修改理由（必填）</span>
                        <input
                          maxLength={4000}
                          onChange={(event) => setReason(event.target.value)}
                          placeholder="说明为什么需要追加这个版本"
                          value={reason}
                        />
                      </label>
                    </div>
                    <div className="form-footer">
                      <p>
                        乐观锁基于 v{selected.current_version.version}
                        ，并发修改会返回冲突而非覆盖。
                      </p>
                      <button
                        className="button button-primary"
                        disabled={
                          saving || !changed || !reason.trim() || !content.trim()
                        }
                        type="submit"
                      >
                        {saving ? "正在保存…" : "追加新版本"}
                      </button>
                    </div>
                  </form>
                ) : (
                  <div className="readonly-memory">
                    <p>{selected.current_version.raw_content}</p>
                    <small>
                      {statusLabel(selected.memory.status)}记忆为只读审计记录。
                    </small>
                  </div>
                )}

                <section className="memory-section">
                  <div className="subheading">
                    <h3>证据来源</h3>
                    <span>{selected.evidence.length} 个来源定位</span>
                  </div>
                  {selected.evidence.length === 0 ? (
                    <p className="muted">当前版本不再逐字绑定原始证据。</p>
                  ) : (
                    selected.evidence.map((item) => (
                      <article className="source-card" key={item.evidence.id}>
                        <header>
                          <span className="source-mark">S</span>
                          <span>
                            <strong>
                              {item.source_document.original_filename}
                            </strong>
                            <small>
                              {locatorLabel(item.document_chunk.locator)} ·{" "}
                              {item.evidence.relation}
                            </small>
                          </span>
                        </header>
                        <blockquote>{item.evidence.excerpt}</blockquote>
                      </article>
                    ))
                  )}
                </section>

                <section className="memory-section">
                  <div className="subheading">
                    <h3>版本历史</h3>
                    <span>{selected.versions.length} 个不可变版本</span>
                  </div>
                  <ol className="version-timeline">
                    {[...selected.versions].reverse().map((version) => (
                      <li key={version.id}>
                        <span className="timeline-dot" />
                        <div>
                          <header>
                            <strong>版本 {version.version}</strong>
                            <time>{formatDate(version.created_at)}</time>
                          </header>
                          <p>{version.raw_content}</p>
                          <small>
                            {version.change_reason || "初始候选版本"} ·{" "}
                            {version.created_by_type}
                          </small>
                        </div>
                      </li>
                    ))}
                  </ol>
                </section>

                {selected.memory.status === "confirmed" ? (
                  <section className="memory-section">
                    <div className="subheading">
                      <h3>记忆关系</h3>
                      <span>支持、冲突、派生与取代</span>
                    </div>
                    {relations.length > 0 ? (
                      <div className="relation-list">
                        {relations.map((relation) => {
                          const outgoing =
                            relation.from_memory_id === selected.memory.id;
                          const otherId = outgoing
                            ? relation.to_memory_id
                            : relation.from_memory_id;
                          const other = memoryById.get(otherId);
                          return (
                            <article className="relation-row" key={relation.id}>
                              <span className="relation-direction">
                                {outgoing ? "本记忆 →" : "→ 本记忆"}
                              </span>
                              <span className="relation-kind">
                                {relationLabels[relation.relation]}
                              </span>
                              <span className="relation-target">
                                {other ? memoryTitle(other) : shortId(otherId, 14)}
                              </span>
                              <small>
                                {Math.round(relation.confidence * 100)}%
                              </small>
                              <button
                                aria-label="删除关系"
                                className="icon-button"
                                onClick={() => void removeRelation(relation)}
                                type="button"
                              >
                                <Icon name="close" size={16} />
                              </button>
                            </article>
                          );
                        })}
                      </div>
                    ) : (
                      <p className="muted">这条记忆尚未建立关系。</p>
                    )}
                    {relationTargets.length > 0 ? (
                      <form className="relation-form" onSubmit={createRelation}>
                        <label>
                          <span>关系</span>
                          <select
                            onChange={(event) =>
                              setRelationKind(
                                event.target.value as MemoryRelationKind,
                              )
                            }
                            value={relationKind}
                          >
                            {(
                              Object.entries(relationLabels) as Array<
                                [MemoryRelationKind, string]
                              >
                            ).map(([value, label]) => (
                              <option key={value} value={value}>
                                {label}
                              </option>
                            ))}
                          </select>
                        </label>
                        <label className="field-grow">
                          <span>目标记忆</span>
                          <select
                            onChange={(event) => setTargetId(event.target.value)}
                            value={targetId}
                          >
                            <option value="">请选择</option>
                            {relationTargets.map((item) => (
                              <option
                                key={item.memory.id}
                                value={item.memory.id}
                              >
                                {memoryTitle(item).slice(0, 80)}
                              </option>
                            ))}
                          </select>
                        </label>
                        <label>
                          <span>置信度</span>
                          <input
                            max="1"
                            min="0"
                            onChange={(event) =>
                              setRelationConfidence(event.target.value)
                            }
                            step="0.05"
                            type="number"
                            value={relationConfidence}
                          />
                        </label>
                        <button
                          aria-label="添加关系"
                          className="button button-secondary"
                          disabled={creatingRelation || !targetId}
                          type="submit"
                        >
                          <Icon name="link" size={17} />
                          添加
                        </button>
                      </form>
                    ) : null}
                  </section>
                ) : null}
              </>
            ) : null}
          </section>
        </div>
      )}

      <ConfirmDialog
        busy={deleteBusy}
        description={
          <>
            将永久删除这条记忆的全部版本、证据、关系与所有 embedding
            空间中的向量；依赖它的回答会被擦除。此操作不会删除整份来源资料。
          </>
        }
        onClose={() => !deleteBusy && setDeleteOpen(false)}
        onConfirm={() => void deleteMemory()}
        open={deleteOpen}
        title="删除这条长期记忆？"
      />
    </div>
  );
}
