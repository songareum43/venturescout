"""
임베딩 모델 래퍼.

- USPTO/BigQuery 경로: PatentSBERTa → sentence-transformers .encode() 한 줄
- KIPRIS 경로: KorPatBERT → BERT 계열, mean pooling 수동 구현
두 경우 모두 청크 평균풀링으로 512토큰 초과 처리.
"""
from __future__ import annotations

import numpy as np
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    pass


class PatentEmbedder:
    """
    데이터 소스에 무관하게 동일한 인터페이스를 제공.
    config.is_korean 값에 따라 내부 구현만 분기됨.
    """

    def __init__(self):
        from config import config
        self.config = config
        self._model = None
        self._tokenizer = None
        self._chunker = None

    # ── lazy init (첫 호출 시 로드) ─────────────────────────────────────────
    def _load(self):
        if self._model is not None:
            return

        from config import config

        if config.is_korean:
            self._load_korpatbert()
        else:
            self._load_patent_sbert()

    def _load_patent_sbert(self):
        """PatentSBERTa: sentence-transformers 그대로 사용."""
        from sentence_transformers import SentenceTransformer
        from transformers import AutoTokenizer
        from pipeline.chunker import PatentChunker

        model_name = self.config.embedding_model
        self._model = SentenceTransformer(model_name)
        self._tokenizer = AutoTokenizer.from_pretrained(model_name)
        self._chunker = PatentChunker(self._tokenizer, self.config.max_tokens)
        self._backend = "sbert"

    def _load_korpatbert(self):
        """KorPatBERT: BERT 계열 → mean pooling 수동 구현."""
        import torch
        from transformers import AutoTokenizer, AutoModel
        from pipeline.chunker import PatentChunker

        model_name = self.config.embedding_model
        self._tokenizer = AutoTokenizer.from_pretrained(model_name)
        self._bert_model = AutoModel.from_pretrained(model_name)
        self._bert_model.eval()
        self._chunker = PatentChunker(self._tokenizer, self.config.max_tokens)
        self._backend = "korpatbert"
        self._torch = torch

    # ── 퍼블릭 API ───────────────────────────────────────────────────────────

    def embed(self, text: str) -> np.ndarray:
        """
        단일 텍스트 → 768d 벡터 (L2 정규화 완료).
        512토큰 초과 시 청크 분할 → 평균풀링.
        """
        self._load()
        chunks = self._chunker.split(text)

        if len(chunks) == 1:
            return self._embed_single(chunks[0])

        # 청크별 임베딩 → 평균풀링 → 재정규화
        vecs = np.stack([self._embed_single(c) for c in chunks])
        avg = vecs.mean(axis=0)
        return (avg / np.linalg.norm(avg)).astype(np.float32)

    def embed_batch(self, texts: list[str], batch_size: int = 32) -> np.ndarray:
        """
        배치 임베딩 (청크 처리 없이 직접). 짧은 텍스트 대량 처리용.
        B 파이프라인에서 시드 데이터 인덱싱 시 사용.
        """
        self._load()
        if self._backend == "sbert":
            return self._model.encode(
                texts,
                batch_size=batch_size,
                normalize_embeddings=True,
                show_progress_bar=True,
            ).astype(np.float32)
        else:
            return np.stack([self.embed(t) for t in texts])

    # ── 내부 구현 ────────────────────────────────────────────────────────────

    def _embed_single(self, text: str) -> np.ndarray:
        if self._backend == "sbert":
            return self._model.encode(text, normalize_embeddings=True).astype(np.float32)
        else:
            return self._korpatbert_mean_pool(text)

    def _korpatbert_mean_pool(self, text: str) -> np.ndarray:
        """KorPatBERT mean pooling (attention mask 가중 평균)."""
        torch = self._torch
        inputs = self._tokenizer(
            text,
            return_tensors="pt",
            truncation=True,
            max_length=self.config.max_tokens,
            padding=True,
        )
        with torch.no_grad():
            outputs = self._bert_model(**inputs)

        # last_hidden_state: (1, seq_len, 768)
        token_embeddings = outputs.last_hidden_state
        attention_mask = inputs["attention_mask"]

        # attention mask 확장 후 가중 평균
        mask_expanded = attention_mask.unsqueeze(-1).expand(token_embeddings.size()).float()
        sum_embeddings = (token_embeddings * mask_expanded).sum(dim=1)
        sum_mask = mask_expanded.sum(dim=1).clamp(min=1e-9)
        mean_pooled = (sum_embeddings / sum_mask).squeeze(0).numpy()

        # L2 정규화
        return (mean_pooled / np.linalg.norm(mean_pooled)).astype(np.float32)
