# 接入 WorkBuddy 大模型

本文说明后端如何把大模型调用切到 **WorkBuddy 云服务的免密钥大模型网关**，以及哪些已核实、哪些还没验证。

> 结论先行：接入代码已经写完并有单测覆盖，**但还没有用真实凭据跑通一次真实调用**（参见文末「验证状态」）。
> 在拿到真实 `WB_ENDPOINT` / `WB_PUBLISHABLE_KEY` 之前，不要在参赛材料里写「已接入联名版模型」。

---

## 1. 代码结构

```
app/llm.py        chat_json()：唯一对外的调用入口（temperature=0、超时、JSON 重试 1 次）
app/providers.py  get_provider()：按 LLM_PROVIDER 装配上游
                  ├── OpenAICompatibleProvider  非流式 JSON，Authorization: Bearer <key>
                  └── WorkBuddyProvider        SSE 流式，x-wb-webapp-access-key: <publishableKey>
```

上层（`agent.py` / `answer.py` / `extract.py`）只调 `chat_json`，对两条通路完全无感。
切换上游不需要改任何业务代码，也不需要改提示词。

---

## 2. 协议（按官方 SDK 反查，不是猜测）

来源：npm 包 `@tencent-ai/workbuddy-cloud-sdk`（`lib/chunk-*.js`）。

| 项 | 值 |
|----|----|
| 基址 | `{endpoint}/.cloud/llm` |
| 对话 | `POST {endpoint}/.cloud/llm/chat/completions` |
| 模型目录 | `GET {endpoint}/.cloud/llm/models` |
| 凭据头 | `x-wb-webapp-access-key: <publishableKey>`（SDK 常量 `PUBLISHABLE_KEY_HEADER`） |
| 请求体 | `model` / `messages` / `temperature`，且**必须** `stream: true` |
| 响应 | SSE；`data:` 帧为 `ChatCompletionChunk`，`data: [DONE]` 正常结束，`event: error` 表示失败 |

三条容易踩的坑：

1. **网关不支持非流式**。传 `stream: false` 会被直接拒绝（`request_stream_required`），
   所以 `WorkBuddyProvider` 自己拼 SSE，只保留 `delta.content`，丢弃 `reasoning_content`（思维链不可引用）。
2. **Origin 精确匹配**。publishableKey 本身不带权限，服务端校验来源域名；后端默认把
   `WB_ENDPOINT` 的 `协议://域名` 作为 `Origin` 头发出去。域名和 key 不配套会返回 `auth_*`。
3. **错误码前缀有语义**：`auth_` / `quota_` / `request_` / `gateway_` / `model_` / `internal_`，
   已经分别折叠成中文提示（见 `providers._user_message`），不把上游原文透给用户。

---

## 3. 怎么配置

### 3.1 拿到 endpoint 和 publishableKey

在 WorkBuddy 里给这个项目开通云服务（会弹确认框），成功后返回的 `publicConfig` 里就有：

- `endpoint` → 填 `WB_ENDPOINT`
- `publishableKey` → 填 `WB_PUBLISHABLE_KEY`
- `applicationId` → 只在发布/复用应用时用，后端不需要

发布（`workbuddy_sites_deploy`）和开通是两件事；开通只准备环境，不会让服务上线。

### 3.2 选模型

```bash
# PowerShell
$env:LLM_PROVIDER="workbuddy"
$env:WB_ENDPOINT="https://xxx.workbuddy.link"
$env:WB_PUBLISHABLE_KEY="..."
$env:WB_MODEL="<从 /models 里选一个>"
python -m scripts.check_workbuddy
```

自检脚本会依次检查：配置是否齐全 → 模型目录里有没有这个 id → 发一次真实对话并要求返回 JSON。
退出码非 0 表示该步失败，便于接进提交前自检。

### 3.3 运行时

```bat
set LLM_PROVIDER=workbuddy
set WB_ENDPOINT=https://xxx.workbuddy.link
set WB_PUBLISHABLE_KEY=...
set WB_MODEL=...
python -m uvicorn app.api:app --host 127.0.0.1 --port 8000
```

查看当前通路：`GET /api/llm/status`（不返回密钥，只返回 provider / model / 是否齐全 / 缺什么）。

---

## 4. 失败时的表现

| 情况 | 返回 | 用户看到 |
|------|------|----------|
| provider 变量没配全 | `2003` / HTTP 503 | 「WorkBuddy 大模型未配置：缺少 WB_ENDPOINT」 |
| key 与域名不配套 | `2003` / HTTP 503 | 「凭据无效或来源不匹配」 |
| 额度耗尽 / 限流 | `2003` / HTTP 503 | 「额度不足或请求过于频繁」 |
| 流式中断（没收到 `[DONE]`） | `2003` / HTTP 503 | 「流式响应中断，请稍后重试」 |
| 调用超时 | `2001` / HTTP 504 | 不重试，直接返回 |
| 输出不是合法 JSON | `2002` / HTTP 502 | 已重试 1 次仍失败 |

`2003` 是本次新增的错误码，前端按 `message` 展示即可；`data` 恒为 `null`，与既有约定一致。
导入接口遇到这类错误不会失败，只在 `data.extract.error` 里说明（原文照样入库）。

---

## 5. 验证状态

已做（可复现）：

- 协议按官方 SDK 源码核对，不是照记忆写的；
- 15 条单测覆盖：请求 URL/头/`stream=true`、`Origin` 取值与覆盖、SSE 片段拼装
  （含角色帧、心跳注释帧、`reasoning_content` 不计入正文）、`event: error`、缺 `[DONE]`、
  401 映射、`2003` 折叠且不重试、状态接口不泄露 key；
- 原有 77 条与 llm 相关的用例仍然通过（`_call_llm` 钩子保留，mock 方式不变）。

未做（缺条件）：

- 没有真实的 `WB_ENDPOINT` / `WB_PUBLISHABLE_KEY`，**没有跑通过一次真实网关调用**；
- 没有验证 WorkBuddy 联名版的套餐是否允许这种「后端直接调用」的用法——这属于赛事合规问题，
  需要向组委会确认，不能靠代码证明；
- 没有验证网关对 `Origin` 的具体放宽策略；若真实返回 `auth_origin_mismatch`，
  先用 `WB_ORIGIN` 试应用的真实发布域名。

因此：**代码层面「接好了」，运行层面「未验证」**。答辩材料里请按这个口径写。
