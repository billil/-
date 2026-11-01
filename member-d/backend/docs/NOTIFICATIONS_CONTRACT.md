# 通知接口与联调边界

此版本仍是工作区令牌原型，不是正式用户登录或群成员资格系统。接入真实用户数据及公开部署前必须完成身份和群权限校验。

## 前端调用

使用现有 Bearer 工作区令牌，通过前端同源代理调用：

- `GET /v1/groups` 返回该工作区登记的群。
- `GET /v1/subscriptions` 查看关注；`POST /v1/subscriptions` 使用 `{"group_keys":["群标识"]}` 关注。`DELETE /v1/subscriptions/{group_key}` 取消。
- `GET /v1/notifications?limit=50&after=<cursor>` 返回 `items`、`next_cursor`、`has_more` 和 `sync`。将游标视为不透明字符串；`has_more=true` 时继续读取。新游标由 `v1.` 开头，并包含导入时间与消息 ID 的排序位置。
- `POST /v1/notifications/ack` 使用 `{"group_key":"群标识","cursor":"游标"}`。该原型将位置保存在工作区订阅中，不提供独立用户或跨设备已读语义。
- `GET /v1/sync/status` 返回 `configured` 和 `connection_state`；当前 `connection_state=unknown` 且 `connected=null`，因为没有机器人心跳，不能证明在线。

旧版时间戳游标仍按旧版“严格晚于此时间”的语义接受，无法补回此前同一时间戳漏掉的消息；升级时应重置游标并按消息 ID 去重。空游标从头读取。

## 重要限制

复合游标修复已存在数据中同时间戳跨页遗漏；如之后新写入的记录具有早于当前游标的时间或相同时间且 ID 排在游标之前，仍可能遗漏。生产环境需以持久单调递增的变更序号作为游标，并覆盖新增、更正、删除、撤回及权限变化。消息目前按 group_label 匹配群标识，显示名与稳定群 ID 尚需迁移分离；未实现完整用户级授权、实时在线心跳及正式已读语义。不要公开部署或对外声称这些问题已解决。

对已撤销授权的群，订阅通知与已读确认不再返回内容；新机器人消息不会自动恢复授权。现有通用消息、搜索、详情仍只有工作区隔离，不具备逐用户逐群访问控制。真实 QQ、真实模型及云端常驻需分别实测。

在 `member-d/backend` 运行 `python -m unittest discover -s tests -q`。测试使用临时 SQLite 和模拟消息，不连接 QQ。
