# 群忆 CampusRecall

校园群通知订阅与 AI 信息检索助手。目标是一次选择关注的已授权群，后续自动更新通知，并通过模糊记忆查找、更正条件和追溯原文。

## 阅读入口

- [最新讨论与后续任务](docs/项目进展/群忆_项目讨论汇总与成员D后续任务清单.md)
- [后端开始使用](member-d/backend/开始使用.md)
- [接口文档](member-d/backend/docs/API.md)与 [OpenAPI](member-d/backend/docs/openapi.json)
- [本次归档与验证说明](docs/项目进展/本次归档与验证说明.md)

## 分类目录

| 目录 | 内容 |
| --- | --- |
| member-d/backend/ | 后端源码、SQLite、模型适配、模拟数据、测试、HTTP 演示与报告 |
| docs/作品方案/ | 参赛方向与作品形态建议，Word 与文字版 |
| docs/开发分工/ | 四人任务清单与成员 D 指南，Word 与文字版 |
| docs/需求调研/ | 八道选择题修订问卷，Word 与文字版 |
| docs/项目进展/ | 最新讨论、已完成工作、后续任务与本次归档说明 |
| releases/ | 原交付 v0.1 ZIP，历史版本原样保留 |

较早方案与最新讨论有差异时，以最新讨论和归档说明为准。自动同步是待验证方案，文档不代表功能已实现。

## 启动与验证

Python 3.10 及以上，无第三方运行依赖。

```bash
cd member-d/backend
python -m qunyi
```

访问 http://127.0.0.1:8000/health 。默认 mock 模式。本机生成的凭据存于 state/credentials.json，不纳入仓库。

另开终端：

```bash
cd member-d/backend
python scripts/demo.py
python scripts/run_tests.py
python scripts/verify_http.py
```

demo.py 要求服务已启动。详细参数见后端 README；不要删除使用中的数据库。

## 状态与边界

已完成本机后端原型、模拟消息演示、原文引用与更正流程、测试、备份恢复。Windows 本机通过由成员 D 反馈；实际日志仍在其电脑，本仓库不伪造该文件。

真实 AI 效果、QQ 官方普通新消息接入、群访问资格、持久订阅、自动刷新及公网运行仍待验证或开发。当前检索不是已验证的向量语义检索。

赛事指定中国移动 WorkBuddy 联名版。外部模型 API 是否允许需向组委会确认。网页登录不等于群消息权限。

样例是模拟数据。仓库不包含运行数据库、真实密钥或个人聊天数据。模型密钥只在后端配置。当前本机原型尚不能视为完整生产服务。

## 与已有成员 B 代码的关系

仓库原有 backend/ 是成员 B 的 FastAPI 与 AI 检索实现，保持原状。本次成员 D 独立原型位于 member-d/backend/，接口和数据结构尚未与 B 版合并，两套服务不能视为已完成联调。
