# 群忆成员 D 独立后端

已实现可独立运行的后端原型：消息导入、SQLite 持久化、检索适配、会话更正、事件证据、原文读取、删除清理、Bearer 令牌隔离、日志及备份恢复。无需其他队员的前端或 AI 代码即可测试。

**先读 [开始使用.md](开始使用.md)。** 运行需要 Python 3.10+；本次实际验证为 Python 3.12.14 / Linux。Windows/macOS 命令已提供，但未在对应操作系统实测。

```bash
python -m qunyi
# 另开一个终端，仍在本项目目录：
python scripts/demo.py
# 自动化测试与独立服务 HTTP 验证：
python -m unittest discover -s tests -v
python scripts/verify_http.py
```

无需 pip 安装第三方包。默认监听 `127.0.0.1:8000`。启动后访问 `/health` 返回 JSON；这是后端，不提供完整网页。首次启动生成本机随机令牌，保存在 `state/credentials.json`。演示脚本自动从该文件读取，不会在终端打印令牌。

## 真实完成与模拟部分

| 项目 | 状态 |
|---|---|
| HTTP API、SQLite、会话、字段替换和清除、租户隔离、原文引用、删除、日志、备份恢复 | 已实现并测试 |
| 本地字符 n-gram / TF-IDF / 词汇扩展检索 | 已实现；不是 embedding 或经过训练的语义模型 |
| 多候选追问、事件更正演示 | 模拟适配器已实现；事件和字段由模拟数据人工标注 |
| 外部模型接入层和有界工具调度 | 代码已实现；使用本地合成服务验证了 HTTP 协议 |
| 真实外部模型端到端效果 | 未验证，未提供真实密钥或确定的模型端点 |
| 中国移动 WorkBuddy 联名版内运行与赛事平台合规 | 未验证，不能宣称满足 |
| 自由文本自动建立可靠事件关联、任意口语意图的理解效果 | 未完成专项评估；mock 仅支持显式字段更新和有限确定性规则 |
| 前端、私人群自动采集、图片识别、语音、自动报名 | 本次不包含 |

`mock` 返回 `simulation=true`、`actual_participation=false`。`live` 所有合法查询均尝试调用配置的模型，包括无候选情况；缺配置、超时、格式错误或越界引用明确报错，**不回退模拟答案**。`ai.attempted` 表示调用尝试，`actual_participation` 表示成功获得适配器可解析响应，`succeeded` 表示结果通过当前程序校验；这不是对服务商模型身份的独立认证。单元测试中的提供者替身与本地 HTTP 合成服务不是真实大模型。

## 项目内容

- `qunyi/`：可运行后端源码。
- `samples/messages.json`：显著标注的模拟数据。
- `scripts/demo.py`：请求运行中的服务，输出真实 HTTP 请求结果。
- `scripts/verify_http.py`：启动独立子进程，执行演示、备份和恢复，再关闭。
- `scripts/maintenance.py`：数据库备份与停止服务后的恢复。
- `tests/`：43 项测试，含权限、数据清理、错误、HTTP 和模型适配器边界。
- `docs/API.md`、`docs/openapi.json`：接口约定和机器可读定义。
- `docs/VERIFICATION.md`、`docs/test-output.txt`、`docs/http-demo-report.json`：本次验证范围与实际记录。
- `docs/WORKBUDDY.md`：联名版继续开发步骤和未核实边界。

本代码在当前环境独立开发，未操作 GitHub，未部署公网服务，未调用未经配置的外部模型。
