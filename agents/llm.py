"""Strict Amazon Bedrock Claude adapter for live VentureScout runs."""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass
from typing import Any, Literal

from dotenv import load_dotenv

load_dotenv()


# ── 토큰 사용량 집계 + 비용 산정 ────────────────────────────────────────────
# Bedrock converse 응답의 usage(input/output 토큰)를 캡처해 누적한다.
# ADR-029/035의 cost_usd=None TODO를 닫기 위함 — 추정이 아니라 실측 토큰 기반.
# 단가: Claude Sonnet 4.6 (Bedrock, 1P와 동일) 입력 $3 / 출력 $15 per 1M 토큰.
# (claude-api 레퍼런스 2026-06 기준. 리전·계약별로 다르면 .env로 덮어쓴다.)
PRICE_INPUT_PER_MTOK = float(os.getenv("BEDROCK_PRICE_INPUT_PER_MTOK", "3.0"))
PRICE_OUTPUT_PER_MTOK = float(os.getenv("BEDROCK_PRICE_OUTPUT_PER_MTOK", "15.0"))

_USAGE_LOCK = threading.Lock()  # 분석 5노드가 병렬 스레드라 누적은 lock으로 보호
_USAGE = {"input_tokens": 0, "output_tokens": 0, "calls": 0}


def _record_usage(input_tokens: int, output_tokens: int) -> None:
    with _USAGE_LOCK:
        _USAGE["input_tokens"] += int(input_tokens or 0)
        _USAGE["output_tokens"] += int(output_tokens or 0)
        _USAGE["calls"] += 1


def reset_usage() -> None:
    """잡(또는 평가 1회) 시작 전에 호출해 누적을 0으로 되돌린다."""
    with _USAGE_LOCK:
        _USAGE.update(input_tokens=0, output_tokens=0, calls=0)


def usage_snapshot() -> dict:
    """현재까지 누적된 토큰과 그로부터 계산한 USD 비용을 반환한다."""
    with _USAGE_LOCK:
        snap = dict(_USAGE)
    snap["cost_usd"] = round(
        snap["input_tokens"] / 1_000_000 * PRICE_INPUT_PER_MTOK
        + snap["output_tokens"] / 1_000_000 * PRICE_OUTPUT_PER_MTOK,
        6,
    )
    return snap


# ── LangSmith 트레이싱 (선택) ───────────────────────────────────────────────
# langsmith 미설치 또는 LANGCHAIN_TRACING_V2 미설정 시 완전 no-op.
# 설정 시 각 Bedrock 호출이 run_type="llm" 스팬으로 잡히고 토큰 usage가 붙어
# LangGraph 노드 트리와 함께 smith.langchain.com 대시보드에서 비용까지 추적된다.
try:
    from langsmith import traceable as _ls_traceable
    from langsmith.run_helpers import get_current_run_tree as _ls_run_tree
    _LANGSMITH = True
except Exception:  # langsmith 미설치
    _LANGSMITH = False

    def _ls_traceable(*dargs, **dkwargs):
        # @_ls_traceable 와 @_ls_traceable(...) 둘 다 지원하는 no-op
        if len(dargs) == 1 and callable(dargs[0]) and not dkwargs:
            return dargs[0]

        def _deco(fn):
            return fn

        return _deco


def _ls_model_name(model_id: str) -> str:
    """Bedrock 모델 id를 LangSmith 가격표가 인식하는 1P 모델명으로 정규화한다.

    예) 'jp.anthropic.claude-sonnet-4-6' / 'anthropic.claude-sonnet-4-6'
        → 'claude-sonnet-4-6'
    리전 추론 프로파일 프리픽스(jp./us./eu./apac. 등)와 'anthropic.' 프로바이더
    프리픽스를 떼어낸다. LangSmith는 이 이름으로 단가를 찾아 비용을 계산한다.
    """
    parts = (model_id or "").split(".")
    if "anthropic" in parts:
        return ".".join(parts[parts.index("anthropic") + 1 :]) or model_id
    return model_id


def _report_usage_to_langsmith(model_id: str, input_tokens: int, output_tokens: int) -> None:
    """현재 트레이싱 중이면 run에 토큰/모델 메타를 붙인다(비활성/실패 시 무시).

    토큰은 RunTree.outputs의 usage_metadata 키로 넣어야 LangSmith가 집계·과금한다.
    (langsmith 0.8.x RunTree에는 usage_metadata 필드가 없어 직접 대입은 무시된다.)
    비용은 extra.metadata의 ls_model_name을 가격표와 매칭해 계산되므로 1P 모델명을 보낸다.
    """
    if not _LANGSMITH:
        return
    try:
        rt = _ls_run_tree()
        if rt is None:
            return

        in_tok, out_tok = int(input_tokens or 0), int(output_tokens or 0)
        usage = {
            "input_tokens": in_tok,
            "output_tokens": out_tok,
            "total_tokens": in_tok + out_tok,
        }
        rt.outputs = {**(rt.outputs or {}), "usage_metadata": usage}

        rt.extra = rt.extra or {}
        rt.extra.setdefault("metadata", {}).update(
            {"ls_provider": "anthropic", "ls_model_name": _ls_model_name(model_id)}
        )
    except Exception:
        pass  # 트레이싱 실패가 분석을 깨뜨리지 않게


ModelTier = Literal["sonnet"]

DEFAULT_MODEL_IDS: dict[ModelTier, str] = {
    "sonnet": "anthropic.claude-sonnet-4-6",
}

MODEL_TIER_BY_AGENT: dict[str, ModelTier] = {
    "structuring": "sonnet",
    "market": "sonnet",
    "competitor": "sonnet",
    "bm": "sonnet",
    "tech": "sonnet",
    "ip": "sonnet",
    "critic": "sonnet",
}


@dataclass(frozen=True)
class ClaudeConfig:
    provider: str = "bedrock"
    model_tier: ModelTier = "sonnet"
    model_id: str = DEFAULT_MODEL_IDS["sonnet"]
    region_name: str = "us-east-1"
    temperature: float = 0.1
    max_tokens: int = 1800


def model_tier_for_agent(agent_name: str) -> ModelTier:
    return MODEL_TIER_BY_AGENT.get(agent_name, "sonnet")


def _model_id_for_tier(model_tier: ModelTier) -> str:
    return os.getenv(
        "BEDROCK_SONNET_MODEL_ID",
        os.getenv("BEDROCK_MODEL_ID", DEFAULT_MODEL_IDS[model_tier]),
    )


def load_claude_config(model_tier: ModelTier = "sonnet") -> ClaudeConfig:
    return ClaudeConfig(
        provider=os.getenv("AGENT_LLM_PROVIDER", "bedrock").lower(),
        model_tier=model_tier,
        model_id=_model_id_for_tier(model_tier),
        region_name=os.getenv(
            "AWS_REGION",
            os.getenv("AWS_DEFAULT_REGION", "us-east-1"),
        ),
        temperature=float(os.getenv("BEDROCK_TEMPERATURE", "0.1")),
        max_tokens=int(os.getenv("BEDROCK_MAX_TOKENS", "1800")),
    )


def _require_bedrock(config: ClaudeConfig) -> None:
    if config.provider != "bedrock":
        raise RuntimeError(
            "AGENT_LLM_PROVIDER must be 'bedrock'. "
            "Live analysis does not allow fixed LLM output."
        )


def current_model_name(agent_name: str = "structuring") -> str:
    config = load_claude_config(model_tier_for_agent(agent_name))
    _require_bedrock(config)
    return f"bedrock:{config.model_tier}:{config.model_id}"


def validate_bedrock_environment() -> dict[str, str]:
    config = load_claude_config("sonnet")
    _require_bedrock(config)

    try:
        import boto3
    except ImportError as exc:
        raise RuntimeError(
            "boto3 is not installed. Install requirements.txt first."
        ) from exc

    session = boto3.Session(region_name=config.region_name)
    credentials = session.get_credentials()
    if credentials is None:
        raise RuntimeError(
            "AWS credentials were not found. Configure AWS_PROFILE or "
            "AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY."
        )

    return {
        "provider": config.provider,
        "region": config.region_name,
        "model_id": config.model_id,
        "credential_method": getattr(credentials, "method", "unknown"),
    }


@_ls_traceable(run_type="llm", name="bedrock_converse")
def invoke_claude_json(
    *,
    system: str,
    user: str,
    model_tier: ModelTier = "sonnet",
) -> dict[str, Any]:
    """Invoke Claude and fail the run if Bedrock or JSON parsing fails."""

    config = load_claude_config(model_tier)
    _require_bedrock(config)

    try:
        import boto3
        from botocore.config import Config as _BotoConfig

        client = boto3.client(
            "bedrock-runtime",
            region_name=config.region_name,
            config=_BotoConfig(
                read_timeout=int(os.getenv("BEDROCK_READ_TIMEOUT", "300")),
                connect_timeout=10,
                retries={"max_attempts": 2},
            ),
        )
        response = client.converse(
            modelId=config.model_id,
            system=[{"text": system}],
            messages=[
                {
                    "role": "user",
                    "content": [{"text": user}],
                }
            ],
            inferenceConfig={
                "temperature": config.temperature,
                "maxTokens": config.max_tokens,
            },
        )
        # 토큰 사용량 캡처(실측 비용 + LangSmith 트레이싱). 파싱 실패와 무관하게
        # 호출 자체는 과금되므로 parse 이전에 기록한다.
        _usage = response.get("usage", {}) or {}
        _in_tok, _out_tok = _usage.get("inputTokens", 0), _usage.get("outputTokens", 0)
        _record_usage(_in_tok, _out_tok)
        _report_usage_to_langsmith(config.model_id, _in_tok, _out_tok)
        parsed = _parse_json_object(_collect_text(response))
    except Exception as exc:
        raise RuntimeError(
            f"Bedrock Claude invocation failed: {type(exc).__name__}: {exc}"
        ) from exc

    if not isinstance(parsed, dict):
        raise RuntimeError("Bedrock Claude response must be a JSON object.")

    parsed["llm_provider"] = "bedrock"
    parsed["llm_model_id"] = config.model_id
    parsed["llm_succeeded"] = True
    return parsed


def _collect_text(response: dict[str, Any]) -> str:
    parts = response.get("output", {}).get("message", {}).get("content", [])
    return "\n".join(
        part["text"]
        for part in parts
        if isinstance(part, dict) and isinstance(part.get("text"), str)
    )


def _parse_json_object(text: str) -> Any:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`")
        if stripped.lower().startswith("json"):
            stripped = stripped[4:].strip()

    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise
        return json.loads(stripped[start : end + 1])
