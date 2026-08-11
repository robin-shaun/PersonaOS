# ADR 0005：可信管理员初始化与公众成员注册

- 状态：Accepted
- 日期：2026-08-11

## 背景

PersonaOS 需要允许手机上的公众访客自行注册，同时保证站长管理员身份不会被
匿名抢注。服务仍由单个自托管实例运行，不把 Cloudflare 当作业务数据库或会话
存储。

## 决策

1. 首个管理员继续只能由可信主机 CLI 创建，密码通过无回显交互输入。
2. 公众注册默认关闭；开启时必须同时启用 Secure Cookie 并配置 Turnstile
   Site/Secret Key，否则进程拒绝启动。
3. 匿名注册 schema 不包含角色，领域方法固定创建 `member`，且管理员不存在时
   拒绝注册。管理员仍可在近期再认证后创建其他角色。
4. Turnstile token 必须在服务器端验证；登录与注册再受单进程滑动窗口保护。
5. 正式公网入口使用 Cloudflare Named Tunnel 到本机同源 Web 代理。API 端口不
   对公网开放，Cloudflare WAF 承担跨进程/跨重启的边缘速率限制。
6. 会话继续使用服务端可撤销 token、HttpOnly/SameSite=Strict Cookie、Origin
   与 CSRF 校验；浏览器存储中不保存 bearer token。

## 后果

- 站长必须先完成一次可信主机初始化，再开放注册。
- Turnstile 与 Cloudflare 边缘策略成为公众注册部署的外部运行依赖；它们不可用
  时注册应失败关闭，既有用户仍可登录。
- 这不是完整身份平台：当前没有 MFA、自助找回、邮件验证或独立安全审计；公网
  运营者仍需监控、备份、更新依赖并保护主机和密钥。
