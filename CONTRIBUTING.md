# 贡献 PersonaOS

感谢你帮助 PersonaOS 成为一个更可信、可运行的个人智能体底座。这里优先接受
边界清楚、能被测试、不会把推断伪装成记忆的纵向改进。大范围重写、尚无行为的
目录骨架和真实人物数据不适合作为第一份贡献。

参与即表示你同意遵守 [社区行为准则](CODE_OF_CONDUCT.md)，并按
[Apache-2.0](LICENSE) 授权你有权提交的贡献。发现漏洞请不要创建公开 Issue，
而应遵循 [安全策略](SECURITY.md)。

## 选择一个问题

现有 Issue 中标记为 `good first issue` 或 `help wanted` 的条目适合作为起点。
新能力建议先提交功能 Issue，写明：

1. 用户问题和最小可验证结果；
2. 数据来源、人物归属、权限和人工确认边界；
3. 什么是事实、总结、推断或用户设定；
4. 已考虑的成熟组件和更小实现；
5. 迁移、失败恢复和删除语义。

安全修复可以直接通过私密 Security Advisory 协作，不需要先公开讨论。

## 开发环境

基线是 Python 3.11.15、Node.js 22.23.1 和 npm lockfile。Docker Compose 是完整
运行环境，但后端与 Web 测试都可以在没有 Docker 的开发机上执行。

~~~bash
python3 -m venv .venv
.venv/bin/python -m pip install \
  --index-url https://pypi.org/simple \
  --require-hashes \
  -r requirements-dev.lock
.venv/bin/python -m pip install \
  --index-url https://pypi.org/simple \
  --no-deps \
  --no-build-isolation \
  -e .

cd apps/web
npm ci
cd ../..
~~~

启动 SQLite 开发服务可以运行 `./start.sh`；完整 PostgreSQL/pgvector、Worker、
API 和 Web 闭环使用 `docker compose up --build`。只使用虚构或明确获授权的
测试资料，切勿把真实日记、聊天、令牌或私钥提交到仓库、Issue 或 CI 日志。

## 架构不变量

修改实现时必须保留以下约束：

- 原始资料、候选、人工确认版本和模型临时输出是不同的数据类别；
- 候选不会自动成为长期记忆，重要写入经过明确的人类决策；
- 只有当前已确认版本能被索引和用于人物回答；
- 回答中的人物事实必须绑定可解析的 MemoryVersion 和原始来源；
- 无证据时表达不确定性，不让模型补写人物事实；
- 人物、owner、可见性、敏感等级和模型数据边界在检索前强制过滤；
- 修改追加不可变版本，删除清理派生引用并留下不含正文的审计墓碑；
- 工具和 Skill 使用最小权限，高风险动作不能绕过人工确认；
- 浏览器不持有模型、数据库、GitHub App 或 Blob 加密密钥；
- 可信本地账户不是公网身份平台，不得把默认部署暴露到不可信网络；涉及 owner
  的改动必须同时覆盖双账户应用测试和 PostgreSQL RLS smoke。

需要改变这些语义时，先新增 ADR，写清威胁模型、兼容迁移和回滚方案。当前模块
边界见 [架构说明](docs/architecture.md)，项目阶段见 [路线图](ROADMAP.md)。

## 实现与测试

每个 PR 应交付一个可运行的行为，并同时包含失败路径测试。后端变更至少运行：

~~~bash
.venv/bin/ruff check adapters apps core examples migrations scripts tests
.venv/bin/pytest -q
.venv/bin/python scripts/export_openapi.py --check
.venv/bin/python scripts/release_check.py
~~~

Web 变更还需运行：

~~~bash
cd apps/web
npm test
npm run build
npm audit --audit-level=high
~~~

涉及服务装配、PostgreSQL、Nginx 或 Worker 时，运行：

~~~bash
docker compose config --quiet
docker compose up --build --detach --wait
python3 examples/compose_smoke.py
docker compose down
~~~

不要对含真实资料的卷执行 `docker compose down -v`。无法执行某项检查时，在 PR
中明确列出原因和剩余风险。

## 数据库与 API 变更

- 修改 SQLAlchemy 模型必须新增 Alembic migration，禁止修改已经发布的 migration；
- 同时验证 SQLite upgrade/check/downgrade 和 PostgreSQL offline SQL；
- 删除或重命名字段前提供向后兼容窗口和数据迁移策略；
- `/api/v1` 行为变化必须更新 [API 指南](docs/api.md) 和生成的 OpenAPI；
- 运行 `python scripts/export_openapi.py` 更新 `docs/openapi.json`，不要手改快照；
- 并发更新使用显式版本或幂等键，错误码和删除确认语义必须有 API 测试。

## Skill 变更

先阅读 [Skill 开发指南](docs/skill-development.md)。Skill manifest 必须通过
Pydantic 契约，声明权限、工具、风险、超时、重试、测试、示例和依赖；还必须
提供真实运行实现与自动化测试。manifest 本身不是沙箱，也不会自动赋予工具
权限。当前版本只运行仓库内受信代码，不接受把任意第三方代码直接加载进主进程
的实现。

## 依赖与锁文件

Python 依赖范围写在 `pyproject.toml`，生产和开发解析结果分别保存在
`requirements.lock`、`requirements-dev.lock`。修改 Python 依赖后运行：

~~~bash
./scripts/lock_dependencies.sh
.venv/bin/python -m pip install \
  --index-url https://pypi.org/simple \
  --require-hashes \
  -r requirements-dev.lock
.venv/bin/pip-audit --require-hashes -r requirements.lock
~~~

脚本在临时虚拟环境中使用固定版本的 pip-tools，并将 build backend 一起锁定。
修改前端依赖时使用 `npm install --save-exact` 或
`npm install --save-dev --save-exact`，提交 `package.json` 和
`package-lock.json`。容器基础镜像必须保留可读 tag 并固定多架构 manifest
digest；GitHub Action 必须固定完整 commit SHA。

## 提交 PR

保持提交聚焦，避免混入格式化或本地数据。PR 描述应包含结果、取舍、实际执行的
验证和限制。推荐使用 `feat:`、`fix:`、`docs:`、`test:`、`chore:` 等清晰前缀，
但不强制重写已有历史。

可以使用 AI 辅助开发，但提交者必须理解并审核每一项变更，对许可证、来源、
安全和测试结果负责。不要把私密资料交给未经授权的模型供应商，也不要提交未经
验证的 AI 生成测试结果或引用。
