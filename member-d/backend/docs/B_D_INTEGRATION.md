# B/D 后端整合约定
网站统一调用成员 D 的 /v1/* 接口。D 负责 SQLite 持久化、会话、权限过滤、引用校验、订阅和同步状态；B 的检索与提示词能力作为模型层接入。
兼容入口：POST /api/search、POST /api/answer_ask、GET /api/health。新前端使用 /v1/query、/v1/sessions/{id}、/v1/messages/{id}。错误格式统一为 {status:error, error:{code,message}, request_id}。
B 数据使用 scripts/migrate_b_sqlite.py 导入；source_confirmed 默认 false，导入后执行 /v1/index/rebuild。
正式上线仍需 OAuth、群成员资格、公网 HTTPS、QQ 开发者测试群、真实模型许可、Windows 日志和 C 前端部署联调现场验收。
