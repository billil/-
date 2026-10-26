"""向量化客户端。

单例懒加载 sentence-transformers 模型，embed_texts 返回 L2 归一化的 float32 矩阵。

注意：当前环境 Python 3.15 下 `AutoModel.from_pretrained` 会触发 torch C++ 层
access violation，因此改用 `AutoModel.from_config` 手动实例化 + safetensors
加载权重的方式绕过。模型首次加载失败时抛出带镜像解决建议的 EmbeddingError。
"""

import glob
import os
from typing import Optional

import numpy as np
import torch

from .config import EMBED_MODEL, EMBED_BATCH_SIZE


class EmbeddingError(RuntimeError):
    """模型加载或向量化失败。"""


def _find_weight_file(snapshot_dir: str) -> Optional[str]:
    """在快照目录中查找模型权重文件（优先 safetensors，其次 bin）。"""
    for pattern in ("model.safetensors", "pytorch_model.bin", "*.safetensors", "*.bin"):
        matches = glob.glob(os.path.join(snapshot_dir, pattern))
        if matches:
            return matches[0]
    return None


def _resolve_snapshot_dir(model_name: str) -> str:
    """解析模型本地快照目录。

    优先尝试 snapshot_download（联网时自动下载）；离线时直接从 HF 缓存结构定位。
    """
    from huggingface_hub import snapshot_download
    from huggingface_hub.constants import HF_HUB_CACHE

    # 尝试走官方下载流程（联网时可用）
    try:
        return snapshot_download(model_name, local_files_only=not _is_online())
    except Exception:
        pass

    # 离线兜底：直接从缓存目录结构定位最新 snapshot
    repo_folder = "models--" + model_name.replace("/", "--")
    repo_path = os.path.join(HF_HUB_CACHE, repo_folder)
    snapshots_dir = os.path.join(repo_path, "snapshots")
    if not os.path.isdir(snapshots_dir):
        raise EmbeddingError(
            f"模型缓存目录不存在: {snapshots_dir}\n"
            "请先联网下载模型，或设置 HF_ENDPOINT=https://hf-mirror.com 后重试。"
        )
    # 取第一个（通常也是唯一一个）snapshot 子目录
    snaps = [d for d in glob.glob(os.path.join(snapshots_dir, "*")) if os.path.isdir(d)]
    if not snaps:
        raise EmbeddingError(f"模型缓存中无 snapshot: {snapshots_dir}")
    return snaps[0]


class EmbeddingClient:
    """单例向量化客户端。"""

    _instance: "EmbeddingClient | None" = None
    _model = None
    _tokenizer = None
    _model_name: Optional[str] = None

    def __new__(cls) -> "EmbeddingClient":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def _load_model(self):
        """懒加载模型与分词器，失败时给出镜像建议。"""
        if self._model is not None:
            return self._model, self._tokenizer

        from transformers import AutoConfig, AutoModel, AutoTokenizer
        from safetensors.torch import load_file

        try:
            # 0. 解析模型本地快照目录（联网则下载，否则用缓存）
            snap_dir = _resolve_snapshot_dir(EMBED_MODEL)

            # 1. 分词器（从本地路径加载，避免远端解析）
            self._tokenizer = AutoTokenizer.from_pretrained(snap_dir)

            # 2. 配置（从本地路径加载）
            config = AutoConfig.from_pretrained(snap_dir)

            # 3. 手动实例化模型（避免 from_pretrained 的 meta-device 路径在 3.15 崩溃）
            model = AutoModel.from_config(config)

            # 4. 找到本地缓存的权重文件并加载
            weight_path = _find_weight_file(snap_dir)
            if weight_path is None:
                raise EmbeddingError(f"在缓存中找不到模型权重文件: {snap_dir}")

            if weight_path.endswith(".safetensors"):
                state_dict = load_file(weight_path)
            else:
                state_dict = torch.load(weight_path, map_location="cpu")

            model.load_state_dict(state_dict, strict=False)
            model.eval()
            self._model = model
            self._model_name = EMBED_MODEL
            return self._model, self._tokenizer

        except EmbeddingError:
            raise
        except Exception as exc:
            raise EmbeddingError(
                f"sentence-transformers 模型加载失败: {exc}\n"
                "排查建议：\n"
                "  1. 检查网络连接；\n"
                "  2. 国内可设置 HuggingFace 镜像：\n"
                "     Windows: set HF_ENDPOINT=https://hf-mirror.com\n"
                "     Linux/macOS: export HF_ENDPOINT=https://hf-mirror.com\n"
                "  3. 确认模型名正确（当前 EMBED_MODEL="
                f"{EMBED_MODEL}）。"
            ) from exc

    def embed_texts(self, texts: list[str]) -> np.ndarray:
        """批量向量化，返回 L2 归一化后的 float32 矩阵 (n, dim)。"""
        model, tokenizer = self._load_model()
        if not texts:
            dim = model.config.hidden_size
            return np.zeros((0, dim), dtype=np.float32)

        embeddings = []
        for i in range(0, len(texts), EMBED_BATCH_SIZE):
            batch = texts[i : i + EMBED_BATCH_SIZE]
            inputs = tokenizer(
                batch,
                padding=True,
                truncation=True,
                return_tensors="pt",
            )
            with torch.no_grad():
                outputs = model(**inputs)
            # 均值池化（sentence-transformers 默认策略）
            mask = inputs["attention_mask"].unsqueeze(-1).float()
            pooled = (outputs.last_hidden_state * mask).sum(1) / mask.sum(1)
            pooled = torch.nn.functional.normalize(pooled, p=2, dim=1)
            embeddings.append(pooled.cpu().numpy())

        result = np.concatenate(embeddings, axis=0).astype(np.float32)
        return result


def _is_online() -> bool:
    """快速判断是否能访问 HuggingFace（用于决定是否允许联网下载）。"""
    try:
        import urllib.request

        urllib.request.urlopen("https://huggingface.co", timeout=3)
        return True
    except Exception:
        return False
