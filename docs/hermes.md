# Hermes Agent 接入

## 接入方式

AI Colleague 通过 Hermes Agent 的认证 Runs API 连接独立 gateway：

    Worker → HermesRuntime → POST /v1/runs → 轮询状态 → 结构校验
                              ↘ 取消/超时 → POST /v1/runs/{id}/stop

选择进程外 HTTP 边界有三个目的：Hermes 可以独立升级；API 与 Worker 使用同一
配置；业务代码不依赖 Hermes 的 Python 内部对象。Hermes 的实际模型和 provider
由 gateway profile 管理，`HERMES_MODEL` 是请求的模型名或服务端 route alias。

## 1. 安装并创建专用 profile

按照 [Hermes 官方安装说明](https://github.com/NousResearch/hermes-agent) 安装，
然后创建独立 profile：

~~~bash
hermes profile create ai-colleague
hermes -p ai-colleague setup
~~~

profile 会隔离配置、密钥、会话和记忆，但它本身不是操作系统沙箱。官方的
[Profiles 文档](https://hermes-agent.nousresearch.com/docs/user-guide/profiles)
也明确说明 profile 不会限制宿主文件访问，因此下一步的工具清理不能省略。

## 2. 启用 API Server

在 `~/.hermes/profiles/ai-colleague/.env` 中设置：

~~~bash
API_SERVER_ENABLED=true
API_SERVER_HOST=127.0.0.1
API_SERVER_PORT=8642
API_SERVER_KEY=替换为独立的高强度随机值
API_SERVER_MODEL_NAME=ai-colleague
~~~

不要配置 `API_SERVER_CORS_ORIGINS`。网页只访问 AI Colleague API，Hermes key
只保存在服务端。配置字段和 Runs API 语义以
[Hermes API Server 文档](https://hermes-agent.nousresearch.com/docs/user-guide/features/api-server/)
为准。

## 3. 清空 API Server 工具

Hermes API Server 默认具有终端、文件、浏览器、网络和其他工具。当前岗位的
GitHub 数据已由 AI Colleague 的只读 gateway 获取，Hermes 只负责分析，不应
再次获得任何工具。

运行以下命令，选择 `API Server` 平台，清空所有 toolset 后保存：

~~~bash
hermes -p ai-colleague tools
~~~

对应 profile 的 `config.yaml` 应保存一个显式的空选择；如果手工维护配置，需
保留文件中的其他内容并加入：

~~~yaml
platform_toolsets:
  api_server: []
~~~

此 profile 也不要配置 MCP server。若已经安装插件，在工具界面中一并禁用。
Hermes 的 toolset 行为参见
[官方 Toolsets Reference](https://hermes-agent.nousresearch.com/docs/reference/toolsets-reference/)。

## 4. 启动并验证 Hermes

以前台方式启动，便于首次观察日志：

~~~bash
hermes -p ai-colleague gateway
~~~

另一个终端检查 liveness、能力和工具边界：

~~~bash
curl http://127.0.0.1:8642/health
curl http://127.0.0.1:8642/v1/capabilities \
  -H 'Authorization: Bearer YOUR_API_SERVER_KEY'
curl http://127.0.0.1:8642/v1/toolsets \
  -H 'Authorization: Bearer YOUR_API_SERVER_KEY'
~~~

`/health` 应返回 `status: ok`，能力中应包含 `run_submission`、`run_status` 和
`run_stop`，toolsets 的 `data` 中不应有任何 `enabled: true` 项。AI Colleague
会在每个 Skill 执行前重复这些能力与工具检查；发现远端工具时直接拒绝运行。

## 5. 配置 AI Colleague

在仓库 `.env` 中设置：

~~~bash
DIGITAL_EMPLOYEE_RUNTIME=hermes
HERMES_API_URL=http://127.0.0.1:8642
HERMES_API_KEY=YOUR_API_SERVER_KEY
HERMES_MODEL=ai-colleague
HERMES_REQUEST_TIMEOUT_SECONDS=20
HERMES_POLL_INTERVAL_SECONDS=0.5
HERMES_MAX_CONTEXT_BYTES=1000000
~~~

然后正常启动 API 与 Worker：

~~~bash
.venv/bin/python -m apps.api
.venv/bin/python -m apps.worker.run
~~~

检查集成状态：

~~~bash
curl http://127.0.0.1:18110/api/v1/runtime/status
~~~

Hermes 不可用、API key 错误、Runs API 版本不兼容、输出不是所需 JSON 结构，
或 toolsets 不为空时，任务会保留失败轨迹并遵循现有队列重试策略。系统不会在
故障时静默回退到 rules；需要离线运行时应显式改回
`DIGITAL_EMPLOYEE_RUNTIME=rules`。

## 当前记忆边界

适配器为每次 Skill 生成独立、可关联的 session ID，但暂不发送
`X-Hermes-Session-Key`，因此没有启用 Hermes 长期个人记忆。个人记忆将在后续
由 AI Colleague 的来源、同意、冲突和撤销机制统一控制，避免在两个系统中形成
无法审计的平行记忆。
