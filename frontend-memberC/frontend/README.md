# 群忆 · 前端（成员 C）

对接 `member-d/backend`（成员 D 的后端原型）的完整前端实现。纯静态页面 + 原生 ES Module，
无构建步骤、无第三方依赖、不联网加载任何 CDN 资源。

## 一、为什么需要一个 `serve.py`

成员 D 的后端在 `qunyi/server.py` 里明确写了：

> No cookie auth or permissive CORS; front-end should proxy same-origin.

它**不返回 CORS 头，也不处理 OPTIONS 预检**。所以浏览器从 `127.0.0.1:5173` 直接
`fetch('http://127.0.0.1:8000/v1/...')` 会被同源策略拦下。

因此前端统一请求同源的 `/api/...`，由 `serve.py` 转发到后端：

```
浏览器 → http://127.0.0.1:5173/api/v1/query
                     │  (serve.py 静态 + 反向代理，去掉 /api 前缀)
                     ▼
              http://127.0.0.1:8000/v1/query
```

这样不改后端一行代码，也不需要在生产环境放开 CORS。

## 二、启动

**终端 1：后端**

```bash
cd member-d/backend
python -m qunyi            # 默认 127.0.0.1:8000
```

**终端 2：前端**

```bash
cd frontend
python serve.py            # 或双击 start.bat
```

打开 <http://127.0.0.1:5173>。

**取令牌**：后端首次启动会生成随机令牌，存在 `member-d/backend/state/credentials.json`，
内容是 `{"<令牌>":"demo"}` 形式。Windows 下可以这样看：

```powershell
Get-Content .\state\credentials.json
```

把 key（引号里的那一长串）粘到页面右上角的「连接设置」里即可。令牌默认只存在
`sessionStorage`（关标签页即失效）；勾选「记住」才会写入 `localStorage`。请不要把令牌
提交进仓库、发到群里或截图外传。

## 三、目录结构

```
frontend/
├── index.html                  外壳：侧边栏 + 顶栏 + 视图容器
├── serve.py                    静态服务 + /api 同源代理（仅标准库）
├── start.bat                   Windows 一键启动
├── assets/
│   ├── styles.css              设计系统（浅色，无外部字体）
│   ├── api.js                  API 客户端：全部 /v1 接口 + 错误码解释
│   ├── util.js                 HTML 转义、引用高亮、时间格式化、Toast、对话框
│   ├── message-actions.js      导入 / 修订 / 删除 / 详情抽屉（跨页复用）
│   ├── app.js                  路由、连接配置、全局状态
│   └── views/
│       ├── overview.js         概览：覆盖范围、事件、快捷入口
│       ├── library.js          消息库：分页、筛选、行内操作
│       ├── search.js           智能查找：会话、提问、条件、五种结果状态
│       ├── detail.js           原文详情：字段核对 + 悬停定位 + 事件版本与变更
│       └── system.js           系统状态：能力、健康检查、请求日志
└── tools/smoke_test.py         无需浏览器的联调自检（43 项）
```

## 四、两个值得演示的交互

**字段 → 原文真实定位**
后端保证 `fields` 的值逐字出现在 `content` 里（`value ⊂ content`），所以详情页把
「悬停字段 → 原文里标出它的位置」做成了真实定位：鼠标移到右侧任一字段上，左侧原文
会用 `<mark>` 精确框出它，而不是做一个假的动画。`util.js` 的 `highlight()` 用
「先在原文找位置、再分别转义」的方式，避免 HTML 转义改变长度后标错位置。

**五种查询状态，五种表情**
`answer / clarify / not_found / uncertain / error` 不是同一张卡换颜色：
`not_found` 是安静的灰（没找到 ≠ 出故障），`error` 是红色故障态并带
`request_id` 查日志入口，`clarify` 的候选一点即带着 `condition_updates.event_key`
追问并显示 `condition_version` 递增。

## 五、页面与接口对照

| 页面 | 用到的接口 | 说明 |
|---|---|---|
| 连接设置 | `GET /health`、`GET /v1/capabilities` | 保存前先验证令牌，失败会显示具体错误码 |
| 概览 | `GET /v1/coverage`、`GET /v1/events`、`POST /v1/imports`、`POST /v1/index/rebuild` | 覆盖范围的口径原样展示后端 `scope` 文案 |
| 消息库 | `GET /v1/messages`、`GET/PATCH/DELETE /v1/messages/{id}`、`POST /v1/imports` | 分页只有 `limit/offset`，页内筛选是前端做的 |
| 智能查找 | `POST /v1/query`、`GET /v1/sessions/{id}` | 会话续问、`condition_updates` / `clear_conditions`、候选追问 |
| 原文详情 | `GET /v1/messages/{id}`、`GET /v1/events` | 字段值是否逐字出现在原文，逐条标 ✓/⚠ |
| 系统状态 | `GET /health`、`GET /v1/capabilities`、`GET /v1/requests/{id}` | 用 `request_id` 查脱敏日志 |

## 五、几个刻意做对的细节

1. **mock 不等于真实 AI**
   每张结果卡右上角都有徽标：`模拟适配器` / `真实模型`，直接读 `ai.simulation`。
   系统页把 `true_semantic_model=false`、`workbuddy_certified=false` 原样展示，不美化。

2. **`error ≠ 没有数据`**
   `502/503/504` 与 `not_found` 用完全不同的视觉与文案。查询失败的响应里后端仍会保存
   会话与 `trace`，所以前端把它当成一轮结果渲染出来，而不是丢掉。

3. **修订 / 删除会让整个空间的会话失效**
   后端在 `dispatch` 里对 PATCH / DELETE 调 `store.invalidate(s)`，清掉该空间全部
   turns / sessions / batches。因此：
   - 保存修订前有明确警告；
   - 删除前二次确认，并说明备份里的旧副本不会被清除；
   - 旧会话恢复时若返回 404，标记为「失效」并提示新建，而不是重试。

4. **字段值必须逐字出现在原文**
   后端 `validate_message` 会拒绝 `fields` 里不在 `content` 中的值。前端在导入/修订
   表单里实时标红，避免提交后才看到 `INVALID_INPUT`。

5. **所有后端文本都转义**
   群名、消息原文、模型输出一律走 `esc()` 再插进 DOM；模型返回的内容不会当作 HTML 执行。

6. **幂等键**
   导入默认带 `ui-<时间戳>` 幂等键，避免手抖重复提交；`replayed=true` 会在结果里标明
   「幂等重放」，同键不同内容则显示 409。

## 六、自检

不需要浏览器就能验证静态资源、代理与后端契约（43 项，含导入 / 幂等 / 五种 query 状态 /
修订后失效 / 401 404 400 409 边界）：

```bash
cd frontend
python tools/smoke_test.py --backend-dir ../member-d/backend
```

退出码 0 即全部通过。浏览器里的手工验收建议按这个顺序：

1. 连接设置 → 测试并保存 → 顶栏出现「模拟模式」
2. 概览 → 导入示例数据（3 条）→ 数字变成 3 / 3
3. 智能查找 → 输入「之前那个志愿活动在哪集合」→ 出现两个候选 → 点「外出志愿服务」
4. 结果里出现「时间 / 地点 / 截止」三个字段 + 原文引用 → 点「查看原文」
5. 消息库里打开一条 → 修订群标签 → 提示会话已失效
6. 系统状态 → 用任意结果的 `request_id` 查日志

## 七、当前边界（不要对外说过头）

- 后端 `mock` 模式用确定性规则，**不理解任意口语更正**；自然语言更正要靠 `live` 模式
  或右侧条件面板显式提交。
- 检索是本地 n-gram / TF-IDF，**不是向量语义检索**。
- 后端单进程全局锁，模型慢请求会阻塞其他请求；前端只做了等待提示，没做队列。
- 群关注、自动同步、QQ 群消息接入都还没有后端接口，本前端**不提供**这些页面的假按钮。
- 未做生产部署：没有 HTTPS、登录、限流。`serve.py` 仅供本机联调。

## 八、交给仓库时

把整个 `frontend/` 放到仓库根目录即可（与 `member-d/` 同级）。若成员 D 以后加了 CORS
或把前端与后端同端口部署，把「API 地址」从 `/api` 改成实际前缀即可，代码不用动。
