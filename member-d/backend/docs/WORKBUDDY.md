# 在 WorkBuddy 联名版继续开发

## 已查证和未查证

2026-10-25 阅读腾讯 CodeBuddy 官方 WorkBuddy 产品说明：通用产品可在授权本地文件夹和本地终端执行任务，并支持模型切换、MCP 与 Skills 等机制。

官方来源：
- https://www.codebuddy.cn/docs/workbuddy/From-Beginner-to-Expert-Guide/Product-Guide
- https://www.codebuddy.cn/docs/workbuddy/From-Beginner-to-Expert-Guide/Function-Description/Permission-Modes

这些通用说明**不能证明中国移动赛事联名版具有同样套餐权限，也不能证明可把联名版的对话额度作为本项目 HTTP 模型 API 调用**。未获取赛事附件三正文或该账号操作授权，未登录联名版，未在联名版内实际运行。本后端是在当前独立环境开发；不要宣称由联名版完成或已经满足竞赛要求。

## 可执行的迁移步骤

1. 从学校通知附件领取联名版账号/额度，并向组委会确认“仅限使用”的开发、运行及模型范围。
2. 在联名版中打开解压后的本项目文件夹，勿放在客户端安装目录。允许读取该项目所需文件；真实凭据保留在本机安全配置中。
3. 在其可用终端先运行 `python --version`、`python -m unittest discover -s tests -v`；记录实际版本、权限错误和测试结果。缺 Python 时按学校允许方式配置，不假定产品自带。
4. 在允许的环境运行 `python -m qunyi` 和 `python scripts/demo.py`。记录真实终端截图、文件变更和版本。
5. 若组委会允许独立后端及外部模型，核对真实 API 协议，再替换 providers.py 或设置已允许的 URL/模型/凭据；通用 chat-completions 适配器不是联名版专用接口。
6. 若联名版只能使用平台内工具或 Agent 流程，则把 API.md 的导入、检索、原文、版本、会话工具逐个接成平台允许的工具；复用字段契约，重新运行同等场景验证。此迁移需要实际平台能力，目前未完成。
7. 用模拟通知确认真实调用、无候选、无配置、超时和无效引用；再考虑授权脱敏试用。保留平台内创作过程，向组委会核对材料要求。

可直接输入联名版的任务指令：

> 请读取 README.md、docs/API.md、docs/WORKBUDDY.md 和源代码。先验证本平台允许的运行及模型接入方式，再运行现有测试与模拟 HTTP 演示。不要读取或打印真实密钥。未经确认不要调用外部收费服务。保留现有数据空间隔离、引用校验和 live 不回退规则；逐项列出实际通过、失败及待验证能力。若必须用平台内工具，请按 API.md 的字段契约迁移，并保留明确的模拟标识。
