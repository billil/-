# B 和 D 后端可选整合原型

通过 D 的 `/v1/query` 真实调用 B 的 `app.facade.process`（解析、澄清、检索、答案验证）；B 候选提供器从 D 工作空间 SQLite 检索并映射消息，B 会话写入 D 的 `b_sessions` 表。`trace` 标记 `b_facade` 和 `d_sqlite`。默认关闭，不改变原先 mock 或 D live 入口。旧 B 兼容路由也复用 D 的查询入口。

## 配置

从仓库根目录执行时将 B 的 `backend` 与 D 的 `member-d/backend` 添加到 PYTHONPATH；安装 B 后端所需依赖，至少安装 `jieba`。配置 `QUNYI_B_INTEGRATION=1` 和 `QUNYI_MODE=live`。`QUNYI_MODEL_URL` 必须是完整的 HTTPS chat-completions 地址，`QUNYI_MODEL` 和 `QUNYI_API_KEY` 只在后端配置；B 的 `LLM_BASE_URL`（去掉 `/chat/completions` 后的地址）、`LLM_MODEL` 与 `LLM_API_KEY` 必须与 D 完全一致，否则返回 `B_MODEL_CONFIG_MISMATCH`。凭据通过安全配置提供，不写入仓库。模型调用不跟随重定向。本地测试允许回环 HTTP，仅在独立测试进程显式开启。

## 安全及功能边界

当前隔离范围是 D 现有工作空间，**不是用户或群成员授权**。多用户和真实群授权尚未实现，不应向非授权人员开放。候选只取当前工作空间并通过 D 原索引；B 产生的来源必须对应本次候选且引用为逐字子串。回答目前只展示证据片段并返回 uncertain，避免未完成事件版本/更正整合时将旧通知误当最新结论。当前不调用 B 的向量检索和事件聚类，`db_path=None`，也不声称向量模型已接入。D 缺少可信类别、文件类型和活动时间索引，B 对这些硬条件返回 `B_FILTER_UNSUPPORTED`，不会伪造无结果。发送时间过滤仅作初步适配，需要针对 B 的日期格式和时区开展更多测试。真实模型错误返回 `B_PIPELINE_ERROR` 等错误，不回退 mock。

B 模型请求目前使用 B 自己的超时机制，D 的总时限和调用次数上限还没有统一；D 仍持有全局锁，慢模型可阻塞其他请求。B 会话的撤权及更正后引用安全也依赖未来的正式权限实现。上线前必须增加并发保护、权限和模型调用策略，先备份数据库，并与前端联调本模式返回的 clarify、uncertain 与 citations。

## 验证

`PYTHONPATH=backend:member-d/backend python -m unittest discover -s member-d/backend/tests -q`，从仓库根目录运行。`test_b_integration.py` 在本机启动模拟模型 HTTP 服务和 D HTTP 服务，验证 B facade 确实调用、D 数据库候选、来源引用、会话重启恢复、跨工作空间隔离及配置不一致报错。模拟服务不是外部真实模型；外部模型、QQ 多群和浏览器尚未验证。
