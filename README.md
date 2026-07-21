# Digital Employee MVP

一个“证据驱动、人工审批优先”的开源项目维护数字员工。

当前版本只读取 GitHub 公共或已授权仓库，生成项目工作简报与 Issue
优先级建议。系统不会修改 Issue、回复评论、合并 PR 或发布 Release。
所有建议在交付前都会暂停，等待用户接受、修改或拒绝；这些选择会被
保存为以后构建个人偏好和数字分身的数据证据。

## 产品形态

产品采用网页端优先：浏览器负责连接仓库、发起任务、查看轨迹和处理审批；API、
Worker 与 Hermes 都运行在服务端，模型密钥不会下发到浏览器。当前仓库已完成
后端 API 和 Worker，现阶段可通过 `/docs` 操作；专用管理网页是下一阶段界面。
以后如需桌面客户端，可以在同一 API 上增加桌面壳，不需要改变 Agent 底座。

## 已实现的闭环

    GitHub 只读快照
          ↓
    project-daily-brief Skill
          ↓
    issue-triage Skill
          ↓
    事实引用与格式质量门禁
          ↓
    人工审批（暂停与恢复）
          ↓
    产物、修改、反馈与决策记录

当前 0.5.0 版本已集成 Hermes Agent API Server，也保留确定性的 `rules-v1`
作为默认离线运行时。业务层只依赖 `AgentRuntime`，切换 Hermes 不需要重写
Skill、Workflow、审批或持久化代码。

## 快速启动

需要 Python 3.11 或更高版本。

~~~bash
./start.sh
~~~

第一次运行会自动创建 `.env` 和 `.venv`、安装运行依赖，然后在同一终端启动
API 与 Worker。API 默认监听尚未被本机其他服务使用的 `127.0.0.1:18110`。
打开 http://127.0.0.1:18110/docs 查看交互式 API，按 `Ctrl+C` 会统一停止
本脚本启动的所有进程。依赖更新后可以强制重新安装：

~~~bash
./start.sh --install
~~~

如需分别观察或管理进程，仍可手动启动：

~~~bash
.venv/bin/python -m apps.api
.venv/bin/python -m apps.worker.run
~~~

Worker 默认给每次执行 300 秒硬超时，并每 0.25 秒检查一次主动取消请求。
可以通过 `DIGITAL_EMPLOYEE_WORKER_TASK_TIMEOUT_SECONDS` 和
`DIGITAL_EMPLOYEE_WORKER_CONTROL_POLL_SECONDS` 调整，或使用 Worker 的
`--task-timeout` 与 `--control-poll` 参数临时覆盖。

访问公共仓库时可以不设置 `GITHUB_TOKEN`，但匿名 GitHub API 的请求额度较低。
`GITHUB_TOKEN` 仅保留为本地兼容入口；连接私有仓库推荐使用 GitHub App。
应用只实现 GET 请求，不包含修改 Issue、PR 或仓库的工具。

## 启用 Hermes Agent

Hermes 以独立 API Server 进程接入，本项目不把 Hermes 安装包或内部对象耦合到
业务代码。请先按 [Hermes 配置指南](docs/hermes.md) 创建专用、无工具的
`ai-colleague` profile，然后在本项目 `.env` 中设置：

~~~bash
DIGITAL_EMPLOYEE_RUNTIME=hermes
HERMES_API_URL=http://127.0.0.1:8642
HERMES_API_KEY=与-Hermes-API_SERVER_KEY-相同的值
HERMES_MODEL=ai-colleague
HERMES_PROFILE=ai-colleague
~~~

运行 `./start.sh` 时，如果配置的本地 Hermes gateway 尚未运行，脚本会自动启动
该 profile；如果 gateway 已运行则直接复用。远端 Hermes 只检查连接，不会尝试
在本机代为启动。启动后可以检查：

~~~bash
curl http://127.0.0.1:18110/api/v1/runtime/status
~~~

服务会先检查 Hermes 的 Runs API 能力和 API Server toolsets。只要远端暴露任何
已启用工具，就会在提交模型任务前失败；这一检查不能通过环境变量关闭。Hermes
只分析主服务已经读取的结构化仓库快照，GitHub 访问、权限检查和审批仍由本项目
控制。Worker 取消或超时也会请求 Hermes `/v1/runs/{run_id}/stop`。

## 连接 GitHub App

创建 GitHub App 时只授予以下 Repository permissions：

- Metadata：Read-only（GitHub App 固有的最低权限）；
- Issues：Read-only；
- Pull requests：Read-only。

将 App 安装到明确选择的仓库，然后把 App ID 和私钥路径写入 `.env`。私钥应
放在仓库目录之外；仓库也已忽略 `*.pem` 和 `*.key`，用于降低误提交风险。

~~~bash
GITHUB_APP_ID=12345
GITHUB_APP_PRIVATE_KEY_PATH=/secure/path/ai-colleague.private-key.pem
~~~

配置依据可参考 GitHub 官方的
[App JWT](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/generating-a-json-web-token-jwt-for-a-github-app)
和
[installation token](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/generating-an-installation-access-token-for-a-github-app)
说明。服务生成 RS256 JWT，再申请限制到单个仓库、Issues/PR 只读的短期令牌。
令牌只在进程内缓存，不写入连接表、任务输入或执行轨迹。

用安装 ID 验证并建立用户—仓库连接：

~~~bash
curl -X POST http://127.0.0.1:18110/api/v1/github/connections \
  -H 'Content-Type: application/json' \
  -d '{
    "user_id": "shaun",
    "installation_id": 9876,
    "repository": "owner/private-repository"
  }'
~~~

响应中的 `id` 是仓库连接 ID。创建任务时可以不再传仓库名：

~~~bash
curl -X POST http://127.0.0.1:18110/api/v1/tasks/project-maintenance \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: private-repository-2026-07-21' \
  -d '{
    "github_connection_id": "CONNECTION_ID",
    "user_id": "shaun",
    "max_items": 50
  }'
~~~

断开连接后，新任务和队列中尚未执行的任务都不能再通过该连接读取仓库：

~~~bash
curl -X DELETE \
  'http://127.0.0.1:18110/api/v1/github/connections/CONNECTION_ID?user_id=shaun'
~~~

## 跑一个任务

创建项目维护任务：

~~~bash
curl -X POST http://127.0.0.1:18110/api/v1/tasks/project-maintenance \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: owner-repository-2026-07-21' \
  -d '{
    "repository": "owner/repository",
    "user_id": "shaun",
    "max_items": 50
  }'
~~~

API 会立即返回 `202 Accepted` 和 `pending` 任务。Worker 领取任务后，状态依次
变为 `running` 和 `awaiting_approval`。通过任务详情接口轮询，响应中的
`approvals[0].id` 是审批 ID：

~~~bash
curl http://127.0.0.1:18110/api/v1/tasks/TASK_ID
~~~

接受建议：

~~~bash
curl -X POST http://127.0.0.1:18110/api/v1/approvals/APPROVAL_ID/decision \
  -H 'Content-Type: application/json' \
  -d '{
    "decision": "approved",
    "reason": "本次建议可以采用"
  }'
~~~

带修改接受时使用 approved_with_edits，并把完整修改结果放在
edited_output 字段中。系统会保留版本 1 原稿、版本 2 修改稿、修改反馈和
一条 decision_record。拒绝时使用 rejected，原提案仍保留用于审计。

也可以直接从命令行运行一次：

~~~bash
.venv/bin/python -m apps.worker.run_once owner/repository --max-items 20
~~~

后台 Worker 只处理一个队列任务后退出：

~~~bash
.venv/bin/python -m apps.worker.run --once
~~~

取消尚未交付的任务：

~~~bash
curl -X POST http://127.0.0.1:18110/api/v1/tasks/TASK_ID/cancel \
  -H 'Content-Type: application/json' \
  -d '{
    "reason": "本次简报不再需要",
    "requested_by": "shaun"
  }'
~~~

排队中的任务会立即变为 `cancelled`；运行中的任务先变为 `cancelling`，
Worker 停止当前协程后再收敛为 `cancelled`。重复取消是幂等的。已经进入
审批、完成、拒绝或失败的任务不能通过该接口取消。

## API

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | /health | 运行状态、安全模式与队列计数 |
| GET | /api/v1/runtime/status | 检查当前 Agent 运行时及 Hermes 工具边界 |
| GET | /api/v1/employees | 查看岗位定义 |
| GET | /api/v1/skills | 查看已注册 Skill |
| POST | /api/v1/github/connections | 验证并保存 GitHub App 仓库连接 |
| GET | /api/v1/github/connections | 按用户查看仓库连接 |
| DELETE | /api/v1/github/connections/{connection_id} | 断开仓库连接 |
| POST | /api/v1/tasks/project-maintenance | 幂等创建并入队只读维护任务 |
| GET | /api/v1/tasks | 查看任务列表 |
| GET | /api/v1/tasks/{task_id} | 查看完整执行轨迹 |
| POST | /api/v1/tasks/{task_id}/retry | 重新入队已耗尽重试的失败任务 |
| POST | /api/v1/tasks/{task_id}/cancel | 取消排队中或运行中的任务 |
| POST | /api/v1/approvals/{approval_id}/decision | 接受、修改或拒绝 |
| POST | /api/v1/tasks/{task_id}/feedback | 追加评分与文字反馈 |

任务详情一次返回 task_runs、queue_jobs、task_events、tool_calls、
workflow_runs、approvals、feedback、artifacts 和 decision_records，便于
调试和后续偏好学习。`task_events` 会记录取消请求、取消完成和每次执行超时。

## 代码结构

    apps/
      api/                 FastAPI 接口
      worker/              持久化 Worker 与同步调试入口
    core/
      agents/              Employee Definition 与 AgentRuntime
      skills/              Skill 注册和权限检查
      workflows/           重试、条件、暂停与检查点
      evaluation/          事实引用与交付质量检查
      services/            项目维护和审批业务流程
      storage/             SQLite / SQLAlchemy 数据模型
    adapters/
      github/              GitHub App 鉴权与只读 REST 适配器
      runtime/             可离线验证的规则运行时
      hermes/              Hermes Runs API 客户端与隔离接口
    data/
      employee_templates/  岗位配置
      skills/              Skill 定义与版本
      workflows/           Workflow 定义与版本
    tests/                 核心闭环和 API 测试

详细设计与安全边界见 docs/architecture.md。

## 测试

~~~bash
.venv/bin/pytest -q
~~~

测试全部使用内存数据库、伪造的 GitHub 快照和模拟 Hermes HTTP 网关，不消耗
GitHub 或模型 API 配额。

## 当前边界

- 队列采用 SQLite 和“至少一次”执行语义，租约、幂等键、主动取消与执行超时
  可处理重复请求和常见 Worker 故障，但不适合大规模并发。
- 仍使用自动建表；进入多人试用前应增加正式迁移工具和 PostgreSQL。
- `user_id` 目前是调用方提供的本地标识，不是可信身份。完成登录、租户校验和
  API 授权前，不应把当前连接接口直接暴露到不可信网络。
- `rules-v1` 只依据标签、讨论、reaction 与更新时间排序；Hermes 输出也必须
  通过结构校验、证据质量门禁和人工审批，两者都不替代维护者判断。
- Hermes profile 必须专用于本系统且不启用任何工具或 MCP；普通 Hermes API
  Server 默认包含终端、文件和网络工具，不能直接用于当前只读岗位。
- 尚未抽取个人偏好；当前只保存生成偏好所需的修改和决策证据。
- 取消接口中的 requested_by 当前只是审计标签；接入身份认证前不能作为可信身份。
- 没有任何 GitHub 写能力。后续增加写操作时必须使用独立权限和二次审批。

下一阶段应增加网页管理端、PostgreSQL、正式数据库迁移、登录与租户隔离；
个人记忆抽取仍应建立在真实反馈数据之上。
