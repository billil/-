# WorkBuddy 与群忆后端的接入准备

此版本让 D `/v1/query` 通过 B facade 调用 WorkBuddy 的流式模型接口。D 从工作空间内已授权的群中获取候选，B 解析查询并核验来源；当前返回需要用户核对的原文，不应将其当作完整答案质量已验证。

## 配置

后端设置 `QUNYI_MODE=live`、`QUNYI_B_INTEGRATION=1` 和 `LLM_PROVIDER=workbuddy`。服务器需要通过安全方式设置：

- `WB_ENDPOINT`：该 WorkBuddy 云服务应用实际提供的 HTTPS 根地址，不带路径或查询参数。
- `WB_PUBLISHABLE_KEY`：与应用匹配的 `publicConfig.publishableKey`。
- `WB_MODEL`：该云服务提供的模型 ID。
- `WB_ORIGIN`：如应用要求特定来源，填应用允许的来源；必须与该应用配置匹配。

这些值来自用户实际开通的 WorkBuddy 云服务；已有 WorkBuddy 分享页或聊天产品并不一定提供可用的模型 API。密钥和环境文件只应存于服务器受保护目录，不应上传 GitHub、放进网页或出现在命令历史中。

B 模块和 D 后端需要在同一个 Python 进程可导入，依赖至少包括 `jieba`。目前服务器还未部署本桥接；部署前须备份、合并当前线上修复、检查依赖和失败回滚路径。现有 B/D 测试分支不是服务器运行状态的证明。

WorkBuddy SSE 调用使用云服务对应的 SDK 协议，测试使用本地 SSE 双服务；真实云端可用性、来源校验、额度、计费及赛事资格尚未验证。该方案会将用户问题及检索到的群消息候选发送给 WorkBuddy；上线前需明确数据处理范围和相应授权。发生配置缺失或上游错误时应返回错误，不退回模拟答案。

## 测试

在仓库根目录安装所需依赖后：

```sh
PYTHONPATH=backend:member-d/backend python -m unittest discover -s member-d/backend/tests -q
```

`test_workbuddy_bridge.py` 测试本机 HTTP 与模拟 SSE、引用、失去授权、配置缺失及不安全地址拒绝，不能证明真实 WorkBuddy 已开通。真实云端验证先使用虚构数据，再进行经授权的端到端联调。
