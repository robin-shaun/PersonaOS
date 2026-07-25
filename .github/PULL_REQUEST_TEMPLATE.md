## 交付

- 这次变更解决什么具体问题？
- 用户可以如何验证结果？

## 边界

- [ ] 没有把模型推断描述为真实记忆
- [ ] 没有扩大数据、模型或工具权限
- [ ] 测试数据均为虚构或已获授权的数据
- [ ] 数据迁移、删除和审计语义已说明（不适用时勾选）

## 验证

- [ ] `ruff check adapters apps core examples migrations scripts tests`
- [ ] `pytest -q`
- [ ] `npm test && npm run build`（涉及 Web 时）
- [ ] `python scripts/release_check.py`
- [ ] 文档和测试已随行为更新

请列出未执行的检查及原因。涉及高风险工具、认证、加密、删除、外部模型或依赖
更新时，请在正文中说明威胁模型与回滚方式。
