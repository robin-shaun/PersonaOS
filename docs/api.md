# PersonaOS HTTP API

PersonaOS 0.12.0 提供 FastAPI HTTP API。完整 Compose 中 API 直接地址是
`http://127.0.0.1:18110`，Web 同源入口是 `http://127.0.0.1:18111/api`。交互式
Swagger UI 位于 `/docs`，运行时 schema 位于 `/openapi.json`；仓库同时提交
[固定版本 OpenAPI 快照](openapi.json)，CI 会阻止实现与快照漂移。

当前 API 处于 Alpha。`/api/v1` 表示资源语义已经显式版本化，但在 1.0 前仍可能
通过 minor release 做兼容性调整；破坏性变化必须更新本文件、OpenAPI、CHANGELOG
和迁移说明。

## 访问与信任模型

除 `/health`、`/api/v1/auth/status` 和 `/api/v1/auth/login` 外，所有
`/api/v1/*` 都需要有效的 `personaos_session` Cookie。服务端只保存 256-bit
随机 Cookie 值的 SHA-256 摘要，并从会话账户派生 owner 和 actor；业务 payload
不再接受可信 `user_id` 或 `requested_by`。跨账户资源统一表现为 `404`。

所有已认证的 `POST`、`PUT`、`PATCH` 和 `DELETE` 还必须发送当前会话返回的
`X-CSRF-Token`；浏览器请求存在 `Origin` 时必须与实际 Host 完全同源。Cookie
使用 `HttpOnly` 和 `SameSite=Strict`，HTTPS 部署还必须配置
`PERSONA_COOKIE_SECURE=true`。

PostgreSQL 在应用层过滤之外强制 owner RLS；SQLite 不支持 RLS，只用于本机开发
和相同的应用隔离测试。0.12 仍是回环地址上的本地账户系统，没有 MFA、自助恢复、
集中限流、独立数据库角色或公网生产部署基线，不要把它直接暴露到公网或不可信
局域网。

调用人物端点时可以发送最多 100 个字符的 `X-Request-ID`。服务会把它写入相关
审计事件，便于把用户操作和证据链关联起来；包含换行、制表符或过长的值会返回
`400`。

## 登录、会话与 CSRF

首个管理员不能通过匿名 HTTP 注册，只能在可信主机创建：

~~~bash
.venv/bin/python -m apps.admin create-account \
  --username admin --display-name Administrator --role admin
~~~

命令默认无回显提示密码，不接受密码命令行参数或环境变量；自动化场景可使用
`--password-stdin`。浏览器工作台会自动完成下面的 Cookie/CSRF 协议。命令行
客户端应把 cookie jar 放在权限受限的临时位置，且不要把真实密码、Cookie 或
CSRF 写入日志。以下只展示协议，`PASSWORD` 代表安全输入而不是建议写入脚本：

~~~bash
COOKIE_JAR=./var/personaos-api.cookies
LOGIN_RESPONSE="$(
  curl -sS -c "$COOKIE_JAR" \
    -H 'Content-Type: application/json' \
    --data-binary '{"username":"admin","password":"PASSWORD"}' \
    http://127.0.0.1:18110/api/v1/auth/login
)"
CSRF_TOKEN="$(
  printf '%s' "$LOGIN_RESPONSE" |
    .venv/bin/python -c 'import json,sys; print(json.load(sys.stdin)["csrf_token"])'
)"
~~~

之后所有读取发送 `-b "$COOKIE_JAR"`；所有写入还发送
`-H "X-CSRF-Token: $CSRF_TOKEN"`。`GET /api/v1/auth/session` 可以在页面刷新后
恢复 CSRF，不需要读取 HttpOnly Cookie。`POST /api/v1/auth/logout` 会立即撤销
服务端会话。

登录和成功再认证都会轮换 Cookie。删除资料/记忆、包含解密原文的导出、允许
`external` 数据边界、断开 GitHub 连接和创建账户需要默认五分钟内的密码验证；
窗口过期返回 `428`：

~~~bash
curl -sS -X POST \
  -b "$COOKIE_JAR" \
  -c "$COOKIE_JAR" \
  -H "X-CSRF-Token: $CSRF_TOKEN" \
  -H 'Content-Type: application/json' \
  --data-binary '{"password":"PASSWORD"}' \
  http://127.0.0.1:18110/api/v1/auth/reauthenticate
~~~

再认证响应包含新的 `csrf_token`，调用方必须替换旧值，再由用户明确重试原高风险
请求；服务不会自动执行它。

## 错误与并发语义

FastAPI 参数校验错误使用标准 `{"detail": [...]}` 响应。领域错误使用
`{"detail": "..."}`。主要状态码如下：

| 状态 | 含义 |
| --- | --- |
| `200` | 同步读取、更新、删除回执或审批完成 |
| `201` | 人物、关系、会话、消息、连接或反馈已创建 |
| `202` | 资料导入、重建索引、项目任务、重试或取消请求已入队 |
| `400` | 缺少高风险删除确认，或请求 ID 无效 |
| `401` | Cookie 缺失、无效、已撤销或过期，或登录凭据无效 |
| `403` | CSRF/Origin 失败、角色不足或模型数据边界拒绝 |
| `404` | 资源不存在，或不属于当前账户 |
| `409` | 版本冲突、重复决策或任务状态不允许该操作 |
| `413` | 人物导出超过配置的内存缓冲上限 |
| `422` | schema 或领域约束不满足 |
| `428` | 高风险动作要求近期再认证；不会自动重试原请求 |
| `502` | 上游 GitHub/Hermes 失败，或回答 citation 未通过校验 |
| `503` | 所需 GitHub App/Hermes 尚未配置或不可用 |

写入异步任务时应发送稳定的 `Idempotency-Key`。相同 key 和相同操作会返回原任务；
相同 key 被复用于冲突输入时会失败。队列是至少一次执行语义，任务 handler 自身
负责幂等。任务状态可能为 `pending`、`running`、`awaiting_approval`、
`completed`、`failed`、`cancelling`、`cancelled` 或 `rejected`。

记忆更新必须发送 `expected_version`。版本已经变化时返回 `409`，客户端应重新
读取记忆并让用户决定如何合并，不能静默覆盖。

## 人物证据闭环

### 1. 创建人物

~~~bash
curl -sS -X POST http://127.0.0.1:18110/api/v1/personas \
  -b "$COOKIE_JAR" \
  -H "X-CSRF-Token: $CSRF_TOKEN" \
  -H 'Content-Type: application/json' \
  -H 'X-Request-ID: demo-create-persona' \
  -d '{
    "display_name": "虚构演示人物",
    "description": "只依据人工确认的演示资料回答"
  }'
~~~

响应中的 `id` 是 `PERSONA_ID`；`simulation_notice` 明确说明该对象不是现实中的
本人。默认 `allowed_model_boundaries` 只有 `local`。

### 2. 上传资料并等待 Worker

~~~bash
curl -sS -X POST \
  'http://127.0.0.1:18110/api/v1/personas/PERSONA_ID/documents?language=zh-CN' \
  -b "$COOKIE_JAR" \
  -H "X-CSRF-Token: $CSRF_TOKEN" \
  -H 'X-Request-ID: demo-upload' \
  -F 'file=@examples/data/demo-journal.md;type=text/markdown'

curl -sS -b "$COOKIE_JAR" \
  http://127.0.0.1:18110/api/v1/tasks/TASK_ID
~~~

只接受配置大小内的 UTF-8 `.txt` 或 `.md`。`202` 响应包含 `document` 和
`queue_submission.task_id`。Worker 完成后，任务状态为 `completed`，资料状态为
`ready`；原始 Blob 已加密，chunk 带字符、行号和内容哈希定位。

### 3. 审核候选

~~~bash
curl -sS \
  -b "$COOKIE_JAR" \
  http://127.0.0.1:18110/api/v1/personas/PERSONA_ID/memory-candidates

curl -sS -X POST \
  http://127.0.0.1:18110/api/v1/memory-candidates/MEMORY_ID/review \
  -b "$COOKIE_JAR" \
  -H "X-CSRF-Token: $CSRF_TOKEN" \
  -H 'Content-Type: application/json' \
  -H 'X-Request-ID: demo-review' \
  -d '{
    "action": "confirm",
    "reason": "已逐字核对虚构演示资料"
  }'
~~~

也可以设置 `action: "reject"`，或在确认时发送 `edited_content`。修订后的版本会
标为用户陈述并保留 `derived_from` 关系，不能再伪装成来源逐字验证的事实。候选
在确认前不会进入索引。

### 4. 提问并解析 citation

~~~bash
curl -sS -X POST \
  http://127.0.0.1:18110/api/v1/personas/PERSONA_ID/conversations \
  -b "$COOKIE_JAR" \
  -H "X-CSRF-Token: $CSRF_TOKEN" \
  -H 'Content-Type: application/json' \
  -d '{"title":"证据问答"}'

curl -sS -X POST \
  http://127.0.0.1:18110/api/v1/conversations/CONVERSATION_ID/messages \
  -b "$COOKIE_JAR" \
  -H "X-CSRF-Token: $CSRF_TOKEN" \
  -H 'Content-Type: application/json' \
  -H 'X-Request-ID: demo-question' \
  -d '{"content":"我什么时候加入 PersonaOS 项目？","top_k":5}'
~~~

响应同时包含：

- `user_message` 与 `assistant_message`；
- `retrieval_run`，固化 embedding space、过滤条件和召回记录；
- `model_call`，说明是否调用生成器及其数据边界；
- `citations`，每项解析到不可变 MemoryVersion、Evidence、chunk、文件和 locator。

`assistant_message.answer_status` 为 `answered` 时，人物事实必须有 citation。没有
可用证据时返回 `insufficient_evidence`，并明确说明没有找到相关已确认记忆；
默认 `evidence-only` 生成器不会让通用模型补全个人事实。

## 人物资源

| 方法与路径 | 行为 |
| --- | --- |
| `POST /api/v1/personas` | 在当前可信账户下创建人物档案 |
| `GET /api/v1/personas` | 列出人物；`include_inactive=true` 包含停用人物 |
| `GET /api/v1/personas/{id}` | 读取人物、模拟声明与模型策略 |
| `PATCH /api/v1/personas/{id}/model-policy` | 修改允许的模型数据边界 |
| `POST /api/v1/personas/{id}/documents` | multipart 上传并排队处理文本 |
| `GET /api/v1/personas/{id}/documents` | 列出资料及处理状态 |
| `GET /api/v1/documents/{id}` | 读取资料、chunk 和处理元数据，不返回 Blob key |
| `DELETE /api/v1/documents/{id}?confirm=true` | 删除来源及依赖记忆、向量、引用和回答 |
| `GET /api/v1/personas/{id}/memory-candidates` | 列出待人工审核候选 |
| `POST /api/v1/memory-candidates/{id}/review` | 确认、修订确认或拒绝候选 |
| `GET /api/v1/personas/{id}/memories` | 默认列出 confirmed；可用 `status` 过滤 |
| `GET /api/v1/memories/{id}` | 读取当前版本、历史版本、证据和 embedding 元数据 |
| `PATCH /api/v1/memories/{id}` | 以 `expected_version` 追加内容/敏感等级版本 |
| `DELETE /api/v1/memories/{id}?confirm=true` | 删除记忆及其向量、关系和派生引用 |
| `POST /api/v1/personas/{id}/memory-relations` | 建立支持、冲突、派生、取代或相关关系 |
| `GET /api/v1/memories/{id}/relations` | 读取入边和出边 |
| `DELETE /api/v1/memory-relations/{id}?confirm=true` | 删除一条关系 |
| `POST /api/v1/personas/{id}/memories/reindex` | 在当前 embedding 空间重建 confirmed 索引 |
| `POST /api/v1/personas/{id}/conversations` | 创建人物会话 |
| `GET /api/v1/conversations/{id}/messages` | 读取会话消息 |
| `POST /api/v1/conversations/{id}/messages` | 混合检索并生成带引用回答 |
| `GET /api/v1/messages/{id}/citations` | 独立解析回答引用 |
| `GET /api/v1/personas/{id}/audit-events` | 按新到旧读取最多 500 条审计事件 |
| `POST /api/v1/personas/{id}/export` | 导出版本、证据和审计，可选择是否含原文 |

删除响应是应用层依赖图回执，不代表备份或物理介质已净化。导出当前在 API
内存中生成，默认最大 25 MiB；manifest 包含 SHA-256，不包含向量数组和内部
Blob object key。

## 数字员工、Skill 与任务

| 方法与路径 | 行为 |
| --- | --- |
| `GET /api/v1/employees` | 列出已注册岗位及权限 |
| `GET /api/v1/skills` | 列出已注册 Skill 版本与契约 |
| `GET /api/v1/runtime/status` | 检查规则或 Hermes runtime |
| `POST /api/v1/github/connections` | 验证 GitHub App installation 并连接单仓库 |
| `GET /api/v1/github/connections` | 列出当前账户的连接 |
| `DELETE /api/v1/github/connections/{id}` | 近期再认证后断开当前账户连接 |
| `POST /api/v1/tasks/project-maintenance` | 排队生成只读项目简报和 Issue 建议 |
| `GET /api/v1/tasks` | 列出最近任务 |
| `GET /api/v1/tasks/{id}` | 读取队列、运行、步骤、审批与产物轨迹 |
| `POST /api/v1/tasks/{id}/cancel` | 请求协作式取消 |
| `POST /api/v1/tasks/{id}/retry` | 只重试已失败任务 |
| `POST /api/v1/approvals/{id}/decision` | 接受、修改接受或拒绝待审批交付 |
| `POST /api/v1/tasks/{id}/feedback` | 保存反馈作为后续偏好证据 |

偏好接口位于 `/api/v1/users/{user_id}/preferences` 和
`/api/v1/preferences/{id}`。候选必须确认且未过期才会进入 Personal Context。
路径中的 `user_id` 必须与当前会话账户 ID 完全一致，否则返回 `404`；详情与审核
接口也会在仓储层重复匹配当前账户。

## 账户管理

| 方法与路径 | 行为 |
| --- | --- |
| `GET /api/v1/auth/status` | 公开返回本地认证模式和是否需要首个管理员 |
| `POST /api/v1/auth/login` | 验证 Argon2id 密码并轮换已有 Cookie |
| `GET /api/v1/auth/session` | 返回可信账户、会话期限和 CSRF |
| `POST /api/v1/auth/reauthenticate` | 验证当前密码并轮换 Cookie/CSRF |
| `POST /api/v1/auth/logout` | 撤销当前会话 |
| `GET /api/v1/accounts` | 管理员列出账户，不返回凭据材料 |
| `POST /api/v1/accounts` | 近期再认证的管理员创建隔离账户 |

0.12 尚不提供账户停用、密码修改、自助恢复或 MFA endpoint。主机管理员可以使用
受信 CLI 创建账户和执行 legacy owner 迁移，但 CLI 权限等同于主机/数据库控制权，
不构成对恶意主机管理员的隔离。

## OpenAPI 维护

实现或 schema 变化后运行：

~~~bash
python scripts/export_openapi.py
python scripts/export_openapi.py --check
python scripts/release_check.py
~~~

导出器使用临时 SQLite、临时 Blob key 和离线 rules runtime，不会读取或修改开发
数据库，也不会调用 GitHub、模型或付费服务。`docs/openapi.json` 是生成产物，
请勿手工编辑。
