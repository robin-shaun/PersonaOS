# PersonaOS Skill 开发指南

Skill 是 PersonaOS 的一级扩展契约：它描述一个有边界、可评测的能力，而不是一段
可以任意访问主进程的 prompt。0.11.0 已支持仓库内 YAML 注册、版本持久化、岗位
分配、权限/工具交集检查和单次调用超时；项目维护 Skill 可通过规则 runtime 或
隔离的 Hermes API 运行。

当前尚未实现第三方安装、启用/禁用、升级解析、依赖下载、独立进程/容器沙箱和
自动回滚。`retry_policy`、`requires_confirmation` 和 `dependencies` 已进入
契约与审计，但不会仅凭 manifest 自动重试、暂停或安装依赖：Workflow 和业务
service 必须显式实现这些控制。因此，0.11.0 只接受仓库内已审核的受信实现，不要
加载未知社区代码。

## 文件与运行关系

一个可运行能力通常涉及四层：

1. `data/skills/<name>.yaml`：公开的 Skill 契约；
2. `data/employee_templates/*.yaml`：哪些岗位获准使用该 Skill、权限和工具；
3. `data/workflows/*.yaml`：调用顺序、步骤重试和人工审批点；
4. `adapters/` 或 `core/services/`：真实、可测试的执行行为。

启动时 `SkillRegistry` 用 `SkillDefinition` 严格读取全部 YAML，多余字段或缺失
必需契约会失败。`ExecutionStore.seed_definitions` 保存 Skill 及版本快照，
`GET /api/v1/skills` 可检查实际注册结果。

数字员工 Workflow 中的 `skill.<name>` 步骤通过 `SkillExecutor`：执行器先检查
岗位是否分配该 Skill，再计算 `required_permissions`、`required_tools` 与岗位
allowlist 的差集，最后施加 `timeout_seconds`。人物资料导入目前是领域 service
直接执行的确定性 Workflow；对应 manifest 用于契约、注册和审计，但还没有通过
通用 SkillExecutor 加载第三方实现。

## Manifest 契约

~~~yaml
name: example-summary
version: 1.0.0
description: 根据已经授权的文本块生成带来源摘要，不执行外部写操作。

input_schema:
  document_chunks: array
inputs:
  - document_chunks

output_schema:
  summary: string
  evidence: array

required_permissions:
  - persona.chunk.read
required_tools: []

timeout_seconds: 30
risk_level: medium
requires_confirmation: true

retry_policy:
  max_attempts: 2
  backoff_seconds: 0.5
  retry_on:
    - transient_runtime_error

steps:
  - validate_chunks
  - summarize
  - attach_evidence

evaluation:
  - evidence_integrity
  - no_unmarked_inference

tests:
  - name: every_claim_has_evidence
    input_fixture: fictional_chunks
    assertions:
      - every_claim_resolves_to_input

examples:
  - description: 总结一组虚构资料块
    input:
      document_chunks:
        - ordinal: 0
          content: 虚构示例

dependencies: []
~~~

| 字段 | 0.11.0 语义 |
| --- | --- |
| `name` | 稳定、全局唯一的调用名；推荐小写 kebab-case |
| `version` | Skill 行为版本；推荐 SemVer，行为变化必须递增 |
| `description` | 能力、证据边界和明确禁止的副作用 |
| `input_schema` | 必需输入名到基础类型的映射；当前不是完整 JSON Schema |
| `inputs` | runtime context 中预期出现的顶层输入名 |
| `output_schema` | 必需输出名到基础类型的映射 |
| `required_permissions` | 领域权限；必须同时存在于岗位 allowlist |
| `required_tools` | 工具名；必须同时存在于岗位 allowlist |
| `timeout_seconds` | 单次 runtime 调用硬超时，范围 `(0, 3600]` |
| `retry_policy` | 最大尝试、退避和可重试错误类别的声明 |
| `risk_level` | `low`、`medium`、`high` 或 `critical` |
| `requires_confirmation` | 人工确认政策信号；Workflow 仍须有真实 approval 步骤 |
| `steps` | 供审计和文档使用的内部阶段，不会自动生成执行代码 |
| `evaluation` | 交付前必须可实现为确定性检查的质量条件 |
| `tests` | 至少一个具名测试契约 |
| `examples` | 至少一个不含真实个人资料的使用示例 |
| `dependencies` | 外部能力及版本声明；当前无自动解析或下载 |

`input_schema`、`output_schema`、`tests` 和 `examples` 不能为空。Pydantic 会拒绝
未知字段；超时、重试次数和风险值也有范围校验。当前基础类型校验由具体 runtime
和 evaluator 负责，因此新增 Skill 必须提供针对错误输入、缺失输出和越权调用的
自动化测试，不能只依赖 YAML。

## 权限、工具与确认

权限名表达业务动作，例如 `persona.chunk.read`，工具名表达可调用适配器，例如
`github_repository_reader`。两者必须最小化：

~~~yaml
allowed_permissions:
  - persona.chunk.read
allowed_tools: []
skills:
  - example-summary
approval_policy:
  deliver_summary: required
~~~

不要使用 `*`，不要把“为了以后可能需要”当作授权理由。只读工具也要把外部内容
当作不可信输入，防止 prompt injection 通过资料反向扩大权限。模型只能提出工具
请求；真正的 owner、资源归属、敏感等级和确认必须由业务层再次检查。

`requires_confirmation: true` 不是一个自动执行的 UI 开关。必须在 Workflow 中
加入 `approval.<policy-name>`，让 service 在持久化 checkpoint 后暂停，并只允许
明确的 approved、approved_with_edits 或 rejected 决策恢复。高风险或关键 Skill
没有真实 approval 路径时，评审应拒绝合并。

## 实现一个 Skill

1. 从一个输入、一个输出和一个证据不变量开始；
2. 新增 manifest，并把 Skill 分配给最小权限岗位；
3. 在规则 runtime、模型适配器或领域 service 中实现真实行为；
4. 对输出做 Pydantic/确定性 evaluator 校验，不信任模型返回；
5. 在 Workflow 中声明调用顺序、步骤重试和必要审批；
6. 保存调用 runtime、版本、输入摘要、结果、错误和审批轨迹；
7. 添加单元、权限拒绝、超时、重放和纵向 API/Worker 测试；
8. 更新示例、API 或架构文档。

离线基线应保持可运行。如果新 Skill 需要模型或外部服务，提供 fake/规则实现或
固定 fixture，使核心行为在没有 API Key、网络和付费账户时仍能测试。不得把外部
响应整段塞进长期记忆；先保留原始来源，再生成候选并经过写入规则与人工审核。

## 测试清单

~~~bash
pytest -q tests/test_skill_contracts.py
ruff check core/skills adapters data tests
python scripts/release_check.py
~~~

至少覆盖：

- manifest 可加载，版本和所有必需元数据存在；
- 未分配 Skill、缺权限、缺工具时拒绝执行；
- 超时不会留下“成功”状态或未完成副作用；
- 相同输入重放保持幂等或明确产生新版本；
- 每项人物事实、项目建议或高风险判断都能解析到输入证据；
- 模型缺字段、悬空 citation、额外副作用请求被拒绝；
- 审批前不交付或写入长期状态，拒绝后不偷偷重试；
- 日志不包含密钥、原始私密正文或未经脱敏的模型上下文。

仓库已有 `project-daily-brief`、`issue-triage`、
`memory-candidate-extraction` 和 `text-ingestion` 作为实际契约示例。前两个走
通用 SkillExecutor；后两个由人物领域 Workflow 实现。贡献者不应据此误认为
任意 manifest 都会自动获得执行器。

## 版本与兼容

修正文案或测试说明可以递增 patch；增加向后兼容字段递增 minor；删除字段、改变
权限/副作用、证据语义或输出结构应递增 major。旧版本一旦被任务、审批或审计引用，
不得原地覆盖历史记录。

0.11.0 没有 Skill 包管理器。未来安装格式至少需要签名/来源、内容哈希、依赖锁、
权限差异审核、隔离执行、资源配额、升级迁移、禁用和回滚；在这些能力落地前，
“社区 Skill”应以普通代码 PR 进入同一测试和代码审核流程。
