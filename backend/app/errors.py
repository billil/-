"""错误码表与业务异常。

接口统一返回 {code, message, data}；错误码集中在此，禁止在业务代码里写魔法数字。
"""

# ---- 错误码 ----
EMPTY_QUERY = 1001
LLM_TIMEOUT = 2001
LLM_FORMAT_ERROR = 2002
DB_ERROR = 3001
GUARD_REJECTED = 4001
INTERNAL_ERROR = 5000

# ---- 中文提示 ----
MESSAGES = {
    EMPTY_QUERY: "查询内容为空，请输入你想查找的内容",
    LLM_TIMEOUT: "大模型服务响应超时，请稍后重试",
    LLM_FORMAT_ERROR: "大模型返回内容格式异常，已重试仍失败",
    DB_ERROR: "数据库操作失败，请稍后重试",
    GUARD_REJECTED: "内容未通过安全校验，请调整后重试",
    INTERNAL_ERROR: "服务内部错误，请稍后重试",
}

# ---- HTTP 状态码映射 ----
HTTP_STATUS = {
    EMPTY_QUERY: 400,
    LLM_TIMEOUT: 504,
    LLM_FORMAT_ERROR: 502,
    DB_ERROR: 500,
    GUARD_REJECTED: 400,
    INTERNAL_ERROR: 500,
}


class AppError(Exception):
    """已知业务异常：由全局异常处理器转换成 {code, message, data: null}。"""

    def __init__(self, code: int, message: str | None = None):
        self.code = code
        self.message = message or MESSAGES.get(code, "未知错误")
        self.http_status = HTTP_STATUS.get(code, 500)
        super().__init__(self.message)
