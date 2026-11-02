# B→D 交付接口（app.facade）

版本：v1.2  日期：2026-11-02（P0-1 D SQLite 联调 + keywords）  对齐代码：qunyi@T3 + P0-1（2026-11-02）

模块入口：`backend/app/facade.py`
**唯一** 给 D 调用的 Python 模块；所有全局可变状态、业务库查询、LLM 调用都通过依赖注入隔离。
D **不得** 直接 import `app.agent` / `app.search_hybrid` / `app.api` 作为对外链路；通过本文件暴露的 5 个函数调用。

**已核对**：下文 5 个函数签名与 `backend/app/facade.py` 第 72 / 101 / 121 / 202 / 247 行实际代码一致（核对方式：`grep -n "^def " backend/app/facade.py`，核对日期 2026-11-02）。若代码签名变更，以代码为准并同步本文件版本号。

---

## 1. 接口清单（签名 + 中文 docstring）

```python
# -------- 解析层 --------

def analyze_query(
    query: str,
    today: str,                          # YYYY-MM-DD
    context: Optional[dict] = None,      # 保留参数
) -> ParseResult:
    """把自然语言查询解析为结构化检索条件。
    返回 dict：
      {
        "keywords": ["团建", "聚餐"],
        "time":     {"type": "relative"|"absolute"|"none", "start", "end", "raw"},
        "time_field": "event_time" | "send_time",
        "category": "event" | "task" | "file" | None,
        "file_hint": str | None,
        "filters":  {"explicit": ["category:event"], "vague": ["time_relative:这周末"]},
        "ambiguous": {"field": "time", "question": "...", "options": ["...", "..."]} | None,
      }"""


def revise(prev_parse: dict, correction: str, today: str) -> ParseResult:
    """按更正语句（如 '不是周六，改成周日'）修改原 parse。改条件 = 替换，不是叠加。"""


# -------- 检索层 --------

def retrieve(
    parse: dict,
    candidate_provider: CandidateProvider,
    limit: int = TOP_K_DEFAULT,
    group_scope: list[str] | None = None,      # T2：D 授权的群列表
    allowed_msg_ids: list[str] | None = None,  # T2：D 授权的消息白名单
) -> dict:
    """用 parse 构造 filters，调用 candidate_provider 拉候选。

    T2 权限契约：group_scope / allowed_msg_ids 至少提供一个；两者都缺时
    直接返回 permission_denied，不会当成全量可查，也不会调用 provider。
    provider 返回的越权候选（group_id 不在 group_scope、msg_id 不在白名单）
    一律丢弃并计数，只把授权内候选放进 results。

    返回：{"status": "ok" | "permission_denied", "results": [...],
           "message": str, "debug": {...}}
      results 项：{"msg_id", "content", "sender", "send_time", "score",
                   "sources", "group_id", "low_confidence"?, "degraded"? ...}
      debug：仅 facade.settings.DEBUG=true 时填充；含 filters / count /
             dropped_out_of_scope / dropped_not_allowed。

    candidate_provider 收到的 filters：
      {
        "keywords": ["志愿服务"],           # P0-1：解析出的查询词，D 必须用
        "category": "event" | None,
        "file_hint": str | None,
        "time_range": {"start": "...", "end": "..."} | None,
        "time_field": "event_time" | "send_time",
        "group_id": str | None,              # 解析出的缩小范围提示，非权限来源
        "soft_boost": {"category", "file_hint"},
        "group_scope": [...] | None,         # T2 权限
        "allowed_msg_ids": [...] | None,     # T2 权限
        "_limit": 20,                        # D 可参考，也可忽略
      }"""


# -------- 答案层 --------

def build_answer(
    parse: dict,
    candidates: list[dict],
    db_path: Optional[str] = None,
    group_scope: list[str] | None = None,      # T2：透传给 validate_answer 的 L3
    allowed_msg_ids: list[str] | None = None,  # T2：透传给 validate_answer 的 L3
) -> AnswerResult:
    """把候选消息交给大模型生成答案。

    返回 dict：
      {
        "status":   "ok" | "uncertain" | "no_result",
        "answer":   str,
        "events":   [...],
        "evidence": [{"msg_id": "m001", "quote": "这周六团建，下午6点在南门集合",
                      "field": "time", "version": 1}],
        "fields":   {"event_time": "...", "location": "..."},
        "clusters": [...],    # load_clusters 结果，D 可忽略
      }"""


# -------- 端到端 --------

def process(
    query: str,
    session: dict | None,                   # store.get() 的返回 dict 视图
    candidate_provider: Callable[[dict], list[dict]],
    store: SessionStore,                    # 注入的 SessionStore 实现
    db_path: Optional[str] = None,
    today: Optional[str] = None,            # 默认当天
    limit: int = TOP_K_DEFAULT,
    group_scope: list[str] | None = None,      # T2：D 授权的群列表
    allowed_msg_ids: list[str] | None = None,  # T2：D 授权的消息白名单
) -> dict:
    """解析 → 必要时追问一次 → 更正判定 → 检索 → 答案生成 → 会话写回。

    T2 权限前置：group_scope / allowed_msg_ids 都缺时，在解析前直接返回
    permission_denied，不调用大模型、不调用 provider。

    返回统一响应结构：
      {
        "status":      "answer" | "ask" | "no_data" | "low_relevance" |
                       "uncertain" | "retrieval_degraded" | "permission_denied",
        "parse":       {...},                 # analyze_query 产出
        "answer":      {...} | None,          # build_answer 产出
        "evidence":    [...],
        "debug":       {...},                 # 仅 DEBUG 时填充
        "suggestions": ["放宽时间范围..."],   # 仅 no_data
        "assumptions": ["未确认时间范围..."], # parse 里有 ambiguous 时
        "question":    "你想查哪一类？",      # 仅 ask
        "options":     [...],                 # 仅 ask
        "low_confidence": true,               # 仅 low_relevance
        "degraded":     true,                 # 仅 retrieval_degraded
        "degraded_reason": "vector_unavailable",  # 仅 retrieval_degraded
        "context_lost": true,                 # 会话过期但用户在更正
        "message":      "...",                # 给用户的提示语
      }"""
```

> 运行时真正可 import 的是 5 个函数本身，没有名为 `facade` 的对象。请写
> `from app.facade import process, retrieve`，不要写 `from app.facade import facade`。

---

## 2. 数据契约（B↔D 四个结构）

### 2.1 `Message`（D 侧消息，D 填）

| 字段 | 类型 | 必填 | 含义 | 由谁填 |
|------|------|------|------|--------|
| `msg_id` | string | 是 | 消息唯一 ID，引用锚点 | D |
| `group_id` | string | 是 | 群 ID，鉴权与召回范围的唯一依据 | D |
| `sender_id` | string | 是 | 发送者稳定 ID | D |
| `sender_name` | string \| null | 否 | 发送者展示名 | D |
| `content` | string | 是 | 消息原文，引用子串校验的唯一基准 | D |
| `send_time` | string | 是 | 发送时间，格式 `YYYY-MM-DD HH:MM:SS` | D |
| `msg_status` | `normal` \| `edited` \| `revoked` | 是 | 消息状态；`revoked` 不得进入候选 | D |
| `edited_at` | string \| null | 否 | 最后编辑时间；`msg_status=edited` 时必填 | D |
| `version` | integer | 是 | 消息版本号，从 1 开始，编辑一次 +1 | D |

### 2.2 `Candidate`（D 传给 B 的候选，D 填，B 只读）

| 字段 | 类型 | 必填 | 含义 | 由谁填 |
|------|------|------|------|--------|
| `msg_id` | string | 是 | 对应 `Message.msg_id` | D |
| `content` | string | 是 | 对应版本的原文；必须与 `version` 匹配 | D |
| `sender` | string \| null | 否（建议） | 展示用发送人，取 `sender_name` | D |
| `send_time` | string \| null | 否（建议） | 展示用发送时间 | D |
| `group_id` | string | 是 | 必须等于 D 授权范围内的群 | D |
| `category` | `event` \| `task` \| `file` \| null | 否 | 消息类别 | D |
| `file_hint` | string \| null | 否 | 文件名/附件线索 | D |
| `score` | number | 否 | 检索相关度，0~1 或 D 自定义 | D |
| `sources` | string[] | 否 | 检索来源标记，如 `["kw","vec"]` | D |
| `version` | integer | 否 | 对应 `Message.version`，默认 1 | D |
| `low_confidence` | boolean | 否 | 候选整体相关度低；B 据此返回 `low_relevance` | D |
| `degraded` | boolean | 否 | 检索降级（如向量不可用）；B 透传为 `degraded` | D |

### 2.3 `Session`（会话）

| 字段 | 类型 | 必填 | 含义 | 由谁填 |
|------|------|------|------|--------|
| `session_id` | string | 是 | 会话唯一 ID，由 D 生成并持久化 | D |
| `status` | `parsed` \| `asked` \| `answered` | 是 | 会话状态，B 在 `process` 内更新 | B（D 持久化） |
| `parse` | object | 是 | 最近一次解析结果，交给 `revise` 做更正 | B（D 持久化） |
| `last_query` | string \| null | 是 | 上一轮原查询；等待追问时用于回拼 | D 持久化，B 更新值 |
| `updated_at` | string | 是 | 最近更新时间，ISO8601（含时区） | D |
| `ask_count` | integer | 否 | 已追问次数，B 用它保证最多追问 1 次 | B（D 持久化） |
| `result_summary` | string \| null | 否 | 上一轮结果摘要，B 可写 | B（D 持久化） |

> **键名注意**：`facade.process` 的 `session` 入参是 dict，B 读取的键名是 `query`
> （见 `facade.py` `_dict_to_session`），而 D 持久化字段叫 `last_query`。调用时请映射：
> `{"session_id": ..., "status": ..., "parse": ..., "query": session.last_query, "ask_count": ...}`。
> 见第 7.3 节。

### 2.4 `Citation`（引用）

| 字段 | 类型 | 必填 | 含义 | 由谁填 |
|------|------|------|------|--------|
| `msg_id` | string | 是 | 引用消息 ID | B |
| `quote` | string | 是 | 原文片段；B 已校验必须是该消息 `content` 的子串 | B |
| `field` | string \| null | 是（可为 null） | 该引用支撑的字段名，如 `time` / `location`；无法归类时为 null | B |
| `version` | integer | 是 | 引用对应的消息版本，取 `Candidate.version` | D 注入，B 回填 |

> 若消息在回答前被编辑（`version` 变化），D 必须用该 `version` 对应的原文重新校验引用；
> 无法确认时丢弃该引用，不得把旧版原文当新版展示。

---

## 3. 请求 / 响应示例（三组）

| 场景 | 文件 | 内容 |
|------|------|------|
| A 一次问清 | [`docs/examples/A_single_query.json`](examples/A_single_query.json) | “这周志愿活动在哪” → `answer` |
| B 追问后更正 | [`docs/examples/B_followup_correction.json`](examples/B_followup_correction.json) | “这周志愿活动在哪” → 追问 → “不是这周，是下周” → `answer` |
| C 无结果 | [`docs/examples/C_no_result.json`](examples/C_no_result.json) | “上周有没有人发过无人机采购表” → `no_data` |

每个示例文件的结构：

```json
{
  "version": "v1.0",
  "scenario": "A | B | C",
  "title": "...",
  "description": "...",
  "rounds": [
    {
      "request":  { "query": "...", "today": "...", "session": {...},
                    "d_context": {...}, "candidates": [...], "_owner": {...} },
      "response": { "status": "...", "parse": {...}, "...": "...", "_owner": {...} }
    }
  ]
}
```

- `request` 是 D 调 `facade.process` 时提供的输入（`candidate_provider` / `store` 是代码对象，JSON 中以 `candidates` / `session` 表示其结果）。
- `response` 是 `process` 的返回；字段随 `status` 变化（`ask` 有 `question/options`，`no_data` 有 `suggestions`，`answer` 有 `answer/evidence/message`）。
- `_owner` 是**示例专用**元数据，用来标出字段归属（`B` 产出 / `D` 注入），不属于运行时响应，D 接入时忽略。
- 同一 `_owner` 对象中的键必须恰好覆盖同级对象的全部业务字段（测试会校验完整性）。
- 示例 A/B 的 `response.evidence` 展示的是 v1.0 **目标契约**；当前实现证据只落在 `answer.events[].sources`，见第 7.2 节。

---

## 4. 字段映射表（B 内部 ↔ D 数据库/接口）

| B 侧字段 | 类型 | D 数据库/接口字段 | 方向 | 说明 |
|----------|------|------------------|------|------|
| `Message.msg_id` | string | `messages.msg_id` | D→B | 主键，引用锚点 |
| `Message.group_id` | string | `messages.group_id` | D→B | 鉴权与召回范围 |
| `Message.sender_id` | string | `messages.sender_id` | D→B | 稳定发送者 ID |
| `Message.sender_name` | string | `messages.sender_name` | D→B | 展示名，可空 |
| `Message.content` | string | `messages.content` | D→B | 引用校验基准 |
| `Message.send_time` | string | `messages.send_time`（当前 D 为 `sent_at`，需映射/改名） | D→B | 统一 `YYYY-MM-DD HH:MM:SS` |
| `Message.msg_status` | enum | `messages.msg_status` | D→B | `revoked` 不进候选 |
| `Message.edited_at` | string | `messages.edited_at` | D→B | 编辑时间，可空 |
| `Message.version` | integer | `messages.version` | D→B | 编辑递增 |
| `Candidate.msg_id` | string | `messages.msg_id` | D→B | 直接取值 |
| `Candidate.content` | string | `messages.content`（对应 `version`） | D→B | 不得截断/改写 |
| `Candidate.sender` | string | `messages.sender_name` | D→B | 展示用 |
| `Candidate.send_time` | string | `messages.send_time` | D→B | 展示用 |
| `Candidate.group_id` | string | `messages.group_id` | D→B | 必须落在授权群 |
| `Candidate.category` | enum | `messages.category` 或 D 派生 | D→B | event/task/file |
| `Candidate.file_hint` | string | `messages.file_name` 或 D 派生 | D→B | 附件线索 |
| `Candidate.score` | number | D 检索分数（无固定列） | D→B | 由 D 计算 |
| `Candidate.sources` | string[] | D 检索来源（无固定列） | D→B | 由 D 计算 |
| `Candidate.version` | integer | `messages.version` | D→B | 引用版本 |
| `Candidate.low_confidence` | boolean | D 阈值判断（无固定列） | D→B | 由 D 标记 |
| `Candidate.degraded` | boolean | D 降级标记（无固定列） | D→B | 由 D 标记 |
| `parse.keywords` | string[] | 不落库（B 解析产物） | B 内部 | 当前未传入 filters，见 7.1 |
| `parse.time.start/end` | string | `messages.event_time` 或 `messages.send_time` | B→D | D 需补 `event_time` 列 |
| `parse.time_field` | enum | 无对应列，D 按此选择过滤/排序的时间列 | B→D | event_time/send_time |
| `parse.category` | enum | `messages.category` | B→D | 硬过滤或软加权 |
| `parse.file_hint` | string | `messages.file_name` | B→D | 文件名线索 |
| `filters.category` | enum | `messages.category` | B→D | 硬过滤 |
| `filters.file_hint` | string | `messages.file_name` | B→D | 硬过滤 |
| `filters.time_range` | object | `messages.event_time` / `messages.send_time` | B→D | 闭区间 |
| `filters.time_field` | enum | 同上，决定用哪一列 | B→D | 默认 event_time |
| `filters.group_id` | string | `messages.group_id` + D 授权范围 | D 权威 | 只能 D 决定 |
| `filters.soft_boost` | object | 无对应列，D 用作排序加权 | B→D | 不参与硬过滤 |
| `filters._limit` | integer | D 查询 `LIMIT` | B→D | 建议值，可忽略 |
| `Session.session_id` | string | `sessions.session_id` | D↔B | D 生成并持久化 |
| `Session.last_query` | string | `sessions.last_query`（入参键名 `query`） | D↔B | 见 7.3 |
| `Session.status` | enum | `sessions.status` | D↔B | B 计算，D 持久化 |
| `Session.parse` | object | `sessions.parse_json` | D↔B | B 产出，D 存 JSON |
| `Session.updated_at` | string | `sessions.updated_at` | D↔B | D 维护 |
| `Session.ask_count` | integer | `sessions.ask_count` | D↔B | B 计算，D 持久化 |
| `Session.result_summary` | string | `sessions.result_summary` | D↔B | 可空 |
| `Citation.msg_id` | string | `messages.msg_id` | B→D | 与候选一致 |
| `Citation.quote` | string | `messages.content` 的子串 | B→D | B 已校验 |
| `Citation.field` | string | 无对应列，B 事件字段名 | B→D | 可为 null |
| `Citation.version` | integer | `messages.version` | D→B | 由 D 注入 |

---

## 5. 权限字段与 B 侧承诺

### 5.1 只能由 D 填的字段

| 字段 | 类型 | 含义 | 约束 |
|------|------|------|------|
| `group_scope` | string[] | 本次查询授权的群列表 | 通过 `retrieve` / `process` 的 `group_scope` 参数传入；必须由 D 的身份推导，不能信任自然语言或 LLM 解析结果 |
| `allowed_msg_ids` | string[] | 本次允许进入候选的消息白名单 | 通过 `retrieve` / `process` 的 `allowed_msg_ids` 参数传入；为空表示无候选 |
| `user_scope` | object | 用户身份与可见范围（`user_id`、`role`、`groups` 等） | D 自行定义结构；B 不读取、不校验 |

> `group_scope` 与 `allowed_msg_ids` **至少传一个**。两个都不传时 B 返回
> `permission_denied`，不会把"没有权限参数"当成"可以查全量"。

### 5.2 B 侧承诺

- **不自己查库**：`app/facade.py` 不建立数据库连接，不 import `app.db` / `app.search_hybrid`；候选只来自 `candidate_provider`。
- **跨群不扩召回**：B 不会把 `filters.group_id` 改写成其他群，也不会自行补充候选。
- **只处理 D 传入的候选**：`retrieve` / `build_answer` / `process` 只消费 `candidates`，不读取白名单外的消息。
- **引用可溯源**：`quote` 必须是候选 `content` 的子串，校验失败即丢弃；无证据的字段留 `null`，不编造。
- **边界断言**：`retrieve` 会丢弃 `group_id` 不在 `group_scope`、`msg_id` 不在 `allowed_msg_ids` 的候选并计数（DEBUG 时写入 `debug`），越权候选不进入答案。
- **不做完整鉴权**：B 只做上述二次校验；`user_scope`、`revoked` 等仍需 D 在 `candidate_provider` 内收口。

### 5.3 D 侧义务

- `candidate_provider` 必须先按 `group_id` + `user_scope` + `allowed_msg_ids` 过滤，再返回候选。
- `parse.filters.explicit` 里即使出现 `group_id`，也只能作为**缩小范围**的提示；与 D 授权范围冲突时以 D 为准。
- 撤回（`revoked`）消息、越权消息、白名单外消息一律不得进入候选。
- 候选 `content` 必须与 `version` 对应的原文一致；编辑后的旧版原文不得复用。

### 5.4 四种"查不到"状态（T2）

| status | 含义 | 用户可见文案（固定一句） |
|--------|------|--------------------------|
| `no_data` | 授权范围内确实没有相关资料，且检索未降级 | 群里没有找到相关资料 |
| `low_relevance` | 有候选但相关度低于阈值（阈值见 `runs/calibration.md`，`MIN_SCORE=0.70`） | 没有找到明确匹配，这几个可能相关 |
| `retrieval_degraded` | 向量检索不可用/异常，降级到关键词；返回必带 `degraded: true` 与 `degraded_reason` | 本次仅使用关键词检索，结果可能不完整 |
| `permission_denied` | 缺少权限参数，或候选来源越权 | 没有权限查看这些群聊的消息，请先加入对应群聊 |

四种文案互不相同；尤其 `retrieval_degraded` 不得表述成 `no_data`。`uncertain`
是答案校验层的独立状态，不在这四态之内。

---

## 6. 错误码表（与 `app/errors.py` 对齐）

| code | 常量 | HTTP | 触发条件 | D 应该怎么处理 |
|------|------|------|----------|----------------|
| 1001 | `EMPTY_QUERY` | 400 | `query` 为空或全空白（`agent.py` L620 / L710） | 直接提示用户输入内容，不要重试 |
| 2001 | `LLM_TIMEOUT` | 504 | 大模型调用超过 30 秒（`llm.py`） | 可重试 1 次；仍失败则提示稍后再试 |
| 2002 | `LLM_FORMAT_ERROR` | 502 | 模型返回非法 JSON，已重试 1 次（`LLMFormatError`，`api.py` 映射） | 提示模型格式异常；不要把模型原文透给用户 |
| 2003 | `LLM_UNAVAILABLE` | 503 | 大模型未配置、网关拒绝（凭据/额度/模型下线）、流式响应中断 | 提示稍后重试；不要重复解析 |
| 3001 | `DB_ERROR` | 500 | D 侧数据库操作失败；facade 自身不抛此码 | D 在自己的 DB 层捕获并映射 |
| 4001 | `GUARD_REJECTED` | 400 | 注入/敏感内容被 guard 拒绝；或缺少 `session_id`（`api.py` L107） | 提示用户调整内容或先完成检索 |
| 5000 | `INTERNAL_ERROR` | 500 | 未预期异常，`api.py` 兜底 | 记录 `request_id`，提示稍后重试 |

| 异常类型 | 是否有 code | 触发条件 | D 应该怎么处理 |
|----------|-------------|----------|----------------|
| `LLMFormatError` | 否（由 api 层映射为 2002） | JSON 两次解析失败（`llm.py`） | 捕获后按 2002 处理 |
| `ProviderUnavailable` | 否（`llm._call_llm` 已折叠为 2003） | 未配置 / 网关拒绝 / 流式中断（`providers.py`） | 按 2003 提示，不重试 |
| `TypeError` | 否 | `candidate_provider` 不可调用（`facade.py` L147） | D 的接入代码错误，按 500 处理并修代码 |
| provider / store 抛出的异常 | 否 | 原样向上传递（facade 不吞） | 按依赖类型映射为 503 / 504，并保留原始日志 |

统一响应外壳（B 的 HTTP 层约定，D 复用即可）：

```json
{"code": 2001, "message": "大模型服务响应超时，请稍后重试", "data": null}
```

---

## 7. 已知差距与修复状态

以下为当前代码与 v1.0 契约的差异，D 接入前必须知道。

**7.1 `filters` 不含检索词（P0，已修复 v1.2）**
`agent.build_search_filters` 现在产出 `keywords`（最多 8 个，来自 `parse.keywords`）。
D 的 `candidate_provider` 必须用它做关键词检索；`time_range / category / file_hint / group_id`
仍按原语义使用。旧的“filters 里没有查询词”说法作废。

**7.2 `process` 的 `evidence` 断链（P0，已修复 T3）**
`validate_answer` 现在从 `events[].sources` 派生并返回扁平 `evidence`：
`[{msg_id, quote, field, version, edited}]`，`facade.process` 直接透出。
引用仍需先过 L1（quote 必须是原文子串）、L3（授权归属）、L4（revoked 禁引）；被拒引用写入 `conflicts`，不会静默丢弃。

**7.3 `Session` 键名 `query` vs `last_query`**
D 持久化字段为 `last_query`，而 `facade._dict_to_session` 读取的键名是 `query`。调用 `process` 前请映射，B 不改键名。

**7.4 D 侧表结构缺列（部分缓解）**
D 的 `messages` 只有 `id / content / group_label / sent_at / metadata / revision`。
B 侧只读适配器 `backend/app/d_source.py` 已做如下映射：
`id→msg_id`、`content→content`、`sent_at→send_time`、`group_label→group_id`、`revision→version`、
`metadata.sender→sender`、`metadata.category→category`、`metadata.file_name` 或 `metadata.fields.文件→file_hint`。
仍缺：`sender_id / sender_name / msg_status / edited_at / event_time`。因此
revoked 与 edited 无法从 D 库识别（默认 `normal`），`time_field=event_time` 的时间过滤不可用；
需要 D 补列后才能满足完整的版本与时间过滤契约。

**7.5 生产 HTTP 尚未接 facade**
`api.py` 仍 import `agent.answer_flow` / `revise_flow`，`/api/search`、`/api/answer_ask` 走旧链路；facade 目前只被 `tests/test_facade.py` 调用。旧链路按第 9 节保留，新接入统一走 facade。

---

## 8. D 接入方式

### 8.1 `candidate_provider(filters: dict) -> list[dict]`

B 的 facade **完全不碰业务库**。所有候选检索由 D 实现；内部可以是 SQL、FTS、向量库或远端服务。

**契约**：只认第 2.2 节 `Candidate` 字段；必须完成第 5.3 节权限收口。

示例（伪代码，按 D 自己的库实现）：

```python
def candidate_provider(filters: dict) -> list[dict]:
    """D 实现：filters 来自 facade.retrieve；本函数负责查库与权限收口。"""
    # filters["keywords"] 是用户查询词（7.1），必须参与检索
    rows = my_db.search_messages(
        keywords=filters["keywords"],
        group_scope=filters["group_scope"],
        allowed_msg_ids=filters["allowed_msg_ids"],
        category=filters["category"],
        file_hint=filters["file_hint"],
        time_range=filters["time_range"],
        time_field=filters["time_field"],
        exclude_status={"revoked"},
        limit=filters["_limit"],
    )
    return [
        {
            "msg_id": r.msg_id,
            "content": r.content,
            "sender": r.sender_name,
            "send_time": r.send_time,
            "group_id": r.group_id,
            "category": r.category,
            "file_hint": r.file_name,
            "score": r.score,
            "sources": r.sources,
            "version": r.version,
        }
        for r in rows
    ]
```

### 8.2 `SessionStore`（实现 Protocol）

```python
class SessionStore(Protocol):
    def get(self, session_id: str) -> Optional[Session]: ...
    def save(self, session: Session) -> None: ...
    def touch(self, session_id: str) -> None: ...
```

D 可以继续用 B 提供的 `InMemorySessionStore`（`from app.session import InMemorySessionStore`），
或自行做持久化实现（SQLite / Redis / 远程服务）。

### 8.3 B 侧已提供 D SQLite 只读适配器（P0-1）

`backend/app/d_source.py` 可直接读 D 的库，不修改 D 任何表：

```python
from app.d_source import DStoreCandidateProvider, authorized_group_keys

scope = authorized_group_keys("/data/qunyi.db", workspace="ws-xxx")
provider = DStoreCandidateProvider(
    "/data/qunyi.db", workspace="ws-xxx", group_scope=scope
)
response = process(query, session, provider, store, group_scope=scope)
```

- 只读：不会建表、不会写入；库不存在时抛 `sqlite3.OperationalError`。
- 权限：`group_scope` 为空或无授权群时返回空候选，不会退化为全量。
- 字段映射与三组实测响应见 [`docs/D_integration.md`](D_integration.md)，测试见
  `backend/tests/test_d_integration.py`。

### 8.4 大模型配置

facade 统一走 `app.llm.chat_json(messages)`；上游由 `LLM_PROVIDER` 选择，D 只需按所选通路设变量。

**通路一：`LLM_PROVIDER=openai`（默认）**

| 变量 | 说明 |
|------|------|
| `LLM_BASE_URL` | OpenAI 兼容 endpoint（末尾**不能**带 `/chat/completions`） |
| `LLM_API_KEY` | 密钥 |
| `LLM_MODEL` | 模型名（如 `gpt-4o-mini` / `glm-4` / `deepseek-chat`） |

示例 PowerShell：

```powershell
$env:LLM_BASE_URL = "https://api.deepseek.com/v1"
$env:LLM_API_KEY  = "sk-xxx"
$env:LLM_MODEL    = "deepseek-chat"
```

**通路二：`LLM_PROVIDER=workbuddy`（WorkBuddy 云服务免密钥网关）**

| 变量 | 说明 |
|------|------|
| `WB_ENDPOINT` | 云服务应用域名，如 `https://xxx.workbuddy.link` |
| `WB_PUBLISHABLE_KEY` | `publicConfig.publishableKey`（不带权限，服务端按 Origin 校验） |
| `WB_MODEL` | 模型 id，取自 `GET /.cloud/llm/models`；不填则回退 `LLM_MODEL` |
| `WB_ORIGIN` | 可选，默认取 `WB_ENDPOINT` 的 `协议://域名` |

```powershell
$env:LLM_PROVIDER = "workbuddy"
$env:WB_ENDPOINT = "https://xxx.workbuddy.link"
$env:WB_PUBLISHABLE_KEY = "..."
$env:WB_MODEL = "<模型 id>"
python -m scripts.check_workbuddy   # 自检：配置 → 模型目录 → 一次真实对话
```

facade 的调用方式两条通路完全一致，无需任何改动；协议细节见 `docs/WORKBUDDY.md`。

---

## 9. 典型调用链路

```python
from app.facade import analyze_query, build_answer, process, retrieve
from app.session import InMemorySessionStore

store = InMemorySessionStore()
session = store.get("sess-abc")  # Session 对象或 None

# --- 策略 A：一次调用端到端（推荐） ---
response = process(
    query="这周末的团建在哪",
    session={
        "session_id": "sess-abc",
        "status": session.status if session else "parsed",
        "query": session.last_query if session else None,   # 注意键名映射
        "parse": session.parse if session else {},
        "ask_count": session.ask_count if session else 0,
    },
    candidate_provider=my_provider,   # 见 8.1，内部完成权限收口
    store=store,
    today="2026-10-26",
    group_scope=["g-001"],            # T2：至少传一个权限参数
    allowed_msg_ids=allowed_ids,
)

# --- 策略 B：分阶段调用（需要更细粒度控制时） ---
parse = analyze_query("这周末的团建在哪", today="2026-10-26")
if parse.get("ambiguous"):
    # D 自己决定要不要追问；facade 也会，双轨都行
    ...
retrieval = retrieve(
    parse, my_provider, limit=20,
    group_scope=["g-001"], allowed_msg_ids=allowed_ids,
)
if retrieval["status"] == "permission_denied":
    ...  # 无权限，直接返回
if not retrieval["results"]:
    ...  # 无结果，直接返回
answer = build_answer(parse, retrieval["results"], db_path=None)
```

---

## 10. 向后兼容说明

- `app.agent.answer_flow` / `revise_flow` / `SESSION_STORE` **保留不删**，内部 api.py 仍在用。
- D **新增接入** 请用本文件的 facade；旧的 answer_flow 只保证现有集成不变，不再新增功能。
- 所有全局可变状态（`SESSION_STORE`、写死的库路径、环境变量）都在 facade 之外：
  - 不 import facade 之外的模块不会触发任何全局初始化
  - facade 内部所有有状态的东西都通过参数传入

---

## 11. 文件位置索引

| 文件 | 用途 |
|------|------|
| `backend/app/facade.py` | **唯一** 对外入口，5 个函数 |
| `backend/app/session.py` | SessionStore Protocol + InMemorySessionStore |
| `backend/app/agent.py` | facade 内部依赖的纯函数（parse_query / build_search_filters / generate_answer 等） |
| `backend/app/errors.py` | AppError / 错误码 |
| `backend/app/llm.py` | chat_json 单例（D 不配，B 读环境变量） |
| `backend/tests/test_facade.py` | 5 个路径的 fake provider + 内存 store 测试 |
| `backend/tests/test_docs_examples.py` | 本文件三组示例的可解析/字段/引用溯源校验 |
| `backend/docs/examples/*.json` | 三组请求/响应示例 |

---

## 12. 测试

```powershell
cd backend
python -m pytest tests/test_docs_examples.py -q   # 文档示例契约
python -m pytest tests/test_facade.py -q          # 5 个路径
python -m pytest -q                               # 全量
```
