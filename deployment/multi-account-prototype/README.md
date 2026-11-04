# 群忆多账号隔离原型（未上线）

本目录是多账号注册与独立工作区的测试版本，不应直接覆盖线上服务。

- 新账号各自创建随机工作区，网关以服务器私有签名将工作区传给后端。
- 旧项目账号继续使用原工作区；注册账号不自动读取现有 QQ 群消息。
- 网关和后端都必须通过受保护的服务器环境变量 `QUNYI_INTERNAL_WORKSPACE_KEY` 配置相同的独立随机密钥，不得提交密钥。
- 新账号注册需要首个项目账号已完成设置，否则注册返回 503。
- 网关对注册、登录和查询做基础速率限制，后续仍需补充持续限流、密码恢复及运行监控。
- 当前没有逐群成员身份验证、授权管理或群消息分发，注册后数据空间为空；不能声称新用户可立即查看自己的 QQ 群消息。
- `service.py` 基于本地快照 `repair-stage/merged-service.py`，上线前必须与实际服务器文件对比，并将身份校验以最小变更合入当前版本。
- 登录页面使用 cookie；账号标签当前仍为浏览器本地存储，不跨设备同步。

运行 `python -m unittest discover -s multi-account -p 'test_multi.py'` 及 `python -m unittest discover -s multi-account -p 'test_backend_isolation.py'`，第二项依赖本地后端快照 `qunyi-fix-work/member-d/backend/qunyi`。
