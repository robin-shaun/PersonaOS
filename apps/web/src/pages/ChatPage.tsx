import {
  useEffect,
  useRef,
  useState,
  type FormEvent,
  type KeyboardEvent,
} from "react";

import { api, ApiError } from "../api";
import type {
  CitationBundle,
  ConversationMessage,
  ModelBoundary,
  Persona,
} from "../types";
import {
  EmptyState,
  Icon,
  LoadingState,
  PageHeader,
  StatusPill,
  epistemicLabel,
  formatDate,
  locatorLabel,
  memoryTypeLabel,
  statusLabel,
  type Notify,
} from "../ui";

const conversationKey = (personaId: string) =>
  `personaos.conversation.${personaId}`;

export function ChatPage({
  persona,
  notify,
}: {
  persona: Persona;
  notify: Notify;
}) {
  const [conversationId, setConversationId] = useState("");
  const [messages, setMessages] = useState<ConversationMessage[]>([]);
  const [citations, setCitations] = useState<
    Record<string, CitationBundle[]>
  >({});
  const [question, setQuestion] = useState("");
  const [topK, setTopK] = useState(5);
  const [loadingHistory, setLoadingHistory] = useState(true);
  const [asking, setAsking] = useState(false);
  const [lastBoundary, setLastBoundary] = useState<ModelBoundary | null>(null);
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let cancelled = false;
    const savedId = localStorage.getItem(conversationKey(persona.id)) ?? "";
    setConversationId(savedId);
    setMessages([]);
    setCitations({});
    setLastBoundary(null);
    if (!savedId) {
      setLoadingHistory(false);
      return () => {
        cancelled = true;
      };
    }

    setLoadingHistory(true);
    api
      .listMessages(savedId)
      .then(async (history) => {
        const assistantMessages = history.filter(
          (message) =>
            message.role === "assistant" &&
            message.answer_status === "answered",
        );
        const citationPairs = await Promise.all(
          assistantMessages.map(async (message) => [
            message.id,
            await api.getCitations(message.id),
          ]),
        );
        if (!cancelled) {
          setMessages(history);
          setCitations(Object.fromEntries(citationPairs));
        }
      })
      .catch((historyError: unknown) => {
        if (historyError instanceof ApiError && historyError.status === 404) {
          localStorage.removeItem(conversationKey(persona.id));
          if (!cancelled) setConversationId("");
          return;
        }
        if (!cancelled) {
          notify(
            historyError instanceof Error
              ? historyError.message
              : "读取会话失败",
            "danger",
          );
        }
      })
      .finally(() => {
        if (!cancelled) setLoadingHistory(false);
      });
    return () => {
      cancelled = true;
    };
  }, [persona.id, notify]);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, asking]);

  const startNew = () => {
    localStorage.removeItem(conversationKey(persona.id));
    setConversationId("");
    setMessages([]);
    setCitations({});
    setLastBoundary(null);
    notify("已开始一个新的本地会话。", "info");
  };

  const ask = async (event?: FormEvent) => {
    event?.preventDefault();
    const normalized = question.trim();
    if (!normalized || asking) return;
    setAsking(true);
    try {
      let activeConversation = conversationId;
      if (!activeConversation) {
        const conversation = await api.createConversation(
          persona.id,
          normalized.slice(0, 80),
        );
        activeConversation = conversation.id;
        setConversationId(activeConversation);
        localStorage.setItem(
          conversationKey(persona.id),
          activeConversation,
        );
      }
      const result = await api.askQuestion(
        activeConversation,
        normalized,
        topK,
      );
      setMessages((current) => [
        ...current,
        result.user_message,
        result.assistant_message,
      ]);
      setCitations((current) => ({
        ...current,
        [result.assistant_message.id]: result.citations,
      }));
      setLastBoundary(result.model_call.data_boundary);
      setQuestion("");
    } catch (askError) {
      notify(
        askError instanceof Error ? askError.message : "提问失败",
        "danger",
      );
    } finally {
      setAsking(false);
    }
  };

  const onQuestionKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      void ask();
    }
  };

  return (
    <div className="page-stack chat-page">
      <PageHeader
        eyebrow="Evidence-grounded Q&A"
        title={`与 ${persona.display_name} 对话`}
        description="系统只使用当前已确认且符合数据边界的记忆；证据不足时会明确拒绝补造个人事实。"
        actions={
          <button
            className="button button-secondary"
            disabled={!conversationId && messages.length === 0}
            onClick={startNew}
            type="button"
          >
            <Icon name="plus" size={17} />
            新会话
          </button>
        }
      />

      <section className="chat-boundary">
        <Icon name="shield" size={18} />
        <span>{persona.simulation_notice}</span>
        {lastBoundary ? (
          <StatusPill
            label={`${statusLabel(lastBoundary)}边界`}
            value={lastBoundary}
          />
        ) : null}
      </section>

      <section className="conversation-panel" aria-live="polite">
        {loadingHistory ? (
          <LoadingState label="正在恢复本地会话…" />
        ) : messages.length === 0 ? (
          <EmptyState
            description="例如：“我什么时候开始参与潮汐笔记？” 系统会把回答与记忆、版本及原始资料定位一起展示。"
            icon="chat"
            title="从一条可验证的问题开始"
          />
        ) : (
          <div className="message-list">
            {messages.map((message) => {
              const messageCitations = citations[message.id] ?? [];
              return (
                <article
                  className={`message message-${message.role}`}
                  key={message.id}
                >
                  <header>
                    <span className="message-avatar">
                      {message.role === "user"
                        ? "你"
                        : persona.display_name.slice(0, 1)}
                    </span>
                    <span>
                      <strong>
                        {message.role === "user"
                          ? "你"
                          : persona.display_name}
                      </strong>
                      <time>{formatDate(message.created_at)}</time>
                    </span>
                    {message.role === "assistant" ? (
                      <StatusPill value={message.answer_status} />
                    ) : null}
                  </header>
                  <div className="message-content">{message.content}</div>

                  {message.role === "assistant" &&
                  message.answer_status === "no_memory" ? (
                    <div className="no-evidence-note">
                      <Icon name="shield" size={17} />
                      没有召回可引用的已确认记忆，本次没有调用回答模型补全事实。
                    </div>
                  ) : null}

                  {messageCitations.length > 0 ? (
                    <div className="citation-stack">
                      <p className="citation-heading">
                        <Icon name="link" size={16} />
                        {messageCitations.length} 条可解析引用
                      </p>
                      {messageCitations.map((item) => (
                        <details
                          className="citation-card"
                          key={item.citation.id}
                        >
                          <summary>
                            <span className="citation-id">
                              {item.citation.citation_id}
                            </span>
                            <span>
                              <strong>{item.source.filename}</strong>
                              <small>
                                {locatorLabel(item.source.locator)} · 记忆 v
                                {item.memory.version}
                              </small>
                            </span>
                            <Icon name="arrow" size={17} />
                          </summary>
                          <div className="citation-body">
                            <div className="tag-row">
                              <span className="tag tag-dark">
                                {memoryTypeLabel(item.memory.memory_type)}
                              </span>
                              <span className="tag">
                                {epistemicLabel(item.memory.epistemic_status)}
                              </span>
                              <span className="tag">
                                {statusLabel(item.memory.sensitivity)}
                              </span>
                            </div>
                            <p>{item.memory.structured_summary}</p>
                            <blockquote>{item.source.excerpt}</blockquote>
                            <dl>
                              <div>
                                <dt>证据关系</dt>
                                <dd>{item.evidence.relation}</dd>
                              </div>
                              <div>
                                <dt>来源哈希</dt>
                                <dd className="mono">
                                  {item.source.content_sha256.slice(0, 16)}…
                                </dd>
                              </div>
                            </dl>
                          </div>
                        </details>
                      ))}
                    </div>
                  ) : null}
                </article>
              );
            })}
            {asking ? (
              <div className="thinking-row" role="status">
                <span className="spinner" />
                正在检索已确认记忆并校验引用…
              </div>
            ) : null}
            <div ref={endRef} />
          </div>
        )}
      </section>

      <form className="composer" onSubmit={(event) => void ask(event)}>
        <label className="composer-input">
          <span className="sr-only">向人物提问</span>
          <textarea
            maxLength={10000}
            onChange={(event) => setQuestion(event.target.value)}
            onKeyDown={onQuestionKeyDown}
            placeholder="询问一件能从已确认资料中回答的事…"
            rows={2}
            value={question}
          />
          <small>Enter 发送 · Shift + Enter 换行</small>
        </label>
        <label className="top-k-control">
          <span>召回</span>
          <select
            onChange={(event) => setTopK(Number(event.target.value))}
            value={topK}
          >
            {[3, 5, 8, 10].map((value) => (
              <option key={value} value={value}>
                {value} 条
              </option>
            ))}
          </select>
        </label>
        <button
          aria-label="发送问题"
          className="send-button"
          disabled={asking || !question.trim()}
          type="submit"
        >
          <Icon name="arrow" size={21} />
        </button>
      </form>
    </div>
  );
}
