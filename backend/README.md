# 群忆 · 后端（B 位：AI 与检索）

把 QQ/微信群聊记录变成可检索的"活动 / 办事 / 文件"线索，并在回答中给出**逐字原文引用**。

技术栈：Python 3.11+ / FastAPI / SQLite(FTS5) / sentence-transformers + numpy / RRF 混合检索。

---

## 1. 快速开始

```bat
REM Windows
scripts\run.bat
```

```bash
# macOS / Linux / Git Bash
bash scripts/run.sh
```

脚本依次执行：建库 → 导入 `scripts/sample_messages.json` → 启动服务（`http://127.0.0.1:8000`）。

手动分步执行：

```bat
cd backend
python -m scripts.seed_demo
python -m uvicorn app.api:app --host 127.0.0.1 --port 8000 --reload
```

验证服务：

```powershell
Invoke-RestMethod http://127.0.0.1:8000/api/health
# {"code":0,"message":"ok","data":null}
```

安装依赖：

```bat
pip install -r requirements.txt
```

---

## 2. 环境变量

| 变量 | 必填 | 默认值 | 说明 |
|------|------|--------|------|
| `LLM_BASE_URL` | 是（AI 功能） | 无 | OpenAI 兼容接口地址，如 `https://api.deepseek.com/v1` |
| `LLM_API_KEY` | 是（AI 功能） | 无 | 密钥 |
| `LLM_MODEL` | 是（AI 功能） | 无 | 模型名，如 `deepseek-chat` |
| `LLM_TIMEOUT` | 否 | `30` | 单次大模型调用超时（秒） |
| `EMBED_MODEL` | 否 | `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2` | 语义检索模型 |
| `EMBED_BATCH_SIZE` | 否 | `64` | 向量化批大小 |
| `TOP_K_DEFAULT` | 否 | `50` | 混合检索返回条数 |
| `EXTRACT_BATCH_SIZE` | 否 | `20` | 线索抽取每批消息数 |
| `MEMORY_DB_PATH` | 否 | `backend/memory.db` | SQLite 文件路径 |
| `HF_ENDPOINT` | 否 | 无 | 国内下载模型建议设为 `https://hf-mirror.com` |

未配置 `LLM_BASE_URL` / `LLM_API_KEY` 时：**导入和关键词检索可用**，但查询解析、线索抽取、答案生成会返回错误码（`2001` / `2002`）。

---

## 3. 接口文档

Base URL：`http://127.0.0.1:8000`，所有接口统一返回：

```json
{ "code": 0, "message": "ok", "data": {} }
```

- `code=0` 表示成功；非 0 见第 4 节错误码。
- 失败时 `data` 恒为 `null`。

### 3.1 健康检查

`GET /api/health`

```json
{ "code": 0, "message": "ok", "data": null }
```

### 3.2 检索 / 对话

`POST /api/search`

请求：

```json
{ "query": "帮我找上周的火锅局照片", "session_id": null }
```

- `session_id` 可为 `null`，服务端会生成并返回；后续追问/更正需带上同一个 `session_id`。

响应 `data.status` 有 3 种：

**(1) `answer` —— 已给出带引用答案**

```json
{
  "code": 0,
  "message": "ok",
  "data": {
    "status": "answer",
    "session_id": "3f2a9c1d4b6e4f0a8c7d5e2b1a0f9c8d",
    "message": "",
    "assumptions": [],
    "parse": {
      "keywords": ["火锅", "照片"],
      "time": { "type": "relative", "start": "2026-10-19 00:00:00", "end": "2026-10-25 23:59:59", "raw": "上周" },
      "category": "file",
      "file_hint": null,
      "ambiguous": null
    },
    "candidates": [
      {
        "msg_id": "m003",
        "content": "昨天火锅局的照片我传到群文件了，名字叫火锅局合照.zip，大家自己下载",
        "sender": "李明",
        "send_time": "2026-10-25 21:40:00",
        "score": 0.0325,
        "sources": ["kw", "vec"]
      }
    ],
    "result": {
      "status": "ok",
      "answer": "火锅局照片在群文件，文件名为火锅局合照.zip",
      "events": [
        {
          "summary": "火锅局合照",
          "time": null,
          "time_raw": "昨天",
          "location": null,
          "participants": null,
          "sources": [{ "msg_id": "m003", "quote": "火锅局的照片我传到群文件了" }],
          "msg_ids": ["m003"]
        }
      ]
    }
  }
}
```

- `candidates[].sources` 标注命中来源：`kw`（关键词）/ `vec`（语义）。
- `clusters` 是事件关联结果：同一活动的多条消息合并为一个 cluster；若有人改期/取消，
  `clusters[].corrections` 会给出 `{"type": "reschedule|cancel", "new_time": ..., "from_msg_id": ...}`，
  且 `clusters[].event_time` 始终保留**原定时间**，两个时间都会出现在答案里。
- `result.events[].sources[].quote` 已由服务端校验为对应 `msg_id` 原文的**逐字子串**。

**(2) `ask` —— 需要一次追问（整会话最多 1 次）**

```json
{
  "code": 0,
  "message": "ok",
  "data": {
    "status": "ask",
    "session_id": "3f2a9c1d4b6e4f0a8c7d5e2b1a0f9c8d",
    "question": "你指的是哪段时间的活动？",
    "options": ["最近一周", "上个月", "都可以"],
    "parse": { "keywords": ["活动"], "time": { "type": "none", "start": null, "end": null, "raw": null }, "category": "any", "file_hint": null, "ambiguous": { "field": "time", "question": "你指的是哪段时间的活动？", "options": ["最近一周", "上个月", "都可以"] } }
  }
}
```

前端拿到 `ask` 后，把用户点选/输入的答案发给 `/api/answer_ask`。

**(3) `no_result` —— 没有可支撑回答的原文**

```json
{
  "code": 0,
  "message": "ok",
  "data": {
    "status": "no_result",
    "session_id": "3f2a9c1d4b6e4f0a8c7d5e2b1a0f9c8d",
    "suggestions": ["把时间范围放宽到最近一个月", "只按类别搜索（活动/办事/文件）", "换一个更具体的关键词"]
  }
}
```

不会调用大模型编造回答。前端直接渲染 `suggestions` 即可。

### 3.3 回答追问 / 条件更正

`POST /api/answer_ask`

请求：

```json
{ "session_id": "3f2a9c1d4b6e4f0a8c7d5e2b1a0f9c8d", "reply": "上个月" }
```

- 既用于回答 `ask`，也用于改条件："不是上周，是上个月"、"不是文件，是活动通知"。
- 改条件 = 替换，不是叠加（旧条件不会残留）。
- 响应结构与 `/api/search` 相同（`answer` / `no_result`）。

### 3.4 导入群聊 JSON

`POST /api/import`

请求：

```json
{ "json_path": "D:/data/class_group.json" }
```

文件格式：

```json
[
  {
    "msg_id": "m001",
    "group_id": "g_class_2023",
    "sender": "班长",
    "sender_id": "u1001",
    "send_time": "2026-10-20 09:12:00",
    "content": "这周六团建，下午6点在南门集合"
  }
]
```

响应：

```json
{
  "code": 0,
  "message": "ok",
  "data": {
    "inserted": 8,
    "skipped": 0,
    "total": 8,
    "embedded": 8,
    "extract": { "leads": 5, "dropped": 1, "batches": 1 }
  }
}
```

- 幂等：`msg_id` 已存在则跳过（重复导入 `inserted=0`）。
- `embedding_error` 字段出现表示向量生成失败（模型未下载/离线），原文仍已导入。
- `extract.error` 字段出现表示线索抽取失败（大模型未配置/超时），原文仍已导入。

---

## 4. 错误码

| code | HTTP | message | 触发场景 |
|------|------|---------|----------|
| `0` | 200 | `ok` | 成功 |
| `1001` | 400 | 查询内容为空，请输入你想查找的内容 | `query` / `reply` 为空 |
| `2001` | 504 | 大模型服务响应超时，请稍后重试 | 调用超时（不重试） |
| `2002` | 502 | 大模型返回内容格式异常，已重试仍失败 | 连续两次非 JSON |
| `3001` | 500 | 数据库操作失败，请稍后重试 | SQLite 异常 |
| `4001` | 400 | 内容未通过安全校验，请调整后重试 | 入参不合法（如 `session_id` 为空、导入文件不存在） |
| `5000` | 500 | 服务内部错误，请稍后重试 | 未捕获异常 |

失败响应示例：

```json
{ "code": 1001, "message": "查询内容为空，请输入你想查找的内容", "data": null }
```

---

## 5. 测试

```bat
cd backend
python -m pytest -q
```

测试不访问网络、不调用真实大模型（全部 mock），向量模型在 session 级复用。
测试固定 `HF_HUB_OFFLINE=1`，只读本地模型缓存；换机器首次跑测试前先执行一次
`python -m scripts.seed_demo` 下载模型。

---

## 6. 目录结构

```
backend/
├── app/
│   ├── api.py          # FastAPI 路由 + 全局异常处理器
│   ├── agent.py        # 决策链路：解析 → 追问 → 更正 → 检索 → 答案
│   ├── answer.py       # 带引用答案生成
│   ├── validate.py     # 引用校验（quote 必须是原文子串）
│   ├── cluster.py      # 事件关联（保守合并 + 改期/取消标记）
│   ├── db.py           # 建表 + FTS5 + 触发器
│   ├── ingest.py       # 消息导入（幂等）+ 向量落库
│   ├── extract.py      # 导入时批量线索抽取
│   ├── search_kw.py    # FTS5 关键词检索
│   ├── search_vec.py   # 向量语义检索
│   ├── search_hybrid.py# RRF 混合检索
│   ├── embed.py        # sentence-transformers 客户端
│   ├── errors.py       # 错误码表
│   ├── llm.py          # chat_json（temperature=0，超时 30s，重试 1 次）
│   └── prompts/        # 全部提示词（extract / parse / correct / answer）
├── scripts/            # 一键脚本、示例数据、离线向量构建
├── tests/              # pytest 用例
├── memory.db           # SQLite（首次运行生成）
└── requirements.txt
```

---

## 7. 已知限制

1. **会话缓存在进程内存**：服务重启后 `session_id` 上下文丢失，前端重新发起 `/api/search` 即可。
2. **首次启动慢**：需要下载 embedding 模型（约 500MB），国内建议 `set HF_ENDPOINT=https://hf-mirror.com`。
3. **语义检索全量扫库**：数据量 < 10 万条时用 numpy 矩阵乘法，未引入向量数据库。
4. **不编造**：答案中的每个事实都必须能回溯到 `msg_id` + 原文子串；校验不通过的引用会被丢弃，全部失效则返回 `no_result`。
