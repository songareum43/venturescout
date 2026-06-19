"""
Track B 핵심 테스트.
D3 게이트 전에 반드시 통과해야 하는 항목들.
실데이터 없이도 mock으로 구조 검증 가능.

State/AgentFinding 계약 검증은 tests/test_contracts.py에서 다룬다.
"""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch


# ── chunker ──────────────────────────────────────────────────────────────────

class TestPatentChunker:
    def _make_chunker(self, max_tokens=512):
        from unittest.mock import MagicMock
        tok = MagicMock()
        # 짧은 텍스트: 10토큰 반환
        tok.encode.return_value = list(range(10))
        tok.decode.return_value = "decoded chunk"
        from pipeline.chunker import PatentChunker
        return PatentChunker(tok, max_tokens)

    def test_short_text_no_split(self):
        chunker = self._make_chunker(max_tokens=512)
        chunks = chunker.split("짧은 텍스트")
        assert len(chunks) == 1

    def test_long_text_splits(self):
        from unittest.mock import MagicMock
        tok = MagicMock()
        # 1100토큰짜리 긴 텍스트 → (512-2)*3 = 1530 > 1100이므로 3청크
        tok.encode.return_value = list(range(1100))
        tok.decode.return_value = "chunk"
        from pipeline.chunker import PatentChunker
        chunker = PatentChunker(tok, max_tokens=512)
        chunks = chunker.split("x" * 5000)
        assert len(chunks) == 3  # ceil(1100/510) = 3


# ── reranker ─────────────────────────────────────────────────────────────────

class TestReRanker:
    def _make_candidates(self):
        return [
            {
                "document_id": "doc_001",
                "hybrid_score": 0.8,
                "stance": "supports",
                "reliability_score": 0.9,
                "source_type": "patent",
            },
            {
                "document_id": "doc_002",
                "hybrid_score": 0.7,
                "stance": "contradicts",
                "reliability_score": 0.6,
                "source_type": "seed_review",
            },
            {
                "document_id": "doc_003",
                "hybrid_score": 0.6,
                "stance": "neutral",
                "reliability_score": 0.5,
                "source_type": "web",
            },
        ]

    def test_contradicting_boosted(self):
        from search.reranker import ReRanker
        rr = ReRanker()
        results = rr.rerank(self._make_candidates(), prefer_contradicting=True)
        # contradicts(doc_002)가 supports(doc_001)보다 위에 있어야 함
        ids = [r["document_id"] for r in results]
        assert ids.index("doc_002") < ids.index("doc_001")

    def test_rerank_score_non_negative(self):
        from search.reranker import ReRanker
        rr = ReRanker()
        results = rr.rerank(self._make_candidates())
        for r in results:
            assert r["rerank_score"] >= 0.0

    def test_top_k_respected(self):
        from search.reranker import ReRanker
        rr = ReRanker()
        results = rr.rerank(self._make_candidates(), top_k=2)
        assert len(results) == 2


# ── D3 게이트 ─────────────────────────────────────────────────────────────────

class TestD3Gate:
    @patch("pipeline.indexer.psycopg2.connect")
    @patch("pipeline.indexer.register_vector")
    def test_sync_ok(self, mock_reg, mock_connect):
        mock_conn = MagicMock()
        mock_conn.closed = 0
        mock_connect.return_value = mock_conn
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value.__enter__ = lambda s: mock_cursor
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

        # 전체 100건, 임베딩 100건 → OK
        mock_cursor.fetchone.side_effect = [(100,), (100,)]

        from pipeline.indexer import PatentIndexer
        indexer = PatentIndexer()
        indexer._conn = mock_conn
        result = indexer.verify_sync()

        assert result["gate_pass"] is True
        assert result["missing"] == 0

    @patch("pipeline.indexer.psycopg2.connect")
    @patch("pipeline.indexer.register_vector")
    def test_sync_fail(self, mock_reg, mock_connect):
        mock_conn = MagicMock()
        mock_conn.closed = 0
        mock_connect.return_value = mock_conn
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value.__enter__ = lambda s: mock_cursor
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

        # 전체 100건, 임베딩 85건 → FAIL
        mock_cursor.fetchone.side_effect = [(100,), (85,)]

        from pipeline.indexer import PatentIndexer
        indexer = PatentIndexer()
        indexer._conn = mock_conn
        result = indexer.verify_sync()

        assert result["gate_pass"] is False
        assert result["missing"] == 15


# ── persistence (evidence_items / agent_runs / ip_overlap_candidates) ────────

class TestPersistence:
    def _mock_conn(self):
        conn = MagicMock()
        cursor = MagicMock()
        conn.cursor.return_value.__enter__ = lambda s: cursor
        conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        return conn, cursor

    def test_create_evidence_item(self):
        from pipeline.persistence import create_evidence_item
        conn, cursor = self._mock_conn()
        cursor.fetchone.return_value = ("ev-1",)

        evidence_id = create_evidence_item(
            conn,
            job_id="job-1", hypothesis_id="hyp-1", document_id="doc-1",
            source_type="web", evidence_text="some text",
            stance="supports", relevance_score=0.8, reliability_score=0.4,
        )

        assert evidence_id == "ev-1"
        conn.commit.assert_called_once()

    def test_create_agent_run(self):
        from pipeline.persistence import create_agent_run
        conn, cursor = self._mock_conn()
        cursor.fetchone.return_value = ("run-1",)

        run_id = create_agent_run(
            conn,
            job_id="job-1", hypothesis_id="hyp-1",
            agent_name="market", model_name="claude-sonnet-4-6",
            depth="full", confidence="mid",
            grounded_on=["ev-1", "ev-2"],
            output_json={"pain_signal": {"summary": "x"}},
        )

        assert run_id == "run-1"
        conn.commit.assert_called_once()

    def test_create_ip_overlap_candidates(self):
        from pipeline.persistence import create_ip_overlap_candidates
        conn, cursor = self._mock_conn()
        # candidate별로 evidence_id, candidate_id 순으로 fetchone 호출됨
        cursor.fetchone.side_effect = [("ev-1",), ("cand-1",), ("ev-2",), ("cand-2",)]

        candidates = [
            {"limitation_id": "lim-1", "document_id": "doc-1", "normalized_text": "a widget",
             "lexical_score": 0.1, "similarity_score": 0.9, "hybrid_score": 0.8},
            {"limitation_id": "lim-2", "document_id": "doc-2", "normalized_text": "b widget",
             "lexical_score": 0.2, "similarity_score": 0.7, "hybrid_score": 0.6},
        ]

        ids = create_ip_overlap_candidates(
            conn, job_id="job-1", hypothesis_id="hyp-1",
            plan_technical_element="widget rotation mechanism",
            candidates=candidates,
        )

        assert ids == ["cand-1", "cand-2"]

    @patch("pipeline.persistence.get_connection")
    def test_persist_agent_output_skips_without_job_id(self, mock_get_conn):
        from pipeline.persistence import persist_agent_output
        result = persist_agent_output(
            None, agent_name="market", depth="full",
            confidence="low", output_json={},
        )
        assert result is None
        mock_get_conn.assert_not_called()

    @patch("pipeline.persistence.create_agent_run")
    @patch("pipeline.persistence.create_evidence_items_for_documents")
    @patch("pipeline.persistence.get_connection")
    def test_persist_agent_output_with_job_id(self, mock_get_conn, mock_create_items, mock_create_run):
        from pipeline.persistence import persist_agent_output
        mock_conn = MagicMock()
        mock_get_conn.return_value = mock_conn
        mock_create_items.return_value = {"doc-1": "ev-1", "doc-2": "ev-2"}
        mock_create_run.return_value = "run-1"

        run_config = {"configurable": {"job_id": "job-1", "hypothesis_id": "hyp-1"}}
        result = persist_agent_output(
            run_config, agent_name="market", depth="full", confidence="mid",
            output_json={"pain_signal": {"summary": "x"}},
            evidence_documents=[{"document_id": "doc-1"}, {"document_id": "doc-2"}],
        )

        assert result == "run-1"
        _, kwargs = mock_create_run.call_args
        assert sorted(kwargs["grounded_on"]) == ["ev-1", "ev-2"]
        mock_conn.close.assert_called_once()
