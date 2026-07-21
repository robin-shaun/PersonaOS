# Digital Employee MVP

一个“证据驱动、人工审批优先”的开源项目维护数字员工。

当前版本只读取 GitHub 公共或已授权仓库，生成项目工作简报与 Issue
优先级建议。系统不会修改 Issue、回复评论、合并 PR 或发布 Release。
所有建议在交付前都会暂停，等待用户接受、修改或拒绝；这些选择会被
保存为以后构建个人偏好和数字分身的数据证据。

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

当前 0.3.0 版本使用确定性的 rules-v1 运行时，因此无需模型密钥即可运行和测试。
业务层只依赖 AgentRuntime 接口，Hermes 适配边界位于
adapters/hermes/runtime.py，后续替换运行时不需要重写 Skill、Workflow、
审批或持久化代码。

## 快速启动

需要 Python 3.11 或更高版本。

~~~bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
cp .env.example .env
.venv/bin/python -m apps.api
~~~

API 默认监听尚未被本机其他服务使用的 `127.0.0.1:18110`。打开
http://127.0.0.1:18110/docs 查看交互式 API。

另开一个终端启动持久化 Worker：

~~~bash
.venv/bin/python -m apps.worker.run
~~~

Worker 默认给每次执行 300 秒硬超时，并每 0.25 秒检查一次主动取消请求。
可以通过 `DIGITAL_EMPLOYEE_WORKER_TASK_TIMEOUT_SECONDS` 和
`DIGITAL_EMPLOYEE_WORKER_CONTROL_POLL_SECONDS` 调整，或使用 Worker 的
`--task-timeout` 与 `--control-poll` 参数临时覆盖。

访问公共仓库时可以不设置 GITHUB_TOKEN，但匿名 GitHub API 的请求额度较低。
访问私有仓库时必须提供只读 Token。应用只实现 GET 请求，即使 Token 拥有
更高权限，当前适配器也没有写方法。

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
| GET | /api/v1/employees | 查看岗位定义 |
| GET | /api/v1/skills | 查看已注册 Skill |
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
      github/              只读 GitHub REST 适配器
      runtime/             可离线验证的规则运行时
      hermes/              Hermes 隔离接口
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

测试全部使用内存数据库和伪造的 GitHub 快照，不消耗 GitHub API 配额。

## 当前边界

- 队列采用 SQLite 和“至少一次”执行语义，租约、幂等键、主动取消与执行超时
  可处理重复请求和常见 Worker 故障，但不适合大规模并发。
- 仍使用自动建表；进入多人试用前应增加正式迁移工具和 PostgreSQL。
- rules-v1 只依据标签、讨论、reaction 与更新时间排序，不替代维护者判断。
- 尚未抽取个人偏好；当前只保存生成偏好所需的修改和决策证据。
- 取消接口中的 requested_by 当前只是审计标签；接入身份认证前不能作为可信身份。
- 没有任何 GitHub 写能力。后续增加写操作时必须使用独立权限和二次审批。

下一阶段应使用 GitHub App 安装令牌替代长期个人 Token，再接入 Hermes 生成
更丰富的分析；个人记忆抽取仍应建立在真实反馈数据之上。
