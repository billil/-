# QQ 群消息机器人（官方 WebSocket + SQLite）

仅在已获授权的群中使用，并向群成员说明消息处理用途。程序使用 QQ 官方机器人接口，不使用模拟登录或抓包。

## 功能

- 接收 `GROUP_MESSAGE_CREATE` 全量群消息和 `GROUP_AT_MESSAGE_CREATE` @消息。
- 按消息 ID 去重，避免两种事件重复入库。
- 使用 `group_openid` 白名单；空白名单为发现模式，不保存消息。
- SQLite 本地存储、可配置保留天数、心跳、断线重连和会话恢复。
- 自动获取并缓存 QQ 访问凭证，凭证不会写入日志。

## 1. 准备 QQ 机器人

1. 在 QQ 开放平台创建机器人，取得 `AppID` 和 `AppSecret`。
2. 确认机器人具有群聊事件权限。本程序订阅 `GROUP_AND_C2C_EVENT (1 << 25)`。
3. 由群主或管理员把机器人加入目标群。
4. 在机器人／群设置中开启“接收所有消息”。未开启时只能收到 @机器人的消息。

官方文档：

- 访问凭证：https://bot.q.qq.com/wiki/develop/api-v2/dev-prepare/access-token.html
- WebSocket：https://bot.q.qq.com/wiki/develop/api-v2/dev-prepare/interface-framework/event-emit.html
- 全量群消息：https://bot.q.qq.com/wiki/develop/api-v2/autogen/event/group_message_create.html

## 2. 配置

Windows：

```bat
copy .env.example .env
```

macOS／Linux：

```bash
cp .env.example .env
```

编辑 `.env`：

```dotenv
QQ_APP_ID=你的AppID
QQ_APP_SECRET=你的AppSecret
QQ_ALLOWED_GROUPS=
```

首次建议让 `QQ_ALLOWED_GROUPS` 保持为空。启动后加入群，控制台和 `logs/bot.log` 会显示：

```text
机器人已加入群：group_openid=你的群OpenID
```

把该值填入配置后重启：

```dotenv
QQ_ALLOWED_GROUPS=你的群OpenID
```

多个群使用英文逗号分隔。只有明确希望保存机器人所在所有群的消息时，才设置为 `*`。

## 3. 启动

Windows 双击 `start_bot.bat`，或执行：

```bat
start_bot.bat
```

macOS／Linux：

```bash
chmod +x start_bot.sh
./start_bot.sh
```

启动脚本会创建项目内 `.venv`、安装依赖并以前台方式运行。按 `Ctrl+C` 停止。要保持长期运行，可在完成首次验证后使用系统服务管理器；不要把 AppSecret 写入命令行或公开日志。

## 4. 查询消息

```bash
python query_messages.py --limit 50
python query_messages.py --group 你的群OpenID --limit 100
```

数据库位于 `data/qq_messages.db`，运行日志位于 `logs/bot.log`。默认保留消息 30 天，可通过 `QQ_RETENTION_DAYS` 修改；设置为 `0` 表示不自动删除。

## 5. 测试

```bash
python -m unittest -v
```

测试使用临时数据库和模拟事件，不连接 QQ，也不需要真实凭证。
