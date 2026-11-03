# B AI 门面与 D 后端实验性集成

本集成将 B 的 `app.facade.process` 接到 D 的 HTTP 服务 `/v1/ai/query`，默认关闭。D 的 `/v1/query` 继续保留原行为；网站尚未改接新入口。它是可本地验证的整合原型，不是正式生产认证或真实模型验收。

## 接入方式

以仓库根目录为工作目录，安装 B 所需依赖并设置 Python 模块路径。这个路径包含 B 的 `backend` 和 D 的 `member-d/backend`。仅在已隔离、授权的测试环境中启用。

Linux/macOS：

```sh
export PYTHONPATH="$PWD/backend:$PWD/member-d/backend"
export QUNYI_B_INTEGRATION=1
export LLM_BASE_URL=https://example.invalid/v1
export LLM_MODEL=replace-with-model-name
export LLM_API_KEY=configure-in-secret-store
python -m qunyi
```

Windows PowerShell：

```powershell
$env:PYTHONPATH = "$PWD/backend;$PWD/member-d/backend"
$env:QUNYI_B_INTEGRATION = "1"
# 私下设置 LLM_BASE_URL、LLM_MODEL、LLM_API_KEY 后启动
python -m qunyi
```

示例地址和模型名不可用；模型密钥只在后端私密环境配置。B 的模型调用参数目前仍使用 `LLM_*`，D 原有适配器使用不同变量，两者尚未统一。没有配置、缺少模块或调用错误会报错，不回退模拟结果。模型地址与授权、成本及赛事规则需要事先核实。

请求采用 D 的 Bearer 工作区令牌：`POST /v1/ai/query`，JSON `{"question":"志愿活动在哪里集合"}`。响应为 B 的状态、答案、证据以及 D 生成的 `session_id`；追问可以再发送 `{"question":"...","session_id":"..."}`。默认关闭返回 503 `B_INTEGRATION_DISABLED`；不属于当前工作区的会话返回 404。

B 门面使用 D 的本地检索与 SQLite 中的工作区候选，候选限制为已登记且 `authorized=1` 的群；D 会再次校验返回证据的 ID 和原文片段。B 会话单独保存在 D 的 SQLite 中，修改和删除 D 消息时清除当前工作区的 B 会话。为了不悄悄忽略条件，目前类别、文件以及活动时间范围等无法可靠映射的硬筛选会返回 422 `FILTER_UNSUPPORTED`。B 事件聚类需要其原有业务库，本集成传入 `db_path=None`，因此暂不使用 B 的 leads 聚类，也不迁移数据。

## 重要边界

- 工作区令牌与群的 `authorized` 标志不构成正式用户身份或成员资格校验；现有 D 普通查询、原文读取仍缺少逐用户逐群权限。本实验入口不得用于公开服务或未获授权的真实数据。
- B 会直接通过它自己的 `LLM_*` 配置调用外部模型；D 原先的供应商配置、调用日志和安全约束尚未合并。不能默认外部数据传输已经获得许可。
- 本地检索当前按原始提问检索，并非 B 的混合向量检索；更正时 query 与历史条件的完整融合、筛选精度及原子性仍需完善。
- 模型耗时调用仍处于 D 服务全局锁内，会阻塞采集；正式上线前必须分离。无法从本次测试推断真实模型质量或性能。
- 测试通过 D 实际 HTTP 与 B 真正 facade，模型解析与生成采用替身，不联网；真实 QQ、真实模型及前端联调均未验收。

在仓库根目录运行：`PYTHONPATH=backend:member-d/backend python -m unittest discover -s member-d/backend/tests -q`（Windows 使用分号分隔路径）。本机测试需准备 B 内部 Python 模块，正常安装依赖后可使用真实 B 模型调用。不要把凭据提交仓库。
