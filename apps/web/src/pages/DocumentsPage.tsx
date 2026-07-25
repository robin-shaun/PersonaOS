import {
  useEffect,
  useRef,
  useState,
  type ChangeEvent,
  type FormEvent,
} from "react";

import { api } from "../api";
import type {
  DocumentBundle,
  Persona,
  SourceDocument,
} from "../types";
import {
  ConfirmDialog,
  EmptyState,
  ErrorState,
  Icon,
  LoadingState,
  PageHeader,
  StatusPill,
  formatBytes,
  formatDate,
  shortId,
  type Notify,
} from "../ui";

export function DocumentsPage({
  persona,
  notify,
  onNavigate,
  onOpenTask,
}: {
  persona: Persona;
  notify: Notify;
  onNavigate: (page: string) => void;
  onOpenTask: (taskId: string) => void;
}) {
  const [documents, setDocuments] = useState<SourceDocument[]>([]);
  const [selected, setSelected] = useState<DocumentBundle | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [uploading, setUploading] = useState(false);
  const [file, setFile] = useState<File | null>(null);
  const [language, setLanguage] = useState("zh-CN");
  const [deleting, setDeleting] = useState<SourceDocument | null>(null);
  const [deleteBusy, setDeleteBusy] = useState(false);
  const fileInput = useRef<HTMLInputElement>(null);

  const loadDocuments = async (quiet = false) => {
    if (!quiet) setLoading(true);
    setError("");
    try {
      const result = await api.listDocuments(persona.id);
      setDocuments(result);
      if (selected) {
        const stillPresent = result.find(
          (document) => document.id === selected.document.id,
        );
        if (!stillPresent) {
          setSelected(null);
        } else if (
          stillPresent.status !== selected.document.status ||
          stillPresent.updated_at !== selected.document.updated_at
        ) {
          setSelected(await api.getDocument(stillPresent.id));
        }
      }
    } catch (loadError) {
      setError(loadError instanceof Error ? loadError.message : "读取资料失败");
    } finally {
      if (!quiet) setLoading(false);
    }
  };

  useEffect(() => {
    setSelected(null);
    void loadDocuments();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [persona.id]);

  const hasActiveDocument = documents.some((document) =>
    ["uploaded", "processing"].includes(document.status),
  );

  useEffect(() => {
    if (!hasActiveDocument) return undefined;
    const timer = window.setInterval(() => {
      void loadDocuments(true);
    }, 2000);
    return () => window.clearInterval(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hasActiveDocument, persona.id]);

  const chooseFile = (event: ChangeEvent<HTMLInputElement>) => {
    setFile(event.target.files?.[0] ?? null);
  };

  const upload = async (event: FormEvent) => {
    event.preventDefault();
    if (!file) {
      notify("请选择一份 .txt 或 .md 文件。", "warning");
      return;
    }
    setUploading(true);
    try {
      const result = await api.uploadDocument(
        persona.id,
        file,
        language.trim() || undefined,
      );
      notify(
        result.document_created
          ? "资料已加密保存并进入处理队列。"
          : "相同内容已经导入，本次复用了已有资料。",
        result.document_created ? "success" : "info",
      );
      setFile(null);
      if (fileInput.current) fileInput.current.value = "";
      await loadDocuments(true);
      setSelected(await api.getDocument(result.document.id));
    } catch (uploadError) {
      notify(
        uploadError instanceof Error ? uploadError.message : "资料导入失败",
        "danger",
      );
    } finally {
      setUploading(false);
    }
  };

  const openDocument = async (document: SourceDocument) => {
    try {
      setSelected(await api.getDocument(document.id));
    } catch (openError) {
      notify(
        openError instanceof Error ? openError.message : "读取资料详情失败",
        "danger",
      );
    }
  };

  const confirmDelete = async () => {
    if (!deleting) return;
    setDeleteBusy(true);
    try {
      await api.deleteDocument(deleting.id);
      notify("资料及其派生记忆、索引和回答引用已删除。", "success");
      setDeleting(null);
      setSelected(null);
      await loadDocuments(true);
    } catch (deleteError) {
      notify(
        deleteError instanceof Error ? deleteError.message : "资料删除失败",
        "danger",
      );
    } finally {
      setDeleteBusy(false);
    }
  };

  return (
    <div className="page-stack">
      <PageHeader
        eyebrow="Knowledge ingestion"
        title="资料导入"
        description="原文加密保存，分块位置稳定；导入只生成候选，不会自动成为长期记忆。"
        actions={
          <button
            className="button button-secondary"
            onClick={() => void loadDocuments()}
            type="button"
          >
            <Icon name="refresh" size={17} />
            刷新状态
          </button>
        }
      />

      <section className="upload-panel">
        <div className="upload-copy">
          <span className="upload-icon">
            <Icon name="file" size={26} />
          </span>
          <div>
            <h2>添加授权文本</h2>
            <p>支持 UTF-8 的 .txt / .md，单文件上限由本地服务配置。</p>
          </div>
        </div>
        <form className="upload-form" onSubmit={upload}>
          <label className="file-picker">
            <input
              accept=".txt,.md,text/plain,text/markdown"
              onChange={chooseFile}
              ref={fileInput}
              type="file"
            />
            <span>{file ? file.name : "选择文件"}</span>
            <small>{file ? formatBytes(file.size) : "资料只发送给本地 API"}</small>
          </label>
          <label className="compact-field">
            <span>语言</span>
            <input
              maxLength={40}
              onChange={(event) => setLanguage(event.target.value)}
              placeholder="zh-CN"
              value={language}
            />
          </label>
          <button
            className="button button-primary"
            disabled={uploading || !file}
            type="submit"
          >
            <Icon name="plus" size={17} />
            {uploading ? "正在上传…" : "导入并处理"}
          </button>
        </form>
      </section>

      {error ? (
        <ErrorState message={error} retry={() => void loadDocuments()} />
      ) : loading ? (
        <LoadingState label="正在读取资料清单…" />
      ) : documents.length === 0 ? (
        <EmptyState
          description="上传一份文本资料，系统会保存原文、稳定切分并生成待审核记忆候选。"
          icon="file"
          title="还没有授权资料"
        />
      ) : (
        <div className="master-detail">
          <section className="list-panel" aria-label="资料清单">
            <div className="list-panel-heading">
              <span>{documents.length} 份资料</span>
              {hasActiveDocument ? (
                <small>
                  <span className="live-dot" /> 自动刷新中
                </small>
              ) : null}
            </div>
            <div className="document-list">
              {documents.map((document) => (
                <button
                  className={`document-row ${
                    selected?.document.id === document.id ? "is-active" : ""
                  }`}
                  key={document.id}
                  onClick={() => void openDocument(document)}
                  type="button"
                >
                  <span className="document-glyph">
                    {document.original_filename.split(".").pop()?.toUpperCase()}
                  </span>
                  <span className="document-main">
                    <strong>{document.original_filename}</strong>
                    <small>
                      {formatBytes(document.byte_size)} ·{" "}
                      {formatDate(document.created_at)}
                    </small>
                  </span>
                  <StatusPill value={document.status} />
                </button>
              ))}
            </div>
          </section>

          <section className="detail-panel">
            {!selected ? (
              <EmptyState
                description="选择左侧资料查看处理状态、来源哈希和稳定分块。"
                icon="arrow"
                title="选择一份资料"
              />
            ) : (
              <>
                <div className="detail-heading">
                  <div>
                    <StatusPill value={selected.document.status} />
                    <h2>{selected.document.original_filename}</h2>
                    <p>
                      内容哈希{" "}
                      <span className="mono">
                        {shortId(selected.document.content_sha256, 16)}
                      </span>
                    </p>
                  </div>
                  <button
                    aria-label="删除资料"
                    className="icon-button danger"
                    onClick={() => setDeleting(selected.document)}
                    type="button"
                  >
                    <Icon name="trash" />
                  </button>
                </div>

                {selected.document.error ? (
                  <div className="inline-alert danger">
                    <strong>处理失败</strong>
                    <span>{selected.document.error}</span>
                  </div>
                ) : null}

                <dl className="metadata-grid">
                  <div>
                    <dt>媒体类型</dt>
                    <dd>{selected.document.media_type}</dd>
                  </div>
                  <div>
                    <dt>语言</dt>
                    <dd>{selected.document.language ?? "未指定"}</dd>
                  </div>
                  <div>
                    <dt>处理版本</dt>
                    <dd>{selected.document.ingestion_version}</dd>
                  </div>
                  <div>
                    <dt>分块数量</dt>
                    <dd>{selected.chunks.length}</dd>
                  </div>
                </dl>

                {selected.document.task_id ? (
                  <button
                    className="trace-link"
                    onClick={() => onOpenTask(selected.document.task_id!)}
                    type="button"
                  >
                    <span>
                      <small>处理任务</small>
                      <strong className="mono">
                        {shortId(selected.document.task_id, 14)}
                      </strong>
                    </span>
                    <Icon name="arrow" size={18} />
                  </button>
                ) : null}

                {selected.document.status === "ready" ? (
                  <button
                    className="button button-primary button-wide"
                    onClick={() => onNavigate("review")}
                    type="button"
                  >
                    前往候选审核
                    <Icon name="arrow" size={17} />
                  </button>
                ) : null}

                <div className="chunk-list">
                  <div className="subheading">
                    <h3>原文分块</h3>
                    <span>逐字保留 · 可稳定定位</span>
                  </div>
                  {selected.chunks.length === 0 ? (
                    <p className="muted">
                      {selected.document.status === "ready"
                        ? "这份资料没有产生可用分块。"
                        : "Worker 完成处理后会在这里显示分块。"}
                    </p>
                  ) : (
                    selected.chunks.map((chunk) => (
                      <article className="chunk-card" key={chunk.id}>
                        <header>
                          <span>片段 {chunk.ordinal + 1}</span>
                          <span>
                            行 {chunk.line_start}–{chunk.line_end}
                          </span>
                        </header>
                        <p>{chunk.content}</p>
                      </article>
                    ))
                  )}
                </div>
              </>
            )}
          </section>
        </div>
      )}

      <ConfirmDialog
        busy={deleteBusy}
        description={
          <>
            将永久删除 <strong>{deleting?.original_filename}</strong> 的加密
            Blob、分块、派生记忆与索引，并擦除依赖它的回答引用。审计只保留无正文墓碑。
          </>
        }
        onClose={() => !deleteBusy && setDeleting(null)}
        onConfirm={() => void confirmDelete()}
        open={Boolean(deleting)}
        title="删除这份来源资料？"
      />
    </div>
  );
}
