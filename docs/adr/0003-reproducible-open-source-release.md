# ADR 0003：可复现且最小权限的开源发布基线

- 状态：Accepted
- 日期：2026-07-25
- 决策版本：PersonaOS 0.11.0

## 背景

0.10.0 已能用 Compose 演示人物证据闭环，但仓库没有许可证、Python lock、CI、
稳定 OpenAPI 或安全报告入口。Docker tag 和 GitHub Action 浮动时，同一 commit
可能执行不同代码；只提交文档而不让运行环境消费锁文件，也不能构成可复现发布。

PersonaOS 处理高度私密资料。发布工程必须与产品的可追溯原则一致：依赖来源、
构建输入、API 表面和验证结果都应可检查，同时不能为 CI 提供不必要的仓库写权。

## 决策

1. 项目使用 Apache License 2.0，并提交完整 `LICENSE` 与项目 `NOTICE`；
2. `pyproject.toml` 保留兼容版本范围，`requirements.lock` 和
   `requirements-dev.lock` 保存解析后的精确版本与 PyPI SHA-256；
3. lock 同时包含 setuptools/wheel，Docker、start.sh 和 CI 先用
   `--require-hashes` 安装，再以 `--no-deps --no-build-isolation` 安装项目；
4. npm 直接依赖保持精确版本，构建与 CI 只使用 `npm ci`；
5. 公共基础镜像使用“可读 tag + 多架构 manifest digest”，兼顾审计与不可变性；
6. GitHub Actions 固定完整 commit SHA，workflow token 默认只有
   `contents: read`；
7. CI 分为后端/迁移/发布门禁、Web、真实 Compose smoke，后者在临时 runner
   验证 PostgreSQL/pgvector、API、Worker、Nginx 和 Web-origin 证据流；
8. OpenAPI 从临时离线应用实例确定性生成并提交，CI 比较快照；
9. `scripts/release_check.py` 将版本、许可证、lock、digest、Action SHA、核心
   文档、SVG 可访问性和 OpenAPI 变成可执行发布不变量；
10. Dependabot 提交更新建议，但任何更新仍必须通过同一 CI，不自动获得发布权。

## 选择 Apache-2.0

Apache-2.0 是 OSI 批准的宽松开源许可证，并明确提供贡献相关的版权和专利授权。
它允许商业与非商业复用，同时要求分发许可证、保留适用声明并标注修改。项目
没有选择 copyleft，是为了让本地部署、研究和不同模型/数字人适配器能够广泛
组合；这不减少对人物资料授权、隐私和第三方版权的责任。

许可证只覆盖仓库贡献者有权授权的代码与文档，不自动授权用户导入的人物资料、
声音、照片、商标或人格权益。

## 未选择的方案

- **只固定顶层依赖**：传递依赖仍会漂移，不能复现构建；
- **只提交 lock、不让镜像消费**：无法证明发布 artifact 使用了锁定解析；
- **仅使用 Docker tag**：tag 可移动，供应链输入不稳定；
- **所有 CI 放在一个 job**：反馈慢，且难以区分代码、Web 与真实服务失败；
- **为 CI 开写权限**：当前测试和构建不需要，扩大 token 泄露影响；
- **自动创建 tag/release**：发布是维护者的显式高影响动作，不应由普通 CI 推断。

## 后果

依赖更新会产生较大的 hash diff，基础镜像更新需要同步 release gate 常量。
全量 Compose job 比纯单元测试耗时更长，但它是当前唯一能验证 PostgreSQL 类型、
Worker 竞争、Nginx 同源代理和服务健康依赖的发布证据。

锁文件按 Python 3.11 生成；增加其他 Python minor 或平台前，必须验证 marker 与
wheel 覆盖。digest 固定的是多架构 manifest，具体平台仍由容器运行时选择。

CI 成功不等于生产安全审计。0.11.0 仍只支持回环地址上的本地单所有者，下一
里程碑先补可信身份与人物空间隔离。
