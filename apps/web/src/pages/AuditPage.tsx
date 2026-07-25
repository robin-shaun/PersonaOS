import { useEffect, useMemo, useState, type FormEvent } from "react";

import { api } from "../api";
import type { AuditEvent, Persona } from "../types";
import {
  EmptyState,
  ErrorState,
  Icon,
  JsonDetails,
  LoadingState,
  PageHeader,
  StatusPill,
  downloadJson,
  formatDate,
  shortId,
  type Notify,
} from "../ui";

export function AuditPage({
  persona,
  notify,
}: {
  persona: Persona;
  notify: Notify;
}) {
  const [events, setEvents] = useState<AuditEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [query, setQuery] = useState("");
  const [actionFilter, setActionFilter] = useState("all");
  const [includeRaw, setIncludeRaw] = useState(false);
  const [exporting, setExporting] = useState(false);

  const load = async () => {
    setLoading(true);
    setError("");
    try {
      setEvents(await api.listAuditEvents(persona.id, 500));
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "读取审计失败");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [persona.id]);

  const actionGroups = useMemo(
    () =>
      Array.from(new Set(events.map((event) => event.action.split(".")[0]))).sort(),
    [events],
  );

  const visible = useMemo(() => {
    const normalized = query.trim().toLowerCase();
    return events.filter((event) => {
      const groupMatches =
        actionFilter === "all" || event.action.startsWith(`${actionFilter}.`);
      const queryMatches =
        !normalized ||
        [
          event.action,
          event.resource_type,
          event.resource_id,
          event.request_id ?? "",
        ].some((value) => value.toLowerCase().includes(normalized));
      return groupMatches && queryMatches;
    });
  }, [events, actionFilter, query]);

  const exportData = async (event: FormEvent) => {
    event.preventDefault();
    if (
      includeRaw &&
      !window.confirm(
        "本次导出将包含解密后的原始资料。请只保存到可信位置。确认继续吗？",
      )
    ) {
      return;
    }
    setExporting(true);
    try {
      const result = await api.exportPersona(persona.id, includeRaw);
      const safeName =
        persona.display_name.replace(/[^\p{L}\p{N}-]+/gu, "-") || "persona";
      downloadJson(
        `${safeName}-export-${result.manifest.sha256.slice(0, 8)}.json`,
        result,
      );
      notify(
        `导出已生成并校验：${result.manifest.byte_size.toLocaleString()} 字节。`,
        "success",
      );
      await load();
    } catch (exportError) {
      notify(
        exportError instanceof Error ? exportError.message : "导出失败",
        "danger",
      );
    } finally {
      setExporting(false);
    }
  };

  return (
    <div className="page-stack audit-page">
      <PageHeader
        eyebrow="Audit & portability"
        title="审计与导出"
        description="重要操作以追加事件记录；删除事件只保留哈希和级联计数，不残留记忆正文。"
        actions={
          <button
            className="button button-secondary"
            onClick={() => void load()}
            type="button"
          >
            <Icon name="refresh" size={17} />
            刷新事件
          </button>
        }
      />

      <section className="export-card">
        <div>
          <span className="export-icon">
            <Icon name="download" size={24} />
          </span>
          <div>
            <h2>可校验 JSON 导出</h2>
            <p>
              包含人物、记忆版本、来源定位与审计；不导出向量数组或内部 object key。
            </p>
          </div>
        </div>
        <form onSubmit={exportData}>
          <label className="toggle-row compact-toggle">
            <input
              checked={includeRaw}
              onChange={(event) => setIncludeRaw(event.target.checked)}
              type="checkbox"
            />
            <span className="toggle-control" />
            <span>
              <strong>包含原始资料</strong>
              <small>导出中将出现解密后的原文</small>
            </span>
          </label>
          <button
            className="button button-primary"
            disabled={exporting}
            type="submit"
          >
            <Icon name="download" size={17} />
            {exporting ? "正在生成…" : "生成导出"}
          </button>
        </form>
      </section>

      <section className="audit-toolbar">
        <label className="search-field">
          <span className="sr-only">搜索审计事件</span>
          <input
            onChange={(event) => setQuery(event.target.value)}
            placeholder="搜索 action、资源或 request ID"
            value={query}
          />
        </label>
        <label>
          <span className="sr-only">按事件域筛选</span>
          <select
            onChange={(event) => setActionFilter(event.target.value)}
            value={actionFilter}
          >
            <option value="all">全部事件域</option>
            {actionGroups.map((group) => (
              <option key={group} value={group}>
                {group}
              </option>
            ))}
          </select>
        </label>
        <span className="result-count">{visible.length} 条</span>
      </section>

      {error ? (
        <ErrorState message={error} retry={() => void load()} />
      ) : loading ? (
        <LoadingState label="正在读取追加式审计记录…" />
      ) : events.length === 0 ? (
        <EmptyState
          description="创建人物后的重要操作会依次记录在这里。"
          icon="audit"
          title="还没有审计事件"
        />
      ) : visible.length === 0 ? (
        <EmptyState
          description="调整关键词或事件域筛选后再试。"
          icon="audit"
          title="没有匹配的事件"
        />
      ) : (
        <section className="audit-stream" aria-label="审计事件">
          {visible.map((event) => (
            <article className="audit-row" key={event.id}>
              <span className={`audit-node risk-${event.risk_level}`} />
              <div className="audit-event-main">
                <header>
                  <strong>{event.action}</strong>
                  <time>{formatDate(event.occurred_at)}</time>
                </header>
                <p>
                  {event.resource_type} ·{" "}
                  <span className="mono">{shortId(event.resource_id, 18)}</span>
                </p>
                <div className="tag-row">
                  <StatusPill value={event.outcome} />
                  <span className={`tag risk-tag risk-${event.risk_level}`}>
                    {event.risk_level} risk
                  </span>
                  <span className="tag">
                    {event.actor_type}:{event.actor_id}
                  </span>
                  {event.request_id ? (
                    <span className="tag mono">
                      req {shortId(event.request_id, 14)}
                    </span>
                  ) : null}
                </div>
                {Object.keys(event.detail).length > 0 ? (
                  <JsonDetails label="事件详情" value={event.detail} />
                ) : null}
              </div>
            </article>
          ))}
        </section>
      )}
    </div>
  );
}
