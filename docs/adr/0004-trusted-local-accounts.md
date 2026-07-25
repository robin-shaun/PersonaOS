# ADR 0004：可信本地账户、服务端会话与人物空间隔离

- 状态：已接受
- 日期：2026-07-25
- 目标版本：0.12.0

## 背景

0.11.0 的人物领域已经在 service/repository 查询中重复校验 `owner_id`，但
`AccessContext` 仍来自服务端固定的 `local-user`。旧数字员工接口还允许请求体、
路径或查询参数指定 `user_id`，任务详情、重试、取消、审批和反馈也没有统一的
调用者归属检查。这个边界只适合回环地址上的单所有者演示，不能在两个本地账户
之间提供可信隔离。

M6 的目标不是把 PersonaOS 变成公网身份平台，而是在保持本地优先的前提下形成
一个可测试的账户闭环：

1. 本地管理员能够创建独立账户；
2. 浏览器只提交凭据和业务输入，不提交可信 owner；
3. API、Worker 和 PostgreSQL 对人物、资料、记忆、会话、任务、偏好、连接、
   导出和审计使用同一个账户归属；
4. 破坏性或扩大数据边界的动作需要近期再认证；
5. 0.11 的 `local-user` 数据只能通过显式、可预览、可回滚的命令迁移。

## 决策

### 1. 复用 `users` 作为账户真源

`users` 表继续承担所有领域外键的主体表，并新增规范化用户名、Argon2id 密码
摘要、角色、状态、登录失败计数和凭据时间戳。已有的任意 `user_id` 行标记为
`legacy`，不会自动获得登录能力，也不会被静默归入首个新账户。

首个管理员只能通过受信主机命令创建；不提供“数据库为空即可匿名注册管理员”
的 HTTP 入口。命令默认使用无回显密码提示，也支持只从标准输入读取以便自动化，
不接受命令行密码参数或环境变量密码。

### 2. 使用可撤销的服务端会话，不使用 JWT

登录生成至少 256 bit 的随机会话密钥。浏览器只通过 `HttpOnly`、
`SameSite=Strict` Cookie 持有原值，数据库只保存 SHA-256 摘要及绝对/空闲过期、
撤销和最近再认证时间。每次登录和成功再认证都会轮换密钥，注销可立即撤销当前
会话。schema 保留账户 `disabled` 状态和会话撤销原因，但 0.12 不提供账户停用
或密码修改 endpoint。

本地 HTTP 默认不能设置 `Secure` Cookie，因此 `Secure` 是显式配置项；Compose
仍只映射到 `127.0.0.1`。任何 HTTPS 或非本机部署必须启用该项，但这不使当前
版本自动达到公网生产基线。

选择服务端会话是因为 PersonaOS 需要即时撤销、近期再认证、账户停用和完整审计。
自包含 JWT 会把这些需求重新变成服务端状态，同时增加密钥轮换和失效复杂度。

### 3. 密码与在线猜测控制

密码使用 `argon2-cffi` 的 Argon2id 高层接口和 RFC 9106
`LOW_MEMORY` 配置保存；登录时检测旧参数并在成功验证后重算。单因素密码最少
15 个字符，接受长口令，不施加字符类别组合规则；明显常见值会被拒绝。连续失败
触发有上限的临时锁定，响应不区分“账户不存在”“密码错误”或“账户不可用”。

该实现参考：

- [NIST SP 800-63B 密码与会话要求](https://pages.nist.gov/800-63-4/sp800-63b.html)；
- [RFC 9106 Argon2id 参数建议](https://datatracker.ietf.org/doc/html/rfc9106)；
- [argon2-cffi 参数文档](https://argon2-cffi.readthedocs.io/en/stable/parameters.html)。

### 4. Cookie 请求同时使用 CSRF 与同源校验

所有已认证的 `POST`、`PUT`、`PATCH` 和 `DELETE` 请求都必须携带
`X-CSRF-Token`。Token 由独立本地认证密钥、会话 ID 和当前会话摘要通过 HMAC
派生，不包含会话密钥；登录响应和只读 session endpoint 可返回它。登录等没有
会话的写入口在存在 `Origin` 时必须与有效请求 origin 完全一致。`SameSite`
只是纵深防御，不替代 CSRF Token。

认证密钥与 Blob 密钥分离，首次运行生成到权限为 `0600` 的文件；Compose 通过
私有数据卷持久化它。设计依据包括
[OWASP CSRF Prevention](https://cheatsheetseries.owasp.org/cheatsheets/Cross-Site_Request_Forgery_Prevention_Cheat_Sheet.html)
和
[OWASP Session Management](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html)。

### 5. `AccessContext` 只能来自已验证会话

FastAPI 中间件对 `/api/v1/*` 默认拒绝，只有健康检查、OpenAPI 和登录/安装状态
是公开入口。通过会话后，中间件把账户 ID 写入请求状态；endpoint 再构造
`AccessContext(owner_id=account.id, actor_id=account.id)`。人物 API 不再读取
固定 owner，数字员工、偏好和 GitHub API 也不再信任请求中的 `user_id` 或
`requested_by`。

为兼容领域测试和本地迁移，容器可暂时保留 legacy `AccessContext`，但 HTTP
适配器不得使用它。

### 6. 应用过滤与 PostgreSQL RLS 同时执行

所有 API 可达的 service/store 方法显式接收可信账户并在查询中按归属过滤；
跨账户资源统一表现为 404，避免资源枚举。PostgreSQL 对直接或间接属于账户的表
启用并强制 Row-Level Security：

- 事务开始时通过 `set_config(..., true)` 设置 `personaos.owner_id`；
- policy 的 `USING` 与 `WITH CHECK` 同时限制读、写、更新和删除；
- 未设置 owner 的普通事务默认看不到任何账户行；
- Worker 队列领取、认证查找、管理员账户管理和显式迁移使用单独标记的系统事务，
  每个使用点都必须在代码中可搜索和审计。

PostgreSQL 文档说明 RLS 在没有适用 policy 时默认拒绝，且表所有者通常绕过
policy，因此迁移必须同时执行 `ENABLE` 与 `FORCE ROW LEVEL SECURITY`：
[Row Security Policies](https://www.postgresql.org/docs/17/ddl-rowsecurity.html)。

当前 Compose 仍由同一个数据库角色执行迁移和运行服务，系统事务 GUC 也由应用
设置；因此 RLS 的定位是防止仓储遗漏和跨 owner 查询，而不是抵御数据库凭据已
失守或任意 SQL 执行。公网/高对抗部署仍需独立 migration/runtime 角色和外部
秘密管理。

SQLite 不支持 RLS，只运行相同的应用层隔离测试；真实隔离验收必须包含
PostgreSQL Compose。

### 7. 近期再认证

登录本身计为一次再认证。以下动作要求当前会话在默认五分钟窗口内再次验证密码：

- 创建账户；
- 删除资料或长期记忆；
- 导出解密后的原始资料；
- 首次允许 `external` 模型数据边界；
- 断开第三方仓库连接。

窗口过期返回可机器识别的 `428 reauthentication_required`，不自动执行原请求。
再认证成功后轮换会话密钥，并记录 actor、request ID、结果和时间；失败不延长
窗口。

### 8. 0.11 数据迁移必须显式且可回滚

升级 schema 只新增账户/会话/RLS 能力，不改变任何业务行的 owner。管理员命令
先输出按表计数的 dry-run；只有显式 `--apply` 才在单事务内把指定 legacy owner
迁移到目标账户。

迁移回执保存每个被修改表的主键集合、原 owner、目标账户、时间和状态。回滚只
修改回执列出的行，并要求它们仍属于目标账户；任一行已发生归属分歧则整个回滚
失败，不做部分恢复。凭据、会话和认证事件不参加 legacy 归属迁移。

### 9. 不直接采用完整账户框架

FastAPI Users 提供 Cookie/数据库策略和通用账户路由，但其 Cookie 文档也明确
要求另行实现 CSRF；PersonaOS 仍需自定义近期再认证、legacy 回执迁移、领域
`AccessContext`、RLS 事务作用域和审计语义。M6 因此只复用 Argon2 等成熟密码
原语，保持一个小而显式的认证模块，不复制 OAuth、邮件验证、找回密码或 MFA。
当项目需要非本机部署时，应重新评估成熟 IdP，而不是继续扩张本地密码系统。

## 验收

M6 完成必须同时满足：

- 两个账户各自完成“资料 → 记忆 → 检索/回答”且 API、引用和导出零交叉；
- 伪造路径、请求体 owner、资源 ID 或 idempotency key 不能越权；
- 登录轮换已有会话；缺失/错误 CSRF、跨源登录和过期会话被拒绝；
- 密码、Cookie、CSRF、恢复材料和原始资料不进入日志或审计 detail；
- 高风险动作在窗口外失败、再认证后成功，并产生可归属审计；
- legacy dry-run、apply、冲突拒绝和 rollback 有自动化测试；
- Alembic upgrade/downgrade、SQLite 全套测试、真实 PostgreSQL RLS 测试和
  Docker Compose Web-origin smoke 全部通过。

## 后果与限制

- 首次启动多一步受信 CLI 初始化，换取不暴露匿名管理员注册面；
- 每个认证请求需要一次会话查询，换取即时撤销和明确状态；
- RLS 增加事务作用域要求；漏设 owner 会默认拒绝而不是静默回退；
- 当前只有密码单因素和临时锁定，没有 MFA、WebAuthn、账户恢复、
  账户停用/密码修改 UI、集中限流、独立安全审计或渗透测试；
- 本地管理员和拥有主机/数据库/密钥卷控制权的人仍能访问全部数据，这不是对
  恶意主机管理员的隔离。
