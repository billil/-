"""pytest 全局配置。

测试一律离线：sentence-transformers 模型只从本地 HF 缓存加载，
避免 huggingface_hub 联网校验导致用例偶发超时（曾出现 1.4s → 134s）。

首次运行前请先执行 `python -m scripts.seed_demo` 或
`python scripts/build_embeddings.py`，把模型权重下载到本地缓存。
"""

import os

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
