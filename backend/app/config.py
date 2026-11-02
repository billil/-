"""群忆后端集中配置。

所有可调参数集中在此，环境变量可覆盖默认值。
"""

import os

# ---- 向量模型 ----
# 默认多语言 MiniLM，中英文通用、体积小、速度快
EMBED_MODEL = os.environ.get("EMBED_MODEL", "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")
EMBED_BATCH_SIZE = int(os.environ.get("EMBED_BATCH_SIZE", "64"))

# ---- 检索参数 ----
TOP_K_DEFAULT = int(os.environ.get("TOP_K_DEFAULT", "50"))
VEC_TOP_K_DEFAULT = int(os.environ.get("VEC_TOP_K_DEFAULT", "50"))
# RRF 融合常数 k（论文默认 60）
RRF_K = int(os.environ.get("RRF_K", "60"))

# ---- 线索抽取 ----
# 每批送给大模型的消息条数
EXTRACT_BATCH_SIZE = int(os.environ.get("EXTRACT_BATCH_SIZE", "20"))

# ---- 大模型调用 ----
# 上游由 LLM_PROVIDER 选择（openai / workbuddy），具体配置项在 app/providers.py 每次调用时读取：
#   openai    -> LLM_BASE_URL / LLM_API_KEY / LLM_MODEL
#   workbuddy -> WB_ENDPOINT / WB_PUBLISHABLE_KEY / WB_MODEL（WB_ORIGIN 可选）
LLM_TIMEOUT = float(os.environ.get("LLM_TIMEOUT", "30"))
LLM_TEMPERATURE = 0

# ---- 数据库 ----
DB_PATH = os.environ.get("MEMORY_DB_PATH", os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "memory.db"
))
