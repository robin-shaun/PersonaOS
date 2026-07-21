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

第一版使用确定性的 rules-v1 运行时，因此无需模型密钥即可运行和测试。
业务层只依赖 AgentRuntime 接口，Hermes 适配边界位于
adapters/hermes/runtime.py，后续替换运行时不需要重写 Skill、Workflow、
审批或持久化代码。

## 快速启动

需要 Python 3.11 或更高版本。

~~~bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
cp .env.example .env
.venv/bin/uvicorn apps.api.main:app --reload --env-file .env
~~~

打开 http://127.0.0.1:8000/docs 查看交互式 API。

访问公共仓库时可以不设置 GITHUB_TOKEN，但匿名 GitHub API 的请求额度较低。
访问私有仓库时必须提供只读 Token。应用只实现 GET 请求，即使 Token 拥有
更高权限，当前适配器也没有写方法。

## 跑一个任务

创建项目维护任务：

~~~bash
curl -X POST http://127.0.0.1:8000/api/v1/tasks/project-maintenance \
  -H 'Content-Type: application/json' \
  -d '{
    "repository": "owner/repository",
    "user_id": "shaun",
    "max_items": 50
  }'
~~~

成功后任务状态是 awaiting_approval。响应中的 approvals[0].id 是审批 ID。

接受建议：

~~~bash
curl -X POST http://127.0.0.1:8000/api/v1/approvals/APPROVAL_ID/decision \
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

## API

| 方法 | 路径 | 用途 |
| --- | --- | --- |
| GET | /health | 运行状态与安全模式 |
| GET | /api/v1/employees | 查看岗位定义 |
| GET | /api/v1/skills | 查看已注册 Skill |
| POST | /api/v1/tasks/project-maintenance | 创建并执行只读维护任务 |
| GET | /api/v1/tasks | 查看任务列表 |
| GET | /api/v1/tasks/{task_id} | 查看完整执行轨迹 |
| POST | /api/v1/approvals/{approval_id}/decision | 接受、修改或拒绝 |
| POST | /api/v1/tasks/{task_id}/feedback | 追加评分与文字反馈 |

任务详情一次返回 task_runs、tool_calls、workflow_runs、approvals、feedback、
artifacts 和 decision_records，便于调试和后续偏好学习。

## 代码结构

    apps/
      api/                 FastAPI 接口
      worker/              单次任务命令行入口
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

- 任务目前在 HTTP 请求内同步执行，适合 MVP，不适合长任务或高并发。
- 使用 SQLite 和自动建表；进入多人试用前应增加正式迁移工具和 PostgreSQL。
- rules-v1 只依据标签、讨论、reaction 与更新时间排序，不替代维护者判断。
- 尚未抽取个人偏好；当前只保存生成偏好所需的修改和决策证据。
- 没有任何 GitHub 写能力。后续增加写操作时必须使用独立权限和二次审批。

下一阶段应先增加后台 Worker、任务恢复和 GitHub App 安装流程，再接入
Hermes 生成更丰富的分析；个人记忆抽取仍应建立在真实反馈数据之上。

