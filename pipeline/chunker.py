"""
청크 분할 — 512토큰 초과 청구항을 분할해 임베딩 후 평균풀링으로 합침.
KorPatBERT(KIPRIS)는 mean pooling 직접 구현 필요, PatentSBERTa는 sentence-transformers가 처리.
"""
from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from transformers import PreTrainedTokenizer


class PatentChunker:
    def __init__(self, tokenizer: "PreTrainedTokenizer", max_tokens: int = 512):
        self.tokenizer = tokenizer
        self.max_tokens = max_tokens
        # [CLS], [SEP] 토큰 공간 확보
        self._effective_max = max_tokens - 2

    def split(self, text: str) -> list[str]:
        """
        텍스트를 512토큰 이하 청크로 분할.
        512 이하면 그대로 반환 (청크 1개).
        """
        token_ids = self.tokenizer.encode(text, add_special_tokens=False)

        if len(token_ids) <= self._effective_max:
            return [text]

        chunks: list[str] = []
        for start in range(0, len(token_ids), self._effective_max):
            chunk_ids = token_ids[start : start + self._effective_max]
            chunk_text = self.tokenizer.decode(chunk_ids, skip_special_tokens=True)
            chunks.append(chunk_text)

        return chunks

    def needs_chunking(self, text: str) -> bool:
        ids = self.tokenizer.encode(text, add_special_tokens=False)
        return len(ids) > self._effective_max
