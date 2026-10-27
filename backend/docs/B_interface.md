# B→D 交付接口（app.facade）

模块入口：`backend/app/facade.py`  
**唯一** 给 D 调用的 Python 模块；所有全局可变状态、业务库查询、LLM 调用都通过依赖注入隔离。  
D **不得** 直接 import `app.agent` / `app.search_hybrid` / `app.api`；通过本文件暴露的 5 个函数调用。

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
    candidate_provider: Callable[[dict], list[dict]],
    limit: int = 20,
) -> dict:
    """用 parse 构造 filters，调用 candidate_provider 拉候选。

    返回：{"results": [...], "debug": {...}}
      results 项：{"msg_id", "content", "sender", "send_time", "score",
                   "sources", "low_confidence"?, "degraded"? ...}
      debug：仅 settings.DEBUG=true 时填充。

    candidate_provider 收到的 filters：
      {
        "category": "event" | None,
        "file_hint": str | None,
        "time_range": {"start": "...", "end": "..."} | None,
        "time_field": "event_time" | "send_time",
        "group_id": str | None,
        "soft_boost": {"category", "file_hint"},
        "_limit": 20,          # D 可参考，也可忽略
      }"""


# -------- 答案层 --------

def build_answer(
    parse: dict,
    candidates: list[dict],
    db_path: Optional[str] = None,
) -> AnswerResult:
    """把候选消息交给大模型生成答案。

    返回 dict：
      {
        "status":   "ok" | "uncertain" | "no_result",
        "answer":   str,
        "events":   [...],
        "evidence": [{"msg_id": "m001", "quote": "这周六团建，下午6点在南门集合"}],
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
    limit: int = 20,
) -> dict:
    """解析 → 必要时追问一次 → 更正判定 → 检索 → 答案生成 → 会话写回。

    返回统一响应结构：
      {
        "status":      "answer" | "ask" | "no_result" | "low_relevance" |
                       "uncertain" | "degraded",
        "parse":       {...},                 # analyze_query 产出
        "answer":      {...} | None,          # build_answer 产出
        "evidence":    [...],
        "debug":       {...},                 # 仅 DEBUG 时填充
        "suggestions": ["放宽时间范围..."],   # 仅 no_result
        "assumptions": ["未确认时间范围..."], # parse 里有 ambiguous 时
        "question":    "你想查哪一类？",      # 仅 ask
        "options":     [...],                 # 仅 ask
        "low_confidence": true,               # 仅 low_relevance
        "degraded":     true,                 # 仅 degraded
        "degraded_reason": "vector_unavailable",  # 仅 degraded
        "context_lost": true,                 # 会话过期但用户在更正
        "message":      "...",                # 给用户的提示语
      }"""
```

---

## 2. D 需要提供什么

### 2.1 `candidate_provider(filters: dict) -> list[dict]`

B 的 facade **完全不碰业务库**。所有候选检索由 D 实现本函数，内部可以是：

- `SELECT * FROM messages WHERE ...`（SQLite/MySQL）
- 调用 B 的内部 `app.search_hybrid.hybrid_search`（D 自己决定是否走 B 的混合检索）
- 远端服务 / 向量库 / 缓存

**契约**（B 只认这几个字段）：

| 字段 | 必填 | 说明 |
|------|------|------|
| `msg_id` | ✅ | 消息唯一 ID（引用来源用） |
| `content` | ✅ | 原文（答案生成用） |
| `sender` | 建议 | 发送人（答案展示用） |
| `send_time` | 建议 | 发送时间（答案展示用） |
| `low_confidence` | 可选 | 候选相关度低，facade 会标为 `low_relevance` |
| `degraded` | 可选 | 检索已降级，facade 会在响应里透传 |

示例实现（D 可照抄）：

```python
from app.search_hybrid import hybrid_search

def candidate_provider(filters: dict) -> list[dict]:
    """D 实现：filters 来自 facade.retrieve，内部怎么查库 D 自己决定。"""
    query = filters.pop("_query", " ".join(
        filters.get("keywords", [])
    )) if "keywords" in filters else ""
    # 直接调用 B 已有的混合检索（这是最省事的默认做法）
    out = hybrid_search(query, limit=filters.get("_limit", 20), filters=filters)
    return out["results"]
```

### 2.2 `SessionStore`（实现 Protocol）

```python
class SessionStore(Protocol):
    def get(self, session_id: str) -> Optional[Session]: ...
    def save(self, session: Session) -> None: ...
    def touch(self, session_id: str) -> None: ...
```

D 可以继续用 B 提供的 `InMemorySessionStore`（`from app.session import InMemorySessionStore`），或自行做持久化实现（SQLite / Redis / 远程服务）。

### 2.3 大模型配置

facade 统一走 `app.llm.chat_json(messages)`，D 只需要设三个环境变量：

| 变量 | 说明 |
|------|------|
| `LLM_BASE_URL` | OpenAI 兼容 endpoint（末尾**不能**带 `/chat/completions`） |
| `LLM_API_KEY`  | 密钥 |
| `LLM_MODEL`    | 模型名（如 `gpt-4o-mini` / `glm-4` / `deepseek-chat`） |

示例 PowerShell：
```powershell
$env:LLM_BASE_URL = "https://api.deepseek.com/v1"
$env:LLM_API_KEY  = "sk-xxx"
$env:LLM_MODEL    = "deepseek-chat"
```

---

## 3. 错误码

B 的业务异常定义在 `app/errors.py`，D 可以在 `except` 里捕获：

| 异常类型 | code | 含义 |
|----------|------|------|
| `AppError(GUARD_REJECTED)` | 1001 | 空查询 / 非法输入 |
| `AppError(LLM_TIMEOUT)` | 3001 | 大模型调用超时（30 秒） |
| `LLMFormatError` | 3002 | 大模型返回非法 JSON（已重试一次） |
| `RuntimeError` | — | `LLM_BASE_URL` / `LLM_MODEL` 未配置 |

D 层可以统一：

```python
from app.errors import AppError, LLM_TIMEOUT

try:
    facade.process(...)
except AppError as e:
    # e.code / e.message
except RuntimeError as e:
    # LLM 未配置
```

---

## 4. 示例：D 的典型调用链路

```python
from app.facade import facade, process, retrieve
from app.session import InMemorySessionStore
from app.search_hybrid import hybrid_search

# D 初始化
store = InMemorySessionStore()
session = store.get("sess-abc") or {}

# --- 策略 A：一次调用端到端（推荐） ---
response = facade.process(
    query="这周末的团建在哪",
    session=session,
    candidate_provider=lambda f: hybrid_search(" ".join(f.get("keywords") or []),
                                              limit=f.get("_limit", 20),
                                              filters=f)["results"],
    store=store,
    today="2026-10-26",
)
store.save(response)   # facade 内部已 save，D 可不再做

# --- 策略 B：分阶段调用（需要更细粒度控制时） ---
parse = facade.analyze_query("这周末的团建在哪", today="2026-10-26")
if parse.get("ambiguous"):
    # D 自己决定要不要追问；facade 也会，双轨都行
    ...
retrieval = facade.retrieve(parse, my_provider, limit=20)
if not retrieval["results"]:
    # 无结果，直接返回
    ...
answer = facade.build_answer(parse, retrieval["results"], db_path="memory.db")
```

---

## 5. 向后兼容说明

- `app.agent.answer_flow` / `revise_flow` / `SESSION_STORE` **保留不删**，内部 api.py 仍在用。
- D **新增接入** 请用本文件的 facade；旧的 answer_flow 只保证现有集成不变，不再新增功能。
- 所有全局可变状态（`SESSION_STORE`、写死的库路径、环境变量）都在 facade 之外：
  - 不 import facade 之外的模块不会触发任何全局初始化
  - facade 内部所有有状态的东西都通过参数传入

---

## 6. 文件位置索引

| 文件 | 用途 |
|------|------|
| [app/facade.py](file:///C:/Users/CLY/Documents/Trae-CN/temp_20261025_02/backend/app/facade.py) | **唯一** 对外入口，5 个函数 |
| [app/session.py](file:///C:/Users/CLY/Documents/Trae-CN/temp_20261025_02/backend/app/session.py) | SessionStore Protocol + InMemorySessionStore |
| [app/agent.py](file:///C:/Users/CLY/Documents/Trae-CN/temp_20261025_02/backend/app/agent.py) | facade 内部依赖的纯函数（parse_query / build_search_filters / generate_answer 等） |
| [app/errors.py](file:///C:/Users/CLY/Documents/Trae-CN/temp_20261025_02/backend/app/errors.py) | AppError / 错误码 |
| [app/llm.py](file:///C:/Users/CLY/Documents/Trae-CN/temp_20261025_02/backend/app/llm.py) | chat_json 单例（D 不配，B 读环境变量） |
| [tests/test_facade.py](file:///C:/Users/CLY/Documents/Trae-CN/temp_20261025_02/backend/tests/test_facade.py) | 5 个路径的 fake provider + 内存 store 测试 |

---

## 7. 测试

```powershell
cd backend
python -m pytest tests/test_facade.py -q
# 全量
python -m pytest -q    # → 114 passed
```
