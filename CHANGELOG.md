# Changelog

本项目记录用户可见行为、数据语义和迁移边界。版本遵循 SemVer 意图，但 1.0
之前仍可能在 minor release 中调整 API；破坏性变化会在这里明确标注。

## 0.11.0 - 2026-07-25

### Added

- Apache-2.0 许可证、NOTICE、贡献指南、社区行为准则和私密漏洞报告策略；
- 五分钟本地演示叙事、可访问架构图、路线图、API 指南、Skill 开发指南和
  0.11.0 发布说明；
- 提交的 `docs/openapi.json` 及确定性导出/漂移检查脚本；
- Python 生产/开发 hash lock、固定 Node/npm lock、Python/Node 工具链版本；
- 最小权限 GitHub Actions：后端、迁移、OpenAPI、依赖审计、前端与真实
  PostgreSQL/pgvector/API/Worker/Nginx Compose smoke；
- Dependabot 的 Python、npm、Docker 和 GitHub Actions 更新入口；
- 自动 release gate，检查版本同步、许可证、文档、lock hash、容器 digest、
  Action commit SHA、架构可访问性和 OpenAPI 一致性；
- Issue forms 与 PR 安全/证据清单。

### Changed

- 版本统一升级到 0.11.0；
- Python、Node、Nginx 和 pgvector/PostgreSQL 镜像保留 tag 并固定多架构
  manifest digest；
- Docker 和 `start.sh` 从 `requirements.lock` 安装，项目本身使用无依赖、
  无 build isolation 安装，避免重新解析依赖；
- README 和架构文档从 M4 工程交付更新为 M5 开源发布基线。

### Security

- CI 第三方 Action 固定完整 commit SHA，默认 token 权限只有 `contents: read`；
- Python 包要求 SHA-256 hash，npm 使用 `npm ci`，两类依赖都执行高等级漏洞门禁；
- 安全文档明确本地单所有者、数据库明文派生字段、备份/介质删除和第三方服务
  边界，避免把 Alpha 当作生产认证。

### Known limitations

- 没有可信登录、远程多租户、速率限制或生产秘密管理；
- Skill 不支持第三方安装、启停、升级、沙箱或自动回滚；
- 默认 embedding 和回答器是离线验证基线，不是高质量语义/开放式模型；
- 当前发布资产不等于独立安全审计，也不包含已发布的 Git tag 或 GitHub Release。

## 0.10.0 - 2026-07-25

- 增加 React/TypeScript 人物工作台、同源非 root Nginx 和 Web-origin smoke；
- Compose 形成 PostgreSQL/pgvector、API、Worker、Web 的本地演示面；
- UI 覆盖人物、资料、候选审核、记忆、引用问答、任务和审计。

## 0.9.0 - 2026-07-25

- 增加版本化记忆修改、关系、模型数据边界、导出和级联删除证明；
- 外部模型只可接收 public 摘要，删除审计不保留正文。

## 0.8.0 - 2026-07-25

- 增加 embedding 空间、混合检索、Conversation/ModelCall/RetrievalRun 和
  可解析 AnswerCitation；
- 无证据时返回明确不确定性，不调用人物事实生成器。

## 0.7.0 - 2026-07-25

- 增加人物、加密文本导入、稳定分块、来源证据、候选与人工确认闭环；
- 引入 Alembic、PostgreSQL/pgvector Compose 和人物审计。
