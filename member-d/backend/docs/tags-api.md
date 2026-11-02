# 消息标签接口 第一版

用户可以创建有名称和颜色的标签，并将多个标签添加到消息。该版本复用 D 后端的 Bearer 安装令牌和 workspace 隔离：同一个 workspace 的所有持令牌者共享标签，**不是个人账户级私有标签**。QQ 群成员身份与单群授权尚未实现；不要将本功能对外宣传为已具备用户级隐私隔离。未来个人账户上线后，应引入用户 ID，设计明确的数据归属迁移与撤权流程。

## 数据与兼容性

首次启动自动创建 `tags` 与 `message_tags` 表，既有消息不变。名称为 1–40 个字符，规范化并去除首尾空格；同一工作空间名称不区分大小写，不可重复。颜色使用 `#RRGGBB`，存储时转为大写。标签 ID 和消息 ID 均由后端生成，标签与消息为多对多。单条消息可有多个标签，重复添加和重复移除是幂等操作。标签不会改动原始消息和 AI 查询内容；新增、修改、删除标签不会改变消息版本。

## 接口

所有 `/v1` 接口均要求 `Authorization: Bearer <工作空间令牌>`。D 前端若用同源代理，路径应在前面增加 `/api`。当前线上演示网关仅允许特定读取和查询，**不放行这些标签接口**，本提交也不部署服务器；前端联调前必须完成访问控制与网关调整。

| 方法与路径 | 请求或用途 | 成功响应 |
|---|---|---|
| GET /v1/tags | 列出当前工作空间标签 | `items`, `count`, `scope`；每项含 `message_count` |
| POST /v1/tags | `{"name":"重要","color":"#FF453A"}` | 201，返回标签对象 |
| PATCH /v1/tags/{tag_id} | `{"name":"课程","color":"#007AFF"}`；两个字段可任选 | 200，返回更新后的标签 |
| DELETE /v1/tags/{tag_id} | 删除标签和关联；原消息保留 | 200，`deleted`, `messages_deleted:false` |
| GET /v1/messages/{message_id}/tags | 查看某条消息的标签 | `message_id`, `items`, `count` |
| POST /v1/messages/{message_id}/tags | `{"tag_id":"标签ID"}`，添加一个标签 | 返回当前消息标签列表 |
| DELETE /v1/messages/{message_id}/tags/{tag_id} | 移除标签与消息的关联 | 200，`removed:true` |
| GET /v1/tags/{tag_id}/messages?limit=20&offset=0 | 按标签浏览消息 | `tag`, `items`, `total`, `limit`, `offset` |

标签对象包含 `id`, `name`, `color`, `created`, `updated`。所有返回都包含 `request_id`。可参考颜色：红色 `#FF453A`、橙色 `#FF9F0A`、黄色 `#FFD60A`、绿色 `#30D158`、蓝色 `#0A84FF`、紫色 `#BF5AF2`、灰色 `#8E8E93`；它们仅为前端建议，后端不预置、不限制为这几种。删除标签或取消标签不会删除消息。删除消息时级联删除其标签关联。

常见错误：`401 UNAUTHORIZED` 无效令牌；`404 NOT_FOUND` 消息或标签不存在/不属于当前空间；`409 TAG_NAME_EXISTS` 名称重复；`400 INVALID_COLOR` 颜色非法；`400 INVALID_INPUT` 名称或分页非法。错误不返回其他工作空间的数据。前端应同时用名称和颜色呈现，避免只靠颜色识别。

## 升级和测试

升级前先使用 SQLite 在线备份保存数据库并验证恢复；部署新代码后建表自动完成。回滚必须先停止服务，恢复升级前数据库和代码；新增标签数据将不会出现在旧备份中，回滚前应另行备份。备份可能含聊天原文，需限制访问与按既定规则删除。当前未修改群忆前端及网关。

在 `member-d/backend` 目录运行 `python -m unittest discover -s tests -q`。测试包括真实本机 HTTP、新建/重命名/改色、重复名称、幂等添加、按标签浏览、删除关联、跨空间隔离、消息删除级联、重启保留。测试使用模拟数据。
