import { useEffect, useState, type FormEvent } from "react";

import { api, ApiError } from "../api";
import type { Account, AuthenticatedSession } from "../types";
import {
  ErrorState,
  Icon,
  LoadingState,
  PageHeader,
  StatusPill,
  formatDate,
  type Notify,
} from "../ui";

export function AccountPage({
  session,
  notify,
  onLogout,
  onReauthenticate,
}: {
  session: AuthenticatedSession;
  notify: Notify;
  onLogout: () => void;
  onReauthenticate: () => void;
}) {
  const [accounts, setAccounts] = useState<Account[]>([]);
  const [loadingAccounts, setLoadingAccounts] = useState(
    session.account.role === "admin",
  );
  const [accountError, setAccountError] = useState("");
  const [creating, setCreating] = useState(false);
  const [username, setUsername] = useState("");
  const [displayName, setDisplayName] = useState("");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState<"admin" | "member">("member");

  const loadAccounts = async () => {
    if (session.account.role !== "admin") return;
    setLoadingAccounts(true);
    setAccountError("");
    try {
      setAccounts(await api.listAccounts());
    } catch (error) {
      setAccountError(
        error instanceof Error ? error.message : "无法读取账户列表",
      );
    } finally {
      setLoadingAccounts(false);
    }
  };

  useEffect(() => {
    void loadAccounts();
  }, [session.account.id, session.account.role]);

  const createAccount = async (event: FormEvent) => {
    event.preventDefault();
    setCreating(true);
    try {
      const account = await api.createAccount(
        username.trim(),
        displayName.trim(),
        password,
        role,
      );
      setAccounts((current) => [...current, account]);
      setUsername("");
      setDisplayName("");
      setPassword("");
      setRole("member");
      notify(`账户 ${account.username} 已创建。`, "success");
    } catch (error) {
      if (error instanceof ApiError && error.status === 428) {
        onReauthenticate();
        notify("账户管理需要近期密码验证。", "warning");
      } else {
        notify(
          error instanceof Error ? error.message : "创建账户失败",
          "danger",
        );
      }
    } finally {
      setCreating(false);
    }
  };

  return (
    <div className="page-stack">
      <PageHeader
        actions={
          <>
            <button
              className="button button-secondary"
              onClick={onReauthenticate}
              type="button"
            >
              <Icon name="shield" size={17} />
              重新验证身份
            </button>
            <button
              className="button button-ghost danger-text"
              onClick={onLogout}
              type="button"
            >
              退出登录
            </button>
          </>
        }
        description="查看可信服务端身份、会话期限和本地账户边界。"
        eyebrow="Local identity"
        title="账户与会话"
      />

      <section className="two-column-grid">
        <article className="card">
          <div className="card-heading">
            <div>
              <h2>{session.account.display_name}</h2>
              <p>@{session.account.username}</p>
            </div>
            <StatusPill value={session.account.status} />
          </div>
          <dl className="definition-list">
            <div>
              <dt>角色</dt>
              <dd>{session.account.role === "admin" ? "管理员" : "成员"}</dd>
            </div>
            <div>
              <dt>账户 ID</dt>
              <dd className="mono compact">{session.account.id}</dd>
            </div>
            <div>
              <dt>上次登录</dt>
              <dd>{formatDate(session.account.last_login_at)}</dd>
            </div>
          </dl>
        </article>
        <article className="card system-card">
          <p className="eyebrow">Revocable session</p>
          <h2>服务端可撤销会话</h2>
          <dl className="definition-list">
            <div>
              <dt>空闲期限</dt>
              <dd>{formatDate(session.session.idle_expires_at)}</dd>
            </div>
            <div>
              <dt>绝对期限</dt>
              <dd>{formatDate(session.session.absolute_expires_at)}</dd>
            </div>
            <div>
              <dt>最近验证</dt>
              <dd>{formatDate(session.session.reauthenticated_at)}</dd>
            </div>
          </dl>
        </article>
      </section>

      {session.account.role === "admin" ? (
        <section className="card">
          <div className="card-heading">
            <div>
              <h2>本地账户</h2>
              <p>新账户从空人物空间开始，不能读取其他账户的数据。</p>
            </div>
          </div>
          {loadingAccounts ? (
            <LoadingState label="正在读取账户…" />
          ) : accountError ? (
            <ErrorState
              message={accountError}
              retry={() => void loadAccounts()}
            />
          ) : (
            <div className="account-grid">
              {accounts.map((account) => (
                <article className="account-row" key={account.id}>
                  <div>
                    <strong>{account.display_name}</strong>
                    <small>@{account.username}</small>
                  </div>
                  <StatusPill value={account.role} label={account.role} />
                  <StatusPill value={account.status} />
                  <time>{formatDate(account.created_at)}</time>
                </article>
              ))}
            </div>
          )}
          <form
            className="account-create-form"
            onSubmit={(event) => void createAccount(event)}
          >
            <label className="field">
              <span>用户名</span>
              <input
                autoCapitalize="none"
                maxLength={32}
                minLength={3}
                onChange={(event) => setUsername(event.target.value)}
                pattern="[a-z0-9][a-z0-9._-]{2,31}"
                placeholder="team-member"
                required
                value={username}
              />
            </label>
            <label className="field">
              <span>显示名称</span>
              <input
                maxLength={200}
                onChange={(event) => setDisplayName(event.target.value)}
                required
                value={displayName}
              />
            </label>
            <label className="field">
              <span>初始密码（至少 15 字符）</span>
              <input
                autoComplete="new-password"
                maxLength={1024}
                minLength={15}
                onChange={(event) => setPassword(event.target.value)}
                required
                type="password"
                value={password}
              />
            </label>
            <label className="field">
              <span>角色</span>
              <select
                onChange={(event) =>
                  setRole(event.target.value as "admin" | "member")
                }
                value={role}
              >
                <option value="member">成员</option>
                <option value="admin">管理员</option>
              </select>
            </label>
            <button
              className="button button-primary"
              disabled={
                creating ||
                !username.trim() ||
                !displayName.trim() ||
                password.length < 15
              }
              type="submit"
            >
              <Icon name="plus" size={17} />
              {creating ? "正在创建…" : "创建账户"}
            </button>
          </form>
        </section>
      ) : null}
    </div>
  );
}
