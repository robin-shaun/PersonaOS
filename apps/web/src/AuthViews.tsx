import { useState, type FormEvent } from "react";

import { api, ApiError } from "./api";
import type { AuthenticatedSession } from "./types";
import { Icon, LogoMark } from "./ui";

export function AuthenticationGate({
  setupRequired,
  busy,
  error,
  onLogin,
}: {
  setupRequired: boolean;
  busy: boolean;
  error: string;
  onLogin: (username: string, password: string) => void;
}) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");

  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!username.trim() || !password) return;
    onLogin(username.trim(), password);
  };

  return (
    <main className="auth-screen">
      <section className="auth-intro">
        <LogoMark />
        <p className="eyebrow">Trusted local accounts</p>
        <h1>你的资料，先经过身份边界。</h1>
        <p>
          PersonaOS 使用本地账户、可撤销会话和操作审计隔离人物空间。
          浏览器不会接收可伪造的 owner 参数。
        </p>
      </section>
      <section className="auth-card">
        {setupRequired ? (
          <>
            <span className="auth-symbol">
              <Icon name="shield" size={25} />
            </span>
            <h2>先创建首个管理员</h2>
            <p>
              为避免未授权的网络初始化，首个账户只能从可信主机命令行创建。
            </p>
            <pre className="setup-command">
              python -m apps.admin create-account --username admin
              {" \\\n  "}--display-name Administrator --role admin
            </pre>
            <p className="auth-note">
              命令会安全提示输入密码；Docker Compose 用法见 README。
            </p>
          </>
        ) : (
          <>
            <span className="auth-symbol">
              <Icon name="shield" size={25} />
            </span>
            <h2>登录本地工作区</h2>
            <p>凭据只提交给当前 PersonaOS API，不会写入浏览器存储。</p>
            <form className="auth-form" onSubmit={submit}>
              <label className="field">
                <span>用户名</span>
                <input
                  autoCapitalize="none"
                  autoComplete="username"
                  maxLength={64}
                  onChange={(event) => setUsername(event.target.value)}
                  required
                  value={username}
                />
              </label>
              <label className="field">
                <span>密码</span>
                <input
                  autoComplete="current-password"
                  maxLength={1024}
                  onChange={(event) => setPassword(event.target.value)}
                  required
                  type="password"
                  value={password}
                />
              </label>
              {error ? (
                <div className="inline-alert danger" role="alert">
                  <strong>登录失败</strong>
                  <span>{error}</span>
                </div>
              ) : null}
              <button
                className="button button-primary button-wide"
                disabled={busy || !username.trim() || !password}
                type="submit"
              >
                <Icon name="shield" size={17} />
                {busy ? "正在验证…" : "登录"}
              </button>
            </form>
          </>
        )}
      </section>
    </main>
  );
}

export function ReauthenticationDialog({
  open,
  onClose,
  onSuccess,
}: {
  open: boolean;
  onClose: () => void;
  onSuccess: (session: AuthenticatedSession) => void;
}) {
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  if (!open) return null;

  const close = () => {
    setPassword("");
    setError("");
    onClose();
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const session = await api.reauthenticate(password);
      setPassword("");
      onSuccess(session);
    } catch (caught) {
      setError(
        caught instanceof ApiError && caught.status === 401
          ? "密码不正确。"
          : caught instanceof Error
            ? caught.message
            : "重新验证失败",
      );
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="modal-backdrop" role="presentation">
      <section
        aria-labelledby="reauthentication-title"
        aria-modal="true"
        className="modal reauthentication-modal"
        role="dialog"
      >
        <button
          aria-label="关闭"
          className="icon-button modal-close"
          disabled={busy}
          onClick={close}
          type="button"
        >
          <Icon name="close" />
        </button>
        <span className="auth-symbol">
          <Icon name="shield" size={24} />
        </span>
        <h2 id="reauthentication-title">重新验证身份</h2>
        <p className="modal-description">
          删除、原始资料导出、外部模型授权和账户管理需要近期密码验证。
          成功后会轮换会话令牌。
        </p>
        <form className="auth-form" onSubmit={(event) => void submit(event)}>
          <label className="field">
            <span>当前密码</span>
            <input
              autoComplete="current-password"
              autoFocus
              maxLength={1024}
              onChange={(event) => setPassword(event.target.value)}
              required
              type="password"
              value={password}
            />
          </label>
          {error ? (
            <div className="inline-alert danger" role="alert">
              <span>{error}</span>
            </div>
          ) : null}
          <div className="modal-actions">
            <button
              className="button button-ghost"
              disabled={busy}
              onClick={close}
              type="button"
            >
              取消
            </button>
            <button
              className="button button-primary"
              disabled={busy || !password}
              type="submit"
            >
              {busy ? "正在验证…" : "验证并轮换会话"}
            </button>
          </div>
        </form>
      </section>
    </div>
  );
}
