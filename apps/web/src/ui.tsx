import type { ReactNode } from "react";

export type NoticeTone = "success" | "warning" | "danger" | "info";
export type Notify = (message: string, tone?: NoticeTone) => void;

export type IconName =
  | "home"
  | "file"
  | "review"
  | "memory"
  | "chat"
  | "task"
  | "audit"
  | "plus"
  | "refresh"
  | "trash"
  | "arrow"
  | "shield"
  | "download"
  | "link"
  | "close";

const iconPaths: Record<IconName, ReactNode> = {
  home: (
    <>
      <path d="M3 10.8 12 3l9 7.8" />
      <path d="M5.5 9.8V21h13V9.8M9 21v-7h6v7" />
    </>
  ),
  file: (
    <>
      <path d="M6 2.5h8l4 4V21.5H6z" />
      <path d="M14 2.5v5h4M9 12h6M9 16h6" />
    </>
  ),
  review: (
    <>
      <path d="M5 3.5h14v17H5zM8.5 8h7M8.5 12h4" />
      <path d="m9 16 1.6 1.6L15 13.2" />
    </>
  ),
  memory: (
    <>
      <path d="M8 4.3a3 3 0 0 0-3 3 3.4 3.4 0 0 0 .5 1.7A3.5 3.5 0 0 0 7 15.5V18a2.5 2.5 0 0 0 5 0V6.5a2.5 2.5 0 0 0-4-2.2Z" />
      <path d="M16 4.3a3 3 0 0 1 3 3 3.4 3.4 0 0 1-.5 1.7 3.5 3.5 0 0 1-1.5 6.5V18a2.5 2.5 0 0 1-5 0V6.5a2.5 2.5 0 0 1 4-2.2ZM8 9h4m0 5h5" />
    </>
  ),
  chat: (
    <>
      <path d="M3.5 4.5h17v12h-10l-5 4v-4h-2z" />
      <path d="M7.5 9h9M7.5 12.5h6" />
    </>
  ),
  task: (
    <>
      <path d="M8 4h12v16H8zM4 8h4M4 12h4M4 16h4" />
      <path d="m11 11 1.5 1.5L17 8" />
    </>
  ),
  audit: (
    <>
      <path d="M12 2.8 19 6v5.2c0 4.6-2.8 8.2-7 10-4.2-1.8-7-5.4-7-10V6z" />
      <path d="M9 12h6M12 9v6" />
    </>
  ),
  plus: <path d="M12 5v14M5 12h14" />,
  refresh: (
    <>
      <path d="M19 7V3l-2 2a8 8 0 1 0 2.1 8" />
      <path d="M19 3h-4" />
    </>
  ),
  trash: (
    <>
      <path d="M5 7h14M9 7V4h6v3M7 7l1 14h8l1-14M10 11v6M14 11v6" />
    </>
  ),
  arrow: <path d="m9 5 7 7-7 7" />,
  shield: (
    <>
      <path d="M12 2.8 19 6v5.2c0 4.6-2.8 8.2-7 10-4.2-1.8-7-5.4-7-10V6z" />
      <path d="m9 12 2 2 4-5" />
    </>
  ),
  download: (
    <>
      <path d="M12 3v12m-4-4 4 4 4-4" />
      <path d="M5 19h14" />
    </>
  ),
  link: (
    <>
      <path d="m10 13 4-4" />
      <path d="M8.5 16.5 7 18a3.5 3.5 0 0 1-5-5l3-3a3.5 3.5 0 0 1 5 0M15.5 7.5 17 6a3.5 3.5 0 0 1 5 5l-3 3a3.5 3.5 0 0 1-5 0" />
    </>
  ),
  close: <path d="m6 6 12 12M18 6 6 18" />,
};

export function Icon({
  name,
  size = 20,
}: {
  name: IconName;
  size?: number;
}) {
  return (
    <svg
      aria-hidden="true"
      className="icon"
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.7"
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      {iconPaths[name]}
    </svg>
  );
}

export function LogoMark() {
  return (
    <svg
      aria-hidden="true"
      className="logo-mark"
      viewBox="0 0 44 44"
      fill="none"
    >
      <path
        d="M22 3.5c7.2 0 13 5.8 13 13 0 3.7-1.5 7-4 9.4V36l-9 4.5-9-4.5V25.9a12.9 12.9 0 0 1-4-9.4c0-7.2 5.8-13 13-13Z"
        fill="currentColor"
      />
      <path
        d="M15 16.5c2.1-4.2 11.9-4.2 14 0M16.5 23c3.5 2.6 7.5 2.6 11 0"
        stroke="var(--ink)"
        strokeWidth="2"
        strokeLinecap="round"
      />
      <circle cx="17.5" cy="18.5" r="1.2" fill="var(--ink)" />
      <circle cx="26.5" cy="18.5" r="1.2" fill="var(--ink)" />
    </svg>
  );
}

export function PageHeader({
  eyebrow,
  title,
  description,
  actions,
}: {
  eyebrow: string;
  title: string;
  description: string;
  actions?: ReactNode;
}) {
  return (
    <header className="page-header">
      <div>
        <p className="eyebrow">{eyebrow}</p>
        <h1>{title}</h1>
        <p className="page-description">{description}</p>
      </div>
      {actions ? <div className="page-actions">{actions}</div> : null}
    </header>
  );
}

export function StatusPill({
  value,
  label,
}: {
  value: string;
  label?: string;
}) {
  const normalized = value.toLowerCase().replaceAll("_", "-");
  return (
    <span className={`status-pill status-${normalized}`}>
      <span className="status-dot" />
      {label ?? statusLabel(value)}
    </span>
  );
}

export function LoadingState({ label = "正在读取…" }: { label?: string }) {
  return (
    <div className="state-panel" role="status">
      <span className="spinner" />
      <p>{label}</p>
    </div>
  );
}

export function EmptyState({
  icon = "memory",
  title,
  description,
  action,
}: {
  icon?: IconName;
  title: string;
  description: string;
  action?: ReactNode;
}) {
  return (
    <div className="state-panel empty-state">
      <span className="empty-icon">
        <Icon name={icon} size={26} />
      </span>
      <h3>{title}</h3>
      <p>{description}</p>
      {action}
    </div>
  );
}

export function ErrorState({
  message,
  retry,
}: {
  message: string;
  retry?: () => void;
}) {
  return (
    <div className="state-panel error-state" role="alert">
      <strong>没有完成这次读取</strong>
      <p>{message}</p>
      {retry ? (
        <button className="button button-secondary" onClick={retry} type="button">
          <Icon name="refresh" size={17} />
          重试
        </button>
      ) : null}
    </div>
  );
}

export function ConfirmDialog({
  open,
  title,
  description,
  confirmLabel = "确认删除",
  busy = false,
  onConfirm,
  onClose,
}: {
  open: boolean;
  title: string;
  description: ReactNode;
  confirmLabel?: string;
  busy?: boolean;
  onConfirm: () => void;
  onClose: () => void;
}) {
  if (!open) return null;
  return (
    <div className="modal-backdrop" role="presentation" onMouseDown={onClose}>
      <section
        aria-labelledby="confirm-dialog-title"
        aria-modal="true"
        className="modal"
        onMouseDown={(event) => event.stopPropagation()}
        role="dialog"
      >
        <button
          aria-label="关闭"
          className="icon-button modal-close"
          disabled={busy}
          onClick={onClose}
          type="button"
        >
          <Icon name="close" />
        </button>
        <span className="danger-symbol">
          <Icon name="trash" size={24} />
        </span>
        <h2 id="confirm-dialog-title">{title}</h2>
        <div className="modal-description">{description}</div>
        <div className="modal-actions">
          <button
            className="button button-ghost"
            disabled={busy}
            onClick={onClose}
            type="button"
          >
            取消
          </button>
          <button
            className="button button-danger"
            disabled={busy}
            onClick={onConfirm}
            type="button"
          >
            {busy ? "正在删除…" : confirmLabel}
          </button>
        </div>
      </section>
    </div>
  );
}

export function JsonDetails({
  value,
  label = "查看结构化详情",
}: {
  value: unknown;
  label?: string;
}) {
  return (
    <details className="json-details">
      <summary>{label}</summary>
      <pre>{JSON.stringify(value, null, 2)}</pre>
    </details>
  );
}

export function formatDate(value?: string | null): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

export function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes < 0) return "—";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KiB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MiB`;
}

export function shortId(value?: string | null, length = 8): string {
  if (!value) return "—";
  return value.length > length ? `${value.slice(0, length)}…` : value;
}

export function statusLabel(value: string): string {
  const labels: Record<string, string> = {
    active: "启用",
    uploaded: "已上传",
    processing: "处理中",
    ready: "已就绪",
    failed: "失败",
    pending: "排队中",
    running: "执行中",
    completed: "已完成",
    awaiting_approval: "待审批",
    cancelling: "取消中",
    cancelled: "已取消",
    candidate: "待审核",
    confirmed: "已确认",
    rejected: "已拒绝",
    superseded: "已取代",
    answered: "已回答",
    no_memory: "无相关记忆",
    local: "本机",
    private_network: "私有网络",
    external: "外部服务",
    public: "公开",
    private: "私密",
    restricted: "受限",
  };
  return labels[value] ?? value.replaceAll("_", " ");
}

export function memoryTypeLabel(value: string): string {
  const labels: Record<string, string> = {
    episodic: "情景记忆",
    semantic: "语义记忆",
    procedural: "程序性记忆",
    preference: "偏好记忆",
    relationship: "关系记忆",
    reflection: "反思记忆",
  };
  return labels[value] ?? value;
}

export function epistemicLabel(value: string): string {
  const labels: Record<string, string> = {
    user_asserted: "用户陈述",
    source_verified: "资料可定位",
    model_summary: "模型总结",
    model_inference: "模型推断",
    user_rule: "用户设定",
  };
  return labels[value] ?? value;
}

export function locatorLabel(locator: Record<string, unknown>): string {
  const lineStart = locator.line_start;
  const lineEnd = locator.line_end;
  if (typeof lineStart === "number" && typeof lineEnd === "number") {
    return lineStart === lineEnd
      ? `第 ${lineStart} 行`
      : `第 ${lineStart}–${lineEnd} 行`;
  }
  const charStart = locator.char_start;
  const charEnd = locator.char_end;
  if (typeof charStart === "number" && typeof charEnd === "number") {
    return `字符 ${charStart}–${charEnd}`;
  }
  return "稳定来源定位";
}

export function downloadJson(filename: string, value: unknown) {
  const blob = new Blob([JSON.stringify(value, null, 2)], {
    type: "application/json;charset=utf-8",
  });
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  anchor.click();
  URL.revokeObjectURL(url);
}
