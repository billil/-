# QQ 机器人与 D 后端联调

机器人将已授权群的新文本消息持久化到 SQLite 待投递队列，并重试发送到 D 后端；后端通过内部令牌鉴权，按群与平台消息 ID 去重。

## 配置

- D 后端设置环境变量 QUNYI_QQ_WORKSPACE 和 QUNYI_QQ_INGEST_TOKEN。
- 机器人设置 QQ_ALLOWED_GROUPS、QUNYI_INGEST_URL（指向 /v1/internal/qq/messages）和 QUNYI_INGEST_TOKEN；两端令牌必须一致。
- 令牌只通过私密环境变量或本地不提交的配置保存。优先使用本机或私网，跨主机需使用 HTTPS。
- 启动 D 后端，再启动机器人；后台服务须持续运行。

## 本机验证

在仓库根目录执行 `python test_qq_bridge.py`。测试包含 HTTP 错误令牌、离线重试、机器人数据库重启恢复及后端去重。后端测试在 member-d/backend 执行 `python -m unittest discover -s tests -q`；机器人测试在 qq-group-bot 执行 `python -m unittest -q test_qq_group_bot`。

## 已知边界

该版本是本地联调原型，真实 QQ 到前端、模型以及云端持续运行尚未完成端到端验收。QQ 网关可接收的事件取决于账号及群权限，不能保证接收所有普通消息。只转发文字，空文本、附件、撤回、群成员资格核验和历史补齐尚未完成。错误配置或不合规消息会持续重试；队列无上限，需要监控磁盘。没有配置接入地址或令牌时消息只保存在机器人本地。工作区令牌不能替代正式用户和群成员权限，不应公开部署或处理未经授权的数据。
