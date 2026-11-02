# P0-1 联调记录：D SQLite → B facade

日期：2026-11-02
范围：B 位只读适配 D 的真实 SQLite 表结构，验证「同一入口定位授权消息 + 引用 D 真实 msg_id」。
**模型状态：本记录中的 `chat_json` 为固定 mock**，只证明 D 数据、字段映射、权限与引用链路；
真实模型效果仍未验证（见 P1-4）。

## 1. 字段映射（D → B Candidate）

| D 列 / 来源 | B 字段 | 处理方式 |
|---|---|---|
| `messages.id` | `msg_id` | 原样传递，作为引用锚点 |
| `messages.content` | `content` | 原样传递，quote 子串校验基准 |
| `messages.sent_at` | `send_time` | 规整为 `YYYY-MM-DD HH:MM:SS`；带时区 ISO 转本机时区 |
| `messages.group_label` | `group_id` | 导入时 D 的 `group_key` 与 `group_label` 相同，可直接用于权限比对 |
| `messages.revision` | `version` | 转 int，默认 1 |
| `metadata.sender` | `sender` | D 未入库发送者时通常为 `null` |
| `metadata.category` | `category` | 仅接受 `event/task/file`，其它值置 `null` |
| `metadata.file_name` 或 `metadata.fields.文件` | `file_hint` | 原样传递 |
| `metadata.msg_status` | `msg_status` | D 无此列时按 `normal`；不做推测 |
| `metadata.edited_at` | `edited_at` | D 无此列时 `null` |

权限来源：`groups.group_key`（`authorized=1`）→ `group_scope`；`facade.retrieve` 再做第二道过滤，
丢弃 `group_id` 不在 `group_scope`、`msg_id` 不在 `allowed_msg_ids` 的候选。

## 2. 三组请求 / 响应（实际运行输出，模型 mock）

### A. 正常查询

请求：`facade.process("志愿服务在哪集合", session=None, provider, store, group_scope=["g-a"])`

```json
{
  "status": "answer",
  "answer": "根据群聊原文：1) 地点：东门，来源：msg_a1。",
  "evidence": [
    {"msg_id": "msg_a1", "quote": "志愿服务上午九点在东门集合",
     "field": "location", "version": 1, "edited": false}
  ]
}
```

`msg_a1` 是测试库中 D 的真实消息 ID；quote 为原文逐字子串。

### B. 缺少权限参数

请求：`facade.process("志愿服务在哪集合", session=None, provider, store)`（无 `group_scope`、无 `allowed_msg_ids`）

```json
{
  "status": "permission_denied",
  "message": "没有权限查看这些群聊的消息，请先加入对应群聊",
  "evidence": []
}
```

解析与 provider 均未被调用（`provider.calls == 0`）。

### C. provider 违规返回越权候选

场景：`provider` 同时返回 `g-a` 与未授权的 `g-b` 候选。

```json
{
  "status": "answer",
  "dropped_out_of_scope": 1,
  "evidence_ids": ["msg_a1"]
}
```

越权候选被 `facade.retrieve` 丢弃，不会进入答案或 evidence。

## 3. 集成测试记录

测试文件：`backend/tests/test_d_integration.py`（5 个用例）

覆盖：
1. `filters.keywords` 真正传给 provider（7.1）；
2. D 列 → Candidate 映射，且只返回授权群；
3. 场景 A：evidence 指向 D 真实 msg_id；
4. 场景 B：无权限参数直接拒绝，不调用模型；
5. 场景 C：provider 越权结果被第二道防线丢弃。

命令与结果：

```
python -m pytest tests/test_d_integration.py -q
5 passed, 1 warning in 3.61s
```

warning 为 jieba 的 `pkg_resources` DeprecationWarning，与本任务无关。

## 4. 已知缺口 / 待 D 确认

1. D 文档提到的 `scripts/migrate_b_sqlite.py` 在仓库中不存在（`member-d/backend/docs/B_D_INTEGRATION.md` L4），
   D 的交付状态却写“B SQLite 迁移脚本已完成”；要么补脚本，要么改成 B 侧只读适配（本记录采用后者）。
2. D `messages` 缺 `msg_status / edited_at / event_time`：撤回、编辑无法识别，按 `normal` 处理；
   `time_field=event_time` 的时间过滤不可用，只能按 `sent_at`（发送时间）过滤。
3. 缺 `sender_id / sender_name`：参与人只能按原文名字做子串校验，无法按发送者身份核验。
4. 生产 HTTP 仍未接 facade（`api.py` 走旧 `answer_flow`），本联调是模块级调用，不是线上 HTTP 联调。
5. 本记录模型为 mock，真实模型未验证。
