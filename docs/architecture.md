# MVP 架构与决策

## 产品边界

PersonaOS `0.7.0` 有两个相互隔离但复用同一运行底座的产品闭环：

1. 人物资料闭环：创建 Persona，导入授权文本，生成有来源候选，由用户确认或
   拒绝，并保留不可变版本和审计；
2. 项目维护员工：汇总 GitHub 仓库、开放 Issue 和 PR，给出可追溯建议，记录
   用户审批形成的行为证据。

当前人物闭环不做检索问答，也不声称系统是现实中的本人；项目维护员工不自动
修改 GitHub。Web UI、登录、多租户、记忆删除和模型驱动总结不属于本版本。

## 人物资料证据链

    FastAPI（固定本地 owner）
              │
              ├── PersonaService ── 文件白名单、大小和 UTF-8 校验
              │         │
              │         ├── EncryptedLocalBlobStore ── AES-256-GCM 原文
              │         └── SourceDocument + queued Task
              │
              ▼
    TaskWorker ── KnowledgeIngestionService
              │
              ├── deterministic chunker
              ├── rules candidate extractor
              └── PersonaRepository
                        ├── DocumentChunk（稳定定位）
                        ├── PersonaMemory（审核状态）
                        ├── MemoryVersion（不可变正文）
                        ├── MemoryEvidence（来源快照）
                        └── AuditEvent（不保存原始正文）

原文只在 BlobStore 解密后进入 Worker 当前协程的临时工作字典。Workflow
checkpoint、task input、tool call 和错误记录只保存哈希、大小、ID 与计数，不
保存原文。候选初始版本标记 `user_confirmed=false`；确认总是派生一个新版本，
复制来源证据并标记人工确认，原版本不被覆盖。

`AccessContext` 来自服务端配置而不是请求参数。M1 只有一个本地 owner，这是一条
明确的部署限制，不等价于认证系统。所有人物、资料和记忆查询仍在 repository
边界重复执行 owner 过滤，为以后替换成登录会话保留接口。

原始 Blob 已应用层加密，但 chunk、候选和证据摘录为后续检索保存在数据库可读
字段，因此仍要求磁盘/volume 加密。`source_verified` 表示可定位回授权资料，
不表示内容已经由外部事实核验。

## 模块关系

    API ──入队──▶ SQL Queue ◀──领取/续租── Worker
                                             │
                                             ▼
    ProjectMaintenanceService
         │
         ├── EmployeeCatalog ── 岗位权限与审批策略
         ├── WorkflowEngine  ── 重试、条件、暂停、检查点
         ├── SkillExecutor   ── Skill 注册与工具权限校验
         │       │
         │       ▼
         │   AgentRuntime ───── rules-v1 / HermesRuntime
         │                              │
         │                              └── 认证 Runs API ── Hermes gateway
         ├── GitHubConnectionService ── 用户、安装与仓库授权关系
         ├── GitHubGateway  ── 只读仓库快照
         ├── Evaluator      ── 引用完整性和输出质量
         ├── PersonalizationService
         │       ├── memory_sources ── 不可混淆的行为来源
         │       └── preferences    ── 候选、审核与运行时上下文
         └── ExecutionStore ── 轨迹、审批、反馈、决策与个人化证据

业务服务不依赖 httpx、Hermes SDK 或 GitHub 响应对象。所有外部对象先在
adapter 中转换为稳定领域模型。

## 持久化队列

API 只负责校验并创建 `pending` 任务，不在 HTTP 请求内访问 GitHub。每个任务
对应一个 queue_job，状态为 `queued`、`leased`、`completed`、`failed`
或 `cancelled`。
Worker 原子领取作业后定期续租；进程在完成前退出时，租约到期的作业可以由
其他 Worker 回收，旧的运行轨迹会标记为失败并保留。

轻量主机开发使用 SQLite；Compose 使用 PostgreSQL，并在领取查询中使用
`FOR UPDATE SKIP LOCKED`。两者都保留后续的条件更新作为租约所有权保护。
Compose 启动 API 前执行 Alembic，应用进程关闭自动建表；`start.sh` 暂时保留
自动建表以兼容现有未版本化 SQLite 数据库。

同一用户可以通过 `Idempotency-Key` 安全重试创建请求。相同键和相同输入返回
原任务；相同键配不同输入会返回冲突。Worker 自动重试耗尽后，任务保持
`failed`，用户可通过重试 API 将尝试次数清零并重新入队。

## 取消与超时

取消接口只接受 `pending` 或 `running` 任务。仍在队列中的任务会原子更新为
`cancelled`，不会再被 Worker 领取；已经领取的任务先进入 `cancelling`，
Worker 的控制轮询会取消当前 asyncio Task，再把 task、queue_job、task_run
和 workflow_run 一起收敛为 `cancelled`。如果 Worker 在取消期间崩溃，
租约回收者负责完成取消，不会把任务重新执行。

每次 Worker 执行都有独立硬超时。超时会把当前 task_run 与 workflow_run
标记为 `timed_out`，随后遵循队列原有的最大尝试次数和重试延迟；耗尽尝试后
任务进入 `failed`。取消请求、取消完成和超时均写入 task_events，保留操作者、
原因、尝试次数和是否继续重试。

## Workflow 状态

| 状态 | 含义 |
| --- | --- |
| pending | 已创建，尚未执行 |
| running | 正在读取、分析或检查 |
| cancelling | 已请求取消，等待 Worker 停止当前执行 |
| cancelled | 已取消，不再执行或交付 |
| awaiting_approval | 已生成建议，等待用户决定 |
| completed | 用户接受原稿或修改稿 |
| rejected | 用户拒绝交付 |
| failed | 工具、Skill 或质量门禁失败 |

task_run 和 workflow_run 还可以进入 `timed_out`，用于区分业务失败与执行时限
耗尽。任务本身在仍有重试机会时回到 `pending`，耗尽后进入 `failed`。

Workflow 每一步都会把当前 state 和 history 写入 workflow_runs。工具错误会
同时进入 tool_calls；超过重试次数后任务进入 failed，API 返回 task_id，用户
仍可查询完整轨迹。

## GitHub App 仓库连接

私有仓库通过 `github_connections` 建立用户、installation 和单个仓库之间的
关系。建立连接时服务先调用 GitHub 验证安装是否能读取目标仓库，验证成功后
只保存 installation ID、规范仓库名、权限快照、状态和验证时间。

每个 installation token 都限制到一个仓库，并把 Issues 与 Pull requests
权限降为 read。token 按 installation + repository 在进程内缓存，并在过期前
刷新；GitHub App 私钥仅来自运行时配置。私钥、App JWT 和 installation token
均不进入数据库、任务输入、tool_calls 或错误响应。

任务只保存 `github_connection_id`。API 入队和 Worker 执行时都会校验连接属于
任务的 user_id 且状态为 active；Worker 每次开始执行时重新解析连接，因此连接
在排队期间被断开后，任务不会继续读取仓库。当前 user_id 尚未由登录态签发，
所以这只是数据层租户边界，不能替代生产身份认证。

## 数据证据

审批的三种结果对应不同记录：

| 用户行为 | artifact | feedback | decision_record |
| --- | --- | --- | --- |
| 接受 | 保留原稿 v1 | 可后续追加 | 保存选择与理由 |
| 修改后接受 | 原稿 v1 + 修改稿 v2 | 保存原稿和修改稿 | 保存选择、理由和结果 |
| 拒绝 | 保留原稿 v1 | 保存拒绝理由 | 保存选择与拒绝结果 |

这使后续偏好抽取可以区分模型原始输出、用户修改和最终决策，不需要从最终稿
反推发生过什么。

## Personal Layer 边界

当前版本不尝试模拟用户人格，只建立数字员工向数字分身演进所需的证据闭环：

    feedback / decision_record
              │
              ▼
        memory_sources
       （来源与时间固定）
              │
              ▼
      candidate preference
    （规则、置信度、证据数）
              │
       用户确认 / 拒绝
              │
              ▼
    confirmed preference
              │
              ▼
       Personal Context
              │
              ▼
      公共 Skill + 个人规则

`memory_sources` 使用 `source_type + source_id` 指向原始 feedback 或
decision_record，并保存本次抽取需要的结构化差异。重复扫描同一来源是幂等的。
`preference_evidence` 把候选规则连接到一个或多个来源，置信度按独立证据权重
累积，并上限限制为 0.99。当前抽取器只做可解释的确定性处理：

- 用户明确写出的修改、拒绝或反馈理由作为候选规则；
- 没有理由的用户修改保存 JSON 字段级差异，并形成低置信度候选观察；
- 决策记录先作为行为来源保留，不从一次普通“接受”中推断人格。

偏好状态为 `candidate`、`confirmed`、`rejected` 或 `revoked`。所有状态变化写入
`preference_reviews`；确认时可设置过期时间。业务 Agent 通过
`PersonalContextProvider` 获取个人上下文，只返回 `confirmed` 且未过期的偏好。
`PersonalContext` 使用版本化结构，并提前保留 `identity_profile`、`memories` 和
`preferences` 三个独立字段；当前前两项为空，后续补充身份和记忆时不需要改变
AgentRuntime 或业务 Workflow 的调用边界。
候选偏好永远不会自动改变公共 Skill，也不会静默代表用户作出决定。

## 安全约束

- 人物 API 使用服务端固定 owner，Compose 只映射到 `127.0.0.1`；未实现认证前
  不允许暴露到不可信网络。
- 原始资料只接受限定大小的 UTF-8 `.txt`/`.md`，按 SHA-256 寻址并用
  AES-256-GCM 加密；密钥来自环境、密钥文件或首次启动生成的 0600 文件。
- Blob object key 有固定格式和根目录约束，读取时同时验证 AEAD tag 与内容哈希。
- 候选永不自动确认；确认总是产生新的人工版本，拒绝候选不进入长期记忆。
- 人物 AuditEvent 只保存状态、哈希、计数和错误类型，不保存原始正文。
- Skill 合约声明权限、工具、超时、重试、风险和确认元数据；SkillExecutor 对
  模型 Skill 强制检查岗位工具/权限并执行超时。第三方 Skill 的安装、启停、
  版本升级和进程级隔离尚未实现，当前只允许仓库内受信代码。
- GitHub 适配器只实现 GET。
- GitHub App installation token 限制到单仓库和只读权限，且不持久化。
- Employee Definition 明确列出 allowed_tools 和 forbidden_actions。
- Skill 执行前验证 required_tools 是否属于岗位允许集合。
- Personal Context 只应用用户已确认且未过期的偏好，候选和撤销记录仅供审核。
- 偏好详情和审核操作必须同时匹配记录所属 user_id。
- deliver_recommendation 固定为 required，Workflow 必须暂停。
- 每份报告声明 read_only 和 github_mutations_performed。
- 推荐项的 Issue 编号与证据链接必须存在于本次仓库快照中，否则质量门禁失败。
- Hermes 使用专用无工具 profile；每次运行前检查 `/v1/capabilities` 和
  `/v1/toolsets`，发现任何已启用远端工具即拒绝提交。
- Hermes API key 只来自进程环境，不进入上下文、数据库、日志或错误正文。
- 仓库内容按不可信数据处理；Hermes 只能返回 Skill schema 要求的 JSON 对象，
  仍需通过确定性的 evaluator 和人工审批。

## 替换运行时

AgentRuntime 暴露异步 `run` 和运行状态接口，输入为任务名、结构化上下文和
业务工具名，输出为结构化 AgentResult。工具名只用于说明主应用已经执行过的
授权业务工具，不会转发为 Hermes 工具权限。

HermesRuntime 通过 `HermesApiClient` 使用官方 `/v1/runs` 接口。客户端先探测
Runs 提交、状态与停止能力，再确认 API Server 没有启用任何 toolset；随后提交
不可信数据信封、轮询完成状态并校验 Skill 的必需字段和基础类型。本地取消会
尽力调用远端 stop。core 中不导入 Hermes 类型，`rules-v1` 继续作为离线基准。

每次 Skill 使用独立 session ID，当前不发送长期记忆 scope header。用户身份、
任务和 task_run 只作为运行关联数据进入上下文，不授权 Hermes 代表用户行动。

## 唯一下一里程碑

M2 只实现 confirmed 当前版本的混合检索与带引用问答：登记 embedding space，
禁止跨空间混用，结合 PostgreSQL 词法检索与向量召回，并在回答落库前验证每个
citation 都能解析到当前 MemoryVersion、DocumentChunk 和 SourceDocument。
证据不足必须返回明确的无记忆结果。登录、Web UI、删除和冲突图仍不混入 M2。
