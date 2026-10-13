import threading
from pathlib import Path

import numpy as np

from legal_rag.config import settings


class Encoder:
    def __init__(self):
        import py_vncorenlp
        import torch
        from huggingface_hub import hf_hub_download
        from sentence_transformers import SentenceTransformer

        config = settings()
        if config.embedding_device == "cuda" and not torch.cuda.is_available():
            raise RuntimeError("CUDA requested but unavailable")
        model_config = hf_hub_download(
            config.embedding_model,
            "config.json",
            revision=config.embedding_revision,
            token=config.hf_token.get_secret_value() or False,
        )
        self.revision = Path(model_config).parent.name
        self.model = SentenceTransformer(
            config.embedding_model,
            revision=self.revision,
            device=config.embedding_device,
            token=config.hf_token.get_secret_value() or False,
        )
        self.model.max_seq_length = 512
        if config.embedding_device == "cuda":
            self.model.half()
        self.nlp = py_vncorenlp.VnCoreNLP(annotators=["wseg"], save_dir=str(config.vncorenlp_dir.resolve()))
        self.lock = threading.Lock()

    def count(self, text):
        return len(self.model.tokenizer(text, add_special_tokens=True, truncation=False)["input_ids"])

    def encode(self, texts):
        import torch

        batch = settings().embedding_batch_size
        while True:
            try:
                vectors = self.model.encode(
                    texts,
                    batch_size=batch,
                    normalize_embeddings=True,
                    convert_to_numpy=True,
                    show_progress_bar=False,
                )
                vectors = np.asarray(vectors, dtype=np.float32)
                vectors /= np.linalg.norm(vectors, axis=1, keepdims=True)
                if vectors.shape != (len(texts), 1024) or not np.isfinite(vectors).all():
                    raise ValueError("invalid embedding vectors")
                return vectors.tolist()
            except torch.cuda.OutOfMemoryError:
                if batch <= 1:
                    raise
                torch.cuda.empty_cache()
                batch = max(1, batch // 2)
