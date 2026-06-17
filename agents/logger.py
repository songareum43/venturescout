"""Track C 단계별 디버깅 로거."""

import json
import logging
import sys
from datetime import datetime

# 로그 포맷 설정
LOG_FORMAT = "%(asctime)s | %(name)s | %(levelname)s | %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"

# 핸들러 설정
handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT))

# 루트 로거 설정
root_logger = logging.getLogger("venturescout")
root_logger.setLevel(logging.DEBUG)
root_logger.addHandler(handler)


def get_logger(name: str) -> logging.Logger:
    """각 모듈별 로거 반환."""
    return logging.getLogger(f"venturescout.{name}")


def log_stage(logger: logging.Logger, stage: str, node: str):
    """노드 시작/진입 로그."""
    logger.info(f"{'='*60}")
    logger.info(f"[{stage}] 노드: {node}")
    logger.info(f"{'='*60}")


def log_input(logger: logging.Logger, data: dict, label: str = "INPUT"):
    """입력 데이터 로깅."""
    logger.info(f"[{label}] 데이터 접수:")
    for key, value in data.items():
        if isinstance(value, (dict, list)):
            logger.info(f"  {key}: {type(value).__name__} (크기: {len(value)})")
        elif isinstance(value, str):
            logger.info(f"  {key}: '{value[:50]}...' (길이: {len(value)})" if len(value) > 50 else f"  {key}: '{value}'")
        else:
            logger.info(f"  {key}: {value}")


def log_processing(logger: logging.Logger, step: str, details: dict = None):
    """처리 단계 로깅."""
    logger.info(f"→ {step}")
    if details:
        for key, value in details.items():
            logger.info(f"  ├ {key}: {value}")


def log_output(logger: logging.Logger, data: dict, label: str = "OUTPUT"):
    """출력 데이터 로깅."""
    logger.info(f"[{label}] 결과 생성:")
    for key, value in data.items():
        if isinstance(value, (dict, list)):
            logger.info(f"  {key}: {type(value).__name__} (크기: {len(value)})")
        elif hasattr(value, '__class__'):
            logger.info(f"  {key}: {value.__class__.__name__} 객체")
        else:
            logger.info(f"  {key}: {value}")


def log_error(logger: logging.Logger, error: Exception, context: str = ""):
    """에러 로깅."""
    logger.error(f"❌ 에러 발생{f' ({context})' if context else ''}")
    logger.error(f"  유형: {type(error).__name__}")
    logger.error(f"  메시지: {str(error)}")


def log_validation(logger: logging.Logger, checks: dict):
    """검증 결과 로깅."""
    logger.info("📋 검증 결과:")
    for check_name, result in checks.items():
        status = "✓" if result.get("passed", False) else "✗"
        logger.info(f"  {status} {check_name}: {result.get('message', '')}")


def log_grounding(logger: logging.Logger, agent_name: str, evidence_ids: list, confidence: str):
    """근거 연결 로깅."""
    logger.info(f"📌 근거 연결 [{agent_name}]:")
    logger.info(f"  신뢰도: {confidence}")
    logger.info(f"  근거 ID 개수: {len(evidence_ids)}")
    for eid in evidence_ids[:3]:  # 처음 3개만 표시
        logger.info(f"    - {eid}")
    if len(evidence_ids) > 3:
        logger.info(f"    ... 외 {len(evidence_ids) - 3}개")


def log_decision(logger: logging.Logger, decision: str, confidence: str, reasons: list):
    """최종 판단 로깅."""
    logger.info(f"🎯 최종 판단:")
    logger.info(f"  결정: {decision}")
    logger.info(f"  신뢰도: {confidence}")
    logger.info(f"  사유:")
    for reason in reasons[:5]:  # 처음 5개만 표시
        logger.info(f"    • {reason}")
    if len(reasons) > 5:
        logger.info(f"    ... 외 {len(reasons) - 5}개")


def log_completion(logger: logging.Logger, node: str, duration_ms: float = None):
    """노드 완료 로깅."""
    msg = f"✅ {node} 노드 완료"
    if duration_ms:
        msg += f" ({duration_ms:.0f}ms)"
    logger.info(msg)
    logger.info(f"{'='*60}\n")
