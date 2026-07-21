# MVP 架构与决策

## 产品边界

第一个岗位是开源项目维护员工。它只解决三个问题：

1. 把仓库、开放 Issue 和开放 PR 汇总成每日工作简报；
2. 给开放 Issue 提供可解释、可追溯的优先级建议；
3. 记录用户接受、修改或拒绝建议时留下的行为证据。

“自动修改 GitHub”和“模拟某个人”不属于当前版本。

## 模块关系

    API ──入队──▶ SQLite Queue ◀──领取/续租── Worker
                                             │
                                             ▼
    ProjectMaintenanceService
         │
         ├── EmployeeCatalog ── 岗位权限与审批策略
         ├── WorkflowEngine  ── 重试、条件、暂停、检查点
         ├── SkillExecutor   ── Skill 注册与工具权限校验
         │       │
         │       ▼
         │   AgentRuntime ───── rules-v1 / Hermes
         ├── GitHubGateway  ── 只读仓库快照
         ├── Evaluator      ── 引用完整性和输出质量
         └── ExecutionStore ── 轨迹、审批、反馈与决策

业务服务不依赖 httpx、Hermes SDK 或 GitHub 响应对象。所有外部对象先在
adapter 中转换为稳定领域模型。

## 持久化队列

API 只负责校验并创建 `pending` 任务，不在 HTTP 请求内访问 GitHub。每个任务
对应一个 queue_job，状态为 `queued`、`leased`、`completed`、`failed`
或 `cancelled`。
Worker 原子领取作业后定期续租；进程在完成前退出时，租约到期的作业可以由
其他 Worker 回收，旧的运行轨迹会标记为失败并保留。

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

## 数据证据

审批的三种结果对应不同记录：

| 用户行为 | artifact | feedback | decision_record |
| --- | --- | --- | --- |
| 接受 | 保留原稿 v1 | 可后续追加 | 保存选择与理由 |
| 修改后接受 | 原稿 v1 + 修改稿 v2 | 保存原稿和修改稿 | 保存选择、理由和结果 |
| 拒绝 | 保留原稿 v1 | 保存拒绝理由 | 保存选择与拒绝结果 |

这使后续偏好抽取可以区分模型原始输出、用户修改和最终决策，不需要从最终稿
反推发生过什么。

## 安全约束

- GitHub 适配器只实现 GET。
- Employee Definition 明确列出 allowed_tools 和 forbidden_actions。
- Skill 执行前验证 required_tools 是否属于岗位允许集合。
- deliver_recommendation 固定为 required，Workflow 必须暂停。
- 每份报告声明 read_only 和 github_mutations_performed。
- 推荐项的 Issue 编号与证据链接必须存在于本次仓库快照中，否则质量门禁失败。

## 替换运行时

AgentRuntime 只暴露一个异步 run 方法，输入为任务名、结构化上下文和工具名，
输出为结构化 AgentResult。HermesRuntime 只接受一个满足 HermesClient
协议的客户端。

接入具体 Hermes SDK 时应在 adapters/hermes 下完成请求、工具映射和响应
解析。core 中不得导入 Hermes 类型。生产启用前还需要为模型输出增加 JSON
Schema 校验，并保留当前确定性 evaluator 作为交付门禁。

## 下一批技术任务

1. 使用 GitHub App 安装令牌替代长期个人 Token；
2. 增加 PostgreSQL、数据库迁移和租户隔离；
3. 接入 Hermes，但保留 rules-v1 作为离线测试基准；
4. 从 user_edit 与 decision_records 生成带来源和置信度的候选偏好；
5. 提供候选偏好的查看、确认、撤销、冲突和过期机制。
