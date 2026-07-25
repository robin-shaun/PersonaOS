# ADR 0002：本地 Web 工作台使用静态 SPA 与同源 API 代理

- 状态：Accepted
- 日期：2026-07-25
- 决策版本：PersonaOS 0.10.0

## 背景

M1–M3 已有完整 FastAPI 契约，但只能通过 OpenAPI 页面和脚本操作。M4 需要把人物、
资料导入、候选审核、记忆版本/关系、问答引用、删除、任务与审计形成可演示的操作
闭环，同时不能：

1. 把模型、数据库或对象存储密钥交给浏览器；
2. 在前端复制 owner、敏感等级、版本冲突或删除依赖图等安全判断；
3. 为本地 UI 打开宽泛 CORS 或破坏现有 API/CLI 端口；
4. 让内置 Demo 绕过人工记忆确认。

该工作台不需要 SEO、服务端渲染或面向公网的认证会话。React 官方文档把 Vite
列为从零构建 React 客户端的可用构建工具，并提供 TypeScript 模板：
[React：Build a React app from Scratch](https://react.dev/learn/build-a-react-app-from-scratch)。
Vite 官方提供静态生产构建和开发代理，当前版本要求 Node 22.12 或更高：
[Vite Getting Started](https://vite.dev/guide/)。

## 决策

### 1. UI 是类型化静态客户端，不拥有领域状态

`apps/web` 使用 React、TypeScript 和 Vite。它通过明确的 API client 类型调用
已有 FastAPI；人物、审核、版本、关系、任务和审计记录只以后端响应为准。前端
确认对话框用于让风险可见，但后端的 `confirm=true`、`expected_version`、owner
过滤和审计仍是强制边界。

当前页面不引入 Next.js、React Router、全局状态框架或组件库。MVP 只有一个固定
工作台信息架构，不需要 SSR、嵌套路由或缓存同步层；减少依赖更利于离线部署和
审计。出现公开内容页面、深链接或复杂协作状态后再重新评估。

### 2. 生产 Web 使用非 root Nginx 同源代理

Vite 只负责构建。生产镜像用 `nginxinc/nginx-unprivileged` 在 8080 提供静态
资源：

- `/api/*` 原路径代理到 `api:18110`；
- `/health` 代理到 API，供页面展示系统状态；
- `/healthz` 只验证 Web 容器本身；
- 其他路径回退 `index.html`；
- 内容安全策略只允许同源脚本、样式和连接，禁止嵌入、对象、摄像头、麦克风与
  定位权限。

Nginx 的 `proxy_pass` 行为依据官方模块：
[ngx_http_proxy_module](https://nginx.org/en/docs/http/ngx_http_proxy_module.html)。
Compose 把 Web 映射为 `127.0.0.1:18111`，API 继续映射
`127.0.0.1:18110`，保持现有脚本兼容。未实现认证前不允许将任一端口暴露到
不可信网络。

### 3. Demo 保持人工确认语义

页面内置的虚构 Demo 只创建人物并上传一份文本，Worker 仍异步处理，用户仍需在
审核页逐条确认。它不自动写入 confirmed 记忆，不调用付费模型，也不改变人物
模型数据边界。

独立 `examples/compose_smoke.py` 是测试夹具：它明确确认一条虚构测试候选，再
检查 citation 和审计，以验证全新 Compose 环境的纵向闭环。脚本经 Web origin
调用 API，因而同时覆盖反向代理，不把“后端能运行”误当作“产品入口能运行”。

### 4. 测试分三层

- Vitest + Testing Library 验证创建人物、人工审核和 citation 展示的用户行为；
- Python 测试验证锁文件、非 root 镜像、同源代理、Compose 端口和 smoke 覆盖面；
- 真实 smoke 在已启动 Compose 上验证静态页、API、Worker、数据库和引用链。

Vitest 与 Vite 共用配置，符合其官方集成方式：
[Vitest Getting Started](https://vitest.dev/guide/)。

## 未选择的方案

- 把 React 资产编译进 FastAPI 镜像：会把 API 与前端发布周期绑定，也无法满足
  Compose 的独立 Web 健康边界。
- 浏览器直接调用 `127.0.0.1:18110` 并开放 CORS：开发可行，但生产会增加跨源
  配置和误开放风险。
- Next.js/SSR：本地私密管理端没有 SEO 或首屏服务端数据需求，会增加 Node
  运行时和服务端状态面。
- 前端直接连接 PostgreSQL、对象存储或模型 Provider：会泄露凭据并绕过领域审计。
- Demo 自动确认全部候选：会破坏“重要长期记忆必须人工确认”的核心产品语义。

## 后果与限制

- 静态 SPA 刷新时不能从后端枚举历史会话，因为当前 API 没有会话列表；UI 只在
  localStorage 保存最近会话 ID。清理浏览器存储不会删除服务端审计数据。
- UI 对本地单用户很完整，但不是认证层。隐藏按钮、CSP 或 localhost 绑定都不能
  替代真实身份认证。
- Nginx 和 Node 镜像已固定具体 tag，前端 npm lockfile 已提交；Python 依赖和
  容器 digest 的完整供应链锁定仍属于 M5。
- 当前运行环境可能没有 Docker，因此必须如实区分静态 Compose 检查与真实容器
  smoke 结果。

## 验证

合并前至少执行：

```text
cd apps/web && npm ci && npm test && npm run build
pytest -q
python examples/compose_smoke.py              # Compose 已启动时
```

真实 smoke 必须证明回答 citation 能解析到本次上传的文件和稳定 locator，且审计
至少包含人物创建、资料上传/处理、记忆确认与问题回答。
