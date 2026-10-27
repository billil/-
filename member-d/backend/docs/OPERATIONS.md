# 配置和备份恢复

| 环境变量 | 默认 | 含义 |
|---|---|---|
| QUNYI_MODE | mock | mock / live；只在服务启动配置，HTTP 请求不能自行切模式 |
| QUNYI_STATE | state | SQLite 与本机随机凭据目录 |
| QUNYI_TOKENS | 自动生成 | JSON：令牌到数据空间的映射，令牌至少16位ASCII |
| QUNYI_MODEL_URL | 空 | 完整 HTTPS chat-completions URL；不接受 URL 中的账号、查询串或片段 |
| QUNYI_MODEL | 空 | 服务商实际模型标识 |
| QUNYI_API_KEY | 空 | 仅后端持有的模型凭据 |
| QUNYI_TIMEOUT | 12 | 每次模型请求最长秒数，0—60之间，不含0 |
| QUNYI_DEADLINE | 30 | 查询开始到模型处理的总预算，最多120秒；数据库锁排队时间也计算在内 |
| QUNYI_MAX_CALLS | 3 | 最大模型调用次数，1—5 |
| QUNYI_ALLOW_HTTP_LOOPBACK | 未启用 | 仅对 127.0.0.1/localhost/::1 放行 HTTP 合成服务，不放行外部明文地址 |

默认随机令牌文件首次生成时设置仅本机用户权限；Windows 仍须按账户权限保护该文件。启动只打印文件位置，不打印令牌。`config.example.env` 是字段说明，不会自动载入。

## 备份

在项目根目录运行：

```bash
python scripts/maintenance.py backup --state state --file backup-20261025.sqlite
```

使用 SQLite backup API，可读取运行中的数据库。命令拒绝覆盖已存在的备份目标。生成文件含原文，不能加入参赛源码包或公开分享；应与本机令牌文件分开安全管理。

## 恢复

1. 停止服务，保留当前整个 state 文件夹。
2. 选择新的、没有现存数据库的目录。
3. 执行：

```bash
python scripts/maintenance.py restore --state restored-state --file backup-20261025.sqlite --server-stopped
```

4. 设置 QUNYI_STATE 指向 restored-state，再启动服务。恢复时先做 integrity_check，并检查 schema_version 表。不要直接覆盖运行中的数据库；本工具不实现跨版本迁移。
5. 如自定义过数据空间，恢复相同的令牌到空间映射，否则新生成的 demo 空间无法访问原空间。备份只包含数据库，不包含账号凭据或模型密钥。

## 运维限制

数据库 schema 为 v1。原型采用单进程串行数据库业务锁，不适合高并发。未实现自动备份保留、生产迁移、TLS、账号自助注册和速率限制。数据库日志不保存原文，但主数据库、历史会话和备份在删除前可能包含用户提供的内容。调试只使用脱敏或模拟资料。
