import { useEffect, useState } from "react";

import { api } from "../api";
import type { MemoryBundle, Persona } from "../types";
import {
  EmptyState,
  ErrorState,
  Icon,
  LoadingState,
  PageHeader,
  StatusPill,
  epistemicLabel,
  locatorLabel,
  memoryTypeLabel,
  type Notify,
} from "../ui";

function CandidateCard({
  candidate,
  busy,
  onReview,
}: {
  candidate: MemoryBundle;
  busy: boolean;
  onReview: (
    candidate: MemoryBundle,
    action: "confirm" | "reject",
    content: string,
    reason: string,
  ) => void;
}) {
  const [content, setContent] = useState(candidate.current_version.raw_content);
  const [reason, setReason] = useState("");
  const [expanded, setExpanded] = useState(false);
  const evidence = candidate.evidence[0];
  const changed = content.trim() !== candidate.current_version.raw_content;
  const sourceBound =
    candidate.current_version.metadata_snapshot.source_bound === true;

  return (
    <article className="candidate-card">
      <header className="candidate-header">
        <div className="tag-row">
          <span className="tag tag-dark">
            {memoryTypeLabel(candidate.memory.memory_type)}
          </span>
          <span className="tag">
            {epistemicLabel(candidate.memory.epistemic_status)}
          </span>
          <span className="tag">
            置信度 {Math.round(candidate.memory.confidence * 100)}%
          </span>
        </div>
        <StatusPill value={candidate.memory.status} />
      </header>

      <div className="candidate-body">
        <label className="field">
          <span>
            候选内容
            {changed ? <em>已修改，确认后将保存新版本</em> : null}
          </span>
          <textarea
            maxLength={20000}
            onChange={(event) => setContent(event.target.value)}
            rows={4}
            value={content}
          />
        </label>

        {evidence ? (
          <section className="evidence-box">
            <button
              aria-expanded={expanded}
              className="evidence-heading"
              onClick={() => setExpanded((value) => !value)}
              type="button"
            >
              <span className="source-mark">S</span>
              <span>
                <strong>{evidence.source_document.original_filename}</strong>
                <small>
                  {locatorLabel(evidence.document_chunk.locator)} ·{" "}
                  {sourceBound ? "逐字绑定原文" : "派生自原文"}
                </small>
              </span>
              <Icon name={expanded ? "close" : "arrow"} size={17} />
            </button>
            {expanded ? (
              <blockquote>{evidence.evidence.excerpt}</blockquote>
            ) : null}
          </section>
        ) : (
          <div className="inline-alert danger">
            <strong>没有可解析的来源</strong>
            <span>不应确认这条候选。</span>
          </div>
        )}

        <label className="field">
          <span>审核理由（建议填写）</span>
          <input
            maxLength={4000}
            onChange={(event) => setReason(event.target.value)}
            placeholder="例如：原始资料可验证，表述准确"
            value={reason}
          />
        </label>
      </div>

      <footer className="candidate-actions">
        <button
          className="button button-ghost danger-text"
          disabled={busy}
          onClick={() => onReview(candidate, "reject", content, reason)}
          type="button"
        >
          拒绝候选
        </button>
        <button
          className="button button-primary"
          disabled={busy || !content.trim() || !evidence}
          onClick={() => onReview(candidate, "confirm", content, reason)}
          type="button"
        >
          <Icon name="review" size={17} />
          {busy ? "正在保存…" : changed ? "修订后确认" : "确认并索引"}
        </button>
      </footer>
    </article>
  );
}

export function ReviewPage({
  persona,
  notify,
  onNavigate,
}: {
  persona: Persona;
  notify: Notify;
  onNavigate: (page: string) => void;
}) {
  const [candidates, setCandidates] = useState<MemoryBundle[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [busyId, setBusyId] = useState("");
  const [reviewedCount, setReviewedCount] = useState(0);

  const load = async () => {
    setLoading(true);
    setError("");
    try {
      setCandidates(await api.listCandidates(persona.id));
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "读取候选失败");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    setReviewedCount(0);
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [persona.id]);

  const review = async (
    candidate: MemoryBundle,
    action: "confirm" | "reject",
    content: string,
    reason: string,
  ) => {
    setBusyId(candidate.memory.id);
    try {
      const edited =
        action === "confirm" &&
        content.trim() !== candidate.current_version.raw_content
          ? content.trim()
          : undefined;
      await api.reviewCandidate(
        candidate.memory.id,
        action,
        edited,
        reason,
      );
      setCandidates((current) =>
        current.filter((item) => item.memory.id !== candidate.memory.id),
      );
      setReviewedCount((count) => count + 1);
      notify(
        action === "confirm"
          ? "记忆已确认并进入当前 embedding 空间。"
          : "候选已拒绝，原始资料仍然保留。",
        "success",
      );
    } catch (reviewError) {
      notify(
        reviewError instanceof Error ? reviewError.message : "审核失败",
        "danger",
      );
    } finally {
      setBusyId("");
    }
  };

  return (
    <div className="page-stack review-page">
      <PageHeader
        eyebrow="Human review gate"
        title="候选审核"
        description="长期记忆必须由人确认。修订会追加版本并保留原候选与来源，不会覆盖历史。"
        actions={
          <button
            className="button button-secondary"
            onClick={() => void load()}
            type="button"
          >
            <Icon name="refresh" size={17} />
            刷新候选
          </button>
        }
      />

      <section className="review-guide">
        <span className="step-number">01</span>
        <p>
          <strong>核对事实与原文</strong>
          “资料可定位”只说明文字能回到来源，不代表来源陈述一定客观真实。
        </p>
        <span className="step-number">02</span>
        <p>
          <strong>必要时修订</strong>
          人工改写会明确标为用户陈述，并通过派生关系保留证据链。
        </p>
        <span className="step-number">03</span>
        <p>
          <strong>确认后才可检索</strong>
          待审核和已拒绝内容不会进入人物问答。
        </p>
      </section>

      {reviewedCount > 0 ? (
        <div className="inline-alert success" role="status">
          <strong>本次已处理 {reviewedCount} 条</strong>
          <span>每次判断都已写入审计记录。</span>
        </div>
      ) : null}

      {error ? (
        <ErrorState message={error} retry={() => void load()} />
      ) : loading ? (
        <LoadingState label="正在读取待审核记忆…" />
      ) : candidates.length === 0 ? (
        <EmptyState
          action={
            <div className="empty-actions">
              <button
                className="button button-secondary"
                onClick={() => onNavigate("documents")}
                type="button"
              >
                导入更多资料
              </button>
              <button
                className="button button-primary"
                onClick={() => onNavigate("memories")}
                type="button"
              >
                查看已确认记忆
              </button>
            </div>
          }
          description="Worker 完成资料处理后，新的候选会出现在这里。"
          icon="review"
          title="没有待审核候选"
        />
      ) : (
        <section className="candidate-feed">
          <div className="feed-heading">
            <span>{candidates.length} 条等待判断</span>
            <small>逐条审核 · 不支持自动批量确认</small>
          </div>
          {candidates.map((candidate) => (
            <CandidateCard
              busy={busyId === candidate.memory.id}
              candidate={candidate}
              key={candidate.memory.id}
              onReview={(item, action, content, reason) =>
                void review(item, action, content, reason)
              }
            />
          ))}
        </section>
      )}
    </div>
  );
}
