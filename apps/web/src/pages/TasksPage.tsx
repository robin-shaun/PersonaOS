import { useEffect, useMemo, useState } from "react";

import { api } from "../api";
import type { Persona, Task, TaskBundle } from "../types";
import {
  EmptyState,
  ErrorState,
  Icon,
  JsonDetails,
  LoadingState,
  PageHeader,
  StatusPill,
  formatDate,
  shortId,
  type Notify,
} from "../ui";

function valueOf(record: Record<string, unknown>, key: string): string {
  const value = record[key];
  return value === null || value === undefined ? "—" : String(value);
}

export function TasksPage({
  persona,
  initialTaskId,
  notify,
}: {
  persona: Persona;
  initialTaskId: string;
  notify: Notify;
}) {
  const [tasks, setTasks] = useState<Task[]>([]);
  const [selectedId, setSelectedId] = useState(initialTaskId);
  const [bundle, setBundle] = useState<TaskBundle | null>(null);
  const [showAll, setShowAll] = useState(false);
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [error, setError] = useState("");
  const [acting, setActing] = useState(false);

  const visibleTasks = useMemo(
    () =>
      showAll
        ? tasks
        : tasks.filter((task) => task.input.persona_id === persona.id),
    [tasks, showAll, persona.id],
  );

  const loadTasks = async (quiet = false) => {
    if (!quiet) setLoading(true);
    setError("");
    try {
      setTasks(await api.listTasks());
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "读取任务失败");
    } finally {
      if (!quiet) setLoading(false);
    }
  };

  const loadDetail = async (taskId: string, quiet = false) => {
    if (!quiet) setDetailLoading(true);
    try {
      setBundle(await api.getTask(taskId));
      setSelectedId(taskId);
    } catch (detailError) {
      notify(
        detailError instanceof Error ? detailError.message : "读取任务轨迹失败",
        "danger",
      );
    } finally {
      if (!quiet) setDetailLoading(false);
    }
  };

  useEffect(() => {
    void loadTasks();
  }, []);

  useEffect(() => {
    if (initialTaskId) void loadDetail(initialTaskId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialTaskId]);

  useEffect(() => {
    if (!selectedId || bundle?.task.id === selectedId) return;
    void loadDetail(selectedId);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedId]);

  const selectedIsActive =
    bundle &&
    ["pending", "running", "cancelling"].includes(bundle.task.status);

  useEffect(() => {
    if (!selectedIsActive || !selectedId) return undefined;
    const timer = window.setInterval(() => {
      void Promise.all([loadTasks(true), loadDetail(selectedId, true)]);
    }, 2000);
    return () => window.clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedIsActive, selectedId]);

  const cancel = async () => {
    if (!bundle) return;
    if (!window.confirm("确认取消这个尚未交付的任务？")) return;
    setActing(true);
    try {
      const updated = await api.cancelTask(
        bundle.task.id,
        "用户从 Web 管理端取消",
      );
      setBundle(updated);
      await loadTasks(true);
      notify("取消请求已记录。", "success");
    } catch (cancelError) {
      notify(
        cancelError instanceof Error ? cancelError.message : "取消任务失败",
        "danger",
      );
    } finally {
      setActing(false);
    }
  };

  const retry = async () => {
    if (!bundle) return;
    setActing(true);
    try {
      const updated = await api.retryTask(bundle.task.id);
      setBundle(updated);
      await loadTasks(true);
      notify("失败任务已重新进入队列。", "success");
    } catch (retryError) {
      notify(
        retryError instanceof Error ? retryError.message : "重试任务失败",
        "danger",
      );
    } finally {
      setActing(false);
    }
  };

  return (
    <div className="page-stack tasks-page">
      <PageHeader
        eyebrow="Task trace"
        title="任务与执行轨迹"
        description="查看异步导入、重建索引和数字员工任务的队列、运行、审批与结果记录。"
        actions={
          <button
            className="button button-secondary"
            onClick={() => void loadTasks()}
            type="button"
          >
            <Icon name="refresh" size={17} />
            刷新任务
          </button>
        }
      />

      <label className="toggle-row">
        <input
          checked={showAll}
          onChange={(event) => setShowAll(event.target.checked)}
          type="checkbox"
        />
        <span className="toggle-control" />
        <span>
          <strong>显示全部本地任务</strong>
          <small>默认仅显示当前人物的资料与记忆任务</small>
        </span>
      </label>

      {error ? (
        <ErrorState message={error} retry={() => void loadTasks()} />
      ) : loading ? (
        <LoadingState label="正在读取任务队列…" />
      ) : visibleTasks.length === 0 ? (
        <EmptyState
          description="导入资料或重建记忆索引后，任务会出现在这里。"
          icon="task"
          title="当前没有相关任务"
        />
      ) : (
        <div className="task-workbench">
          <aside className="task-list">
            <div className="list-panel-heading">
              <span>{visibleTasks.length} 个任务</span>
              <small>{showAll ? "全部" : "当前人物"}</small>
            </div>
            {visibleTasks.map((task) => (
              <button
                className={`task-row ${
                  selectedId === task.id ? "is-active" : ""
                }`}
                key={task.id}
                onClick={() => void loadDetail(task.id)}
                type="button"
              >
                <span>
                  <strong>{task.workflow_name}</strong>
                  <small>
                    {shortId(task.id, 12)} · {formatDate(task.created_at)}
                  </small>
                </span>
                <StatusPill value={task.status} />
              </button>
            ))}
          </aside>

          <section className="task-detail">
            {detailLoading ? (
              <LoadingState label="正在读取完整执行轨迹…" />
            ) : !bundle ? (
              <EmptyState
                description="选择左侧任务查看队列状态和每一步记录。"
                icon="arrow"
                title="选择一个任务"
              />
            ) : (
              <>
                <header className="task-detail-header">
                  <div>
                    <StatusPill value={bundle.task.status} />
                    <h2>{bundle.task.workflow_name}</h2>
                    <p className="mono">{bundle.task.id}</p>
                  </div>
                  <div className="task-actions">
                    {["pending", "running", "cancelling"].includes(
                      bundle.task.status,
                    ) ? (
                      <button
                        className="button button-ghost danger-text"
                        disabled={acting}
                        onClick={() => void cancel()}
                        type="button"
                      >
                        取消任务
                      </button>
                    ) : null}
                    {bundle.task.status === "failed" ? (
                      <button
                        className="button button-primary"
                        disabled={acting}
                        onClick={() => void retry()}
                        type="button"
                      >
                        重试任务
                      </button>
                    ) : null}
                  </div>
                </header>

                <dl className="metadata-grid">
                  <div>
                    <dt>Employee</dt>
                    <dd>{bundle.task.employee_id}</dd>
                  </div>
                  <div>
                    <dt>创建时间</dt>
                    <dd>{formatDate(bundle.task.created_at)}</dd>
                  </div>
                  <div>
                    <dt>更新时间</dt>
                    <dd>{formatDate(bundle.task.updated_at)}</dd>
                  </div>
                  <div>
                    <dt>运行次数</dt>
                    <dd>{bundle.runs.length}</dd>
                  </div>
                </dl>

                <section className="task-section">
                  <div className="subheading">
                    <h3>队列</h3>
                    <span>{bundle.queue_jobs.length} 条记录</span>
                  </div>
                  <div className="compact-records">
                    {bundle.queue_jobs.map((job, index) => (
                      <article key={valueOf(job, "id") + index}>
                        <StatusPill value={valueOf(job, "status")} />
                        <span>
                          <strong className="mono">
                            {shortId(valueOf(job, "id"), 14)}
                          </strong>
                          <small>
                            尝试 {valueOf(job, "attempts")} /{" "}
                            {valueOf(job, "max_attempts")}
                          </small>
                        </span>
                      </article>
                    ))}
                  </div>
                </section>

                {bundle.workflow_runs.length > 0 ? (
                  <section className="task-section">
                    <div className="subheading">
                      <h3>Workflow</h3>
                      <span>{bundle.workflow_runs.length} 次执行</span>
                    </div>
                    <div className="compact-records">
                      {bundle.workflow_runs.map((run, index) => (
                        <article key={valueOf(run, "id") + index}>
                          <StatusPill value={valueOf(run, "status")} />
                          <span>
                            <strong>
                              当前步骤 {valueOf(run, "current_step")}
                            </strong>
                            <small className="mono">
                              {shortId(valueOf(run, "id"), 14)}
                            </small>
                          </span>
                        </article>
                      ))}
                    </div>
                  </section>
                ) : null}

                {bundle.approvals.length > 0 ? (
                  <section className="task-section">
                    <div className="subheading">
                      <h3>人工审批</h3>
                      <span>{bundle.approvals.length} 项</span>
                    </div>
                    <div className="compact-records">
                      {bundle.approvals.map((approval, index) => (
                        <article key={valueOf(approval, "id") + index}>
                          <StatusPill value={valueOf(approval, "status")} />
                          <span>
                            <strong>{valueOf(approval, "type")}</strong>
                            <small>{valueOf(approval, "decision")}</small>
                          </span>
                        </article>
                      ))}
                    </div>
                  </section>
                ) : null}

                <JsonDetails label="查看安全任务输入" value={bundle.task.input} />
                {bundle.task.final_output ? (
                  <JsonDetails
                    label="查看最终结构化结果"
                    value={bundle.task.final_output}
                  />
                ) : null}
              </>
            )}
          </section>
        </div>
      )}
    </div>
  );
}
