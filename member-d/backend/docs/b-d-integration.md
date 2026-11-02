# B 与 D 集成（试验分支）

网站仍调用 D 的 `/v1/query`。设置 `QUNYI_B_INTEGRATION=1`、D 的 live 模式和一致的 OpenAI 兼容模型配置后，D 使用自己的 SQLite 与已授权群范围调用 B facade；不匹配时返回错误，不回退模拟答案。配置值只应存于服务端受保护环境文件，不要提交密钥。

最小依赖包含 B 的 `jieba`，常规 B 混合向量检索的 torch 和句向量模型仅在调用该检索时需要。使用 `PYTHONPATH=backend:member-d/backend` 启动或测试。两个服务的模型地址、名称和密钥必须一致；本桥接暂不支持 WorkBuddy SSE 通道。请求只发送候选所需内容，设置超时、调用次数上限，禁止模型提供方重定向。

已授权来源只是工作空间内的群开关，不是完整的用户身份或群成员认证。当前不应将此作为多用户产品的安全边界。时间、类型、文件等无法可靠映射的条件会拒绝执行；引用须匹配本次候选原文。无法核验的结果标记为 uncertain，不将模型文字视为已确认事实。撤销群授权后新消息不会自动恢复授权。

测试：`PYTHONPATH=backend:member-d/backend python -m unittest discover -s member-d/backend/tests -q`。其中 HTTP 集成使用本地模拟模型，不证明外部真实模型、WorkBuddy 或 QQ 官方消息持续接入可用。上线前仍需真实模型凭据、来源授权、部署备份、真实端到端验证；禁止将示例数据当真实结论。
