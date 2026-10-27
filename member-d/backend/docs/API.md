# 接口与字段约定

所有 JSON 均使用 UTF-8。除 `GET /health` 外都需 `Authorization: Bearer <本机令牌>`。数据空间由后端根据令牌映射决定，不接收客户端指定用户标识。不同令牌可以映射到同一空间；如需隔离必须配置不同空间名称。

## 路由

| 方法 | 路径 | 作用 |
|---|---|---|
| GET | /health | 健康检查，无密钥或配置详情 |
| GET | /v1/capabilities | 模式、真实模型是否配置、本地检索标识、平台验证状态 |
| POST | /v1/imports | 最多 200 条消息批量导入，逐条校验；整包最多 1 MiB |
| GET | /v1/messages?limit=20&offset=0 | 列表，limit 1—100，offset 非负 |
| GET | /v1/messages/{id} | 原文、时间、来源、人工标注字段 |
| PATCH | /v1/messages/{id} | 修订原文、群标签、发送时间或 metadata，重建该条索引 |
| DELETE | /v1/messages/{id} | 删除原文与索引，清理当前空间全部派生会话 |
| GET | /v1/coverage | 已导入与可检索数量、群标签、发送时间范围 |
| POST | /v1/index/rebuild | 空对象，重建当前空间索引 |
| GET | /v1/events | 根据人工事件标识动态整理字段、冲突、变化证据 |
| POST | /v1/query | 新查询、补充回复、条件替换/清除，mock/live 由服务器决定 |
| GET | /v1/sessions/{id} | 会话条件版本和历史结果 |
| GET | /v1/requests/{id} | 当前空间的脱敏请求日志 |

成功导入中个别记录失败仍返回 HTTP 200，请检查 success_count 和 failure_count；未通过顶层校验为 400。401 无效令牌；404 对象不存在或不属于当前空间；409 幂等键冲突；413 请求过大；415 非 JSON；502/503/504 为 AI 调用失败分类。未知内部故障为 500。所有业务响应附 request_id。

## 导入

```json
{
  "idempotency_key": "batch-a-v1",
  "messages": [{
    "content": "【模拟数据】志愿服务在东门集合。",
    "group_label": "模拟群",
    "sent_at": "2026-10-26T09:00:00+08:00",
    "metadata": {
      "event_key": "service",
      "title": "志愿服务",
      "fields": {"地点": "东门"},
      "source_confirmed": true,
      "revision_kind": "initial",
      "supersedes": []
    }
  }]
}
```

只要求 content 非空，最大 20000 字符；group_label 不提供时用“未知来源”，最大 100 字符。发送时间可空，接受日期或带时区 ISO 时间，存储为 UTC，不用导入时间冒充发送时间。metadata 为可选人工标注。fields 值必须逐字出现在原文，最多 20 项。事件名称、分组、发布者身份没有 AI 独立认证。

`revision_kind` 为 initial/correction/cancellation。更正 `supersedes` 只能指向本空间同一 event_key 下已存在的消息，不能形成环；逐条导入顺序处理，同一批次只可引用此前已经知道的消息标识。`source_confirmed` 仅表示**导入者确认关系**。未确认的更正不会自动覆盖旧字段，冲突并列保留；不是“发布时间最新就一定正确”。普通内容不含结构化标注时仍可检索和查看原文，但 mock 不做任意自由文本自动事件抽取。

返回样例：

```json
{"batch_id":"batch_...","success_count":1,"failure_count":1,"items":[{"index":0,"message_id":"msg_...","index_status":"ready"}],"errors":[{"index":1,"code":"INVALID_INPUT","message":"content 格式或长度不正确"}],"replayed":false,"request_id":"req_..."}
```

幂等键的范围是当前数据空间：同键同内容复用结果，同键不同内容返回 409。不提供键时视为新导入，不按原文去重，以保留跨群转发。修订或删除会清空该空间幂等批次记录；之后重放旧键视作新请求，这是当前保守清理策略。

## 查询和条件更新

```json
{"question":"之前那个志愿活动在哪里集合"}
```

多候选返回 clarify、question、options；选项携带 event_key。继续同一会话：

```json
{
  "session_id":"session_...",
  "question":"选择外出志愿服务",
  "condition_updates":{"event_key":"service"}
}
```

条件只允许 query_text、event_key、group_label、sent_after、sent_before。同键更新为替换，值 null 为删除；clear_conditions 可显式清除指定键。日期筛选针对**消息发送时间**，缺失发送时间的记录不满足日期筛选；日期按 UTC 日界解释。想按中国时区的一天筛选时传明确的 +08:00 起止时间。活动发生日期仅作为证据字段，不等于消息发送时间。

```json
{"session_id":"session_...","question":"不是上周，是上个月","condition_updates":{"sent_after":"2026-09-01T00:00:00+08:00","sent_before":"2026-09-30T23:59:59+08:00"},"clear_conditions":["event_key"]}
```

mock **不会自动理解任意自然语言更正**，需要上面的显式条件；live 可通过 set_conditions 工具更新，实际语言效果待真实模型验证。已有会话的 query_text 默认继续保留，问全新问题请不传 session_id，或明确替换 query_text 并清除不再适用的条件。

`search_mode` 为 expanded（默认）或 keyword。两者均为本地 n-gram/TF-IDF，expanded 仅多有限词汇扩展，不是向量语义搜索。

返回状态：answer / clarify / not_found / uncertain / error。answer 的自由文字由已核查 fields 拼接，避免直接展示模型无依据的长文。关键字段包含 name、value、message_id、quote；value 必须出现在 quote，quote 必须出现在原文。对人工结构化事件，字段还必须匹配当前版本字段，旧版本值会被拒绝；未标注文本只允许“原文摘录”字段。此约束降低无依据字段风险，但不是对所有语义、遗漏和事件关联的完整证明。

每次结果含 ai、trace、conditions、condition_version、coverage。覆盖范围只代表已导入资料的边界，不证明任意一天资料完整。live 超时等错误响应仍保存会话、trace 和 ai 状态以便排查，不会写入模拟成功答案。

## 模型动作协议

提供者返回 JSON 对象，每轮一个 action，最多 QUNYI_MAX_CALLS 次（默认 3，上限 5），总 deadline 默认 30 秒，每次请求 timeout 默认 12 秒，二者取更小值。

- search：query 字符串，查询当前空间。
- read：message_ids 数组，只读取之前检索到的候选。
- versions：event_key，必须来自当前候选，获取当前字段和更正关系。
- set_conditions：updates 对象、clear 数组，替换旧条件后重新查询。
- clarify：question 字符串，交用户补充。
- finish：status 为 answer/not_found/uncertain；answer 须给出 fields 数组。

示例：

```json
{"action":"finish","status":"answer","fields":[{"name":"地点","value":"东门","message_id":"msg_...","quote":"在东门集合"}]}
```

接口拒绝其他工具名、跨空间读取、不存在引用、无效 JSON、无证据值、冲突及过期字段；不把模型返回值当作授权依据。模型超时不自动重试，以免产生重复付费或无限循环。

## 删除、日志和运行边界

修订或删除消息后，为避免历史问答重复暴露原文，**当前空间全部 turns、sessions 和 batches 会失效**，不是只清一条引用。前端遇到旧会话 404 时应创建新会话。索引同步更新或删除。返回 derived_sessions_cleared 说明此行为。

request_logs 不保存请求正文、令牌或模型响应原文，只记录归一化路由、状态、耗时、AI 调用标记及错误类。不要把命令行请求、前端 localStorage 或反向代理日志另行采集为未经说明的用户资料。

数据库备份保留源资料，删除在线消息不会穿透删除旧备份。SQLite 的 WAL、存储介质残留和系统备份不在应用逻辑删除的保证范围内；本原型不声称实现取证级安全擦除。实际试点须确定备份保留期限并按需清理。

服务器单进程用锁串行处理数据库和查询，避免并发更正互相覆盖；模型慢请求也会阻塞其他业务请求，属于演示规模取舍。默认本地监听，不含公网 TLS、速率限制、完整账号登录、运营监控和生产部署；不要直接把开发服务器暴露公网。
