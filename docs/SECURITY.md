# 安全设计

## 已启用措施

- PBKDF2-SHA256 密码散列与随机 Salt。
- 会话仅持久化令牌摘要，Cookie 为 HttpOnly、SameSite=Lax，HTTPS 下启用 Secure。
- `owner/member/viewer` 权限和项目数据隔离。
- 明确来源的 CORS、方法和请求头白名单。
- 对认证、邀请接受和 Agent 等写接口实施滑动窗口限流。
- 所有 HTTP 写操作写入 `audit_logs`，包含操作者、项目、资源、路径、状态码和时间。
- 统一错误响应；500 响应不向客户端暴露堆栈。
- Docker 使用非 root 用户、`no-new-privileges` 和日志轮转。

## 限流边界

当前内置限流器为进程内实现，适合项目当前单 worker 部署。多 worker 或多实例部署必须在 Caddy/API Gateway/Redis 中配置共享限流，否则各进程分别计数。

`COLLAB_TRUST_PROXY=true` 只可用于可信反向代理入口；否则攻击者可伪造 `X-Forwarded-For`。

## 审计边界

审计日志不得保存：

- 密码或密码散列；
- 原始会话 Token、Cookie、Authorization；
- LLM API Key 或外部平台密钥；
- 用户主动输入之外的屏幕、键鼠、位置等隐私数据。

审计记录为安全追溯数据，不应用于成员排名或“摸鱼”判断。
