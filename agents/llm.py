"""Bedrock Claude 호출을 담당하는 작은 어댑터.

기본 실행은 기존 mock 모드로 유지한다.
환경변수 ``AGENT_LLM_PROVIDER=bedrock``일 때만 AWS Bedrock Runtime의
Converse API를 호출한다.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Literal


ModelTier = Literal["haiku"]

DEFAULT_MODEL_IDS: dict[ModelTier, str] = {
    "haiku": "anthropic.claude-3-haiku-20240307-v1:0",
}

MODEL_TIER_BY_AGENT: dict[str, ModelTier] = {
    "structuring": "haiku",
    "market": "haiku",
    "competitor": "haiku",
    "bm": "haiku",
    "tech": "haiku",
    "ip": "haiku",
    "critic": "haiku",
}


@dataclass(frozen=True)
class ClaudeConfig:
    """Bedrock Claude 실행 설정."""

    provider: str = "mock"
    model_tier: ModelTier = "haiku"
    model_id: str = DEFAULT_MODEL_IDS["haiku"]
    region_name: str = "us-east-1"
    temperature: float = 0.1
    max_tokens: int = 1800


def model_tier_for_agent(agent_name: str) -> ModelTier:
    """에이전트 역할에 맞는 Claude 등급을 반환한다."""

    return MODEL_TIER_BY_AGENT.get(agent_name, "haiku")


def _model_id_for_tier(model_tier: ModelTier) -> str:
    """Haiku model/inference profile ID를 환경변수에서 읽는다."""

    return os.getenv(
        "BEDROCK_HAIKU_MODEL_ID",
        os.getenv("BEDROCK_MODEL_ID", DEFAULT_MODEL_IDS[model_tier]),
    )


def load_claude_config(model_tier: ModelTier = "haiku") -> ClaudeConfig:
    """환경변수에서 지정 등급의 Bedrock Claude 설정을 읽는다."""

    return ClaudeConfig(
        provider=os.getenv("AGENT_LLM_PROVIDER", "mock").lower(),
        model_tier=model_tier,
        model_id=_model_id_for_tier(model_tier),
        region_name=os.getenv(
            "AWS_REGION",
            os.getenv("AWS_DEFAULT_REGION", "us-east-1"),
        ),
        temperature=float(os.getenv("BEDROCK_TEMPERATURE", "0.1")),
        max_tokens=int(os.getenv("BEDROCK_MAX_TOKENS", "1800")),
    )


def llm_enabled() -> bool:
    """현재 실행이 Bedrock LLM 모드인지 확인한다."""

    return load_claude_config("haiku").provider == "bedrock"


def current_model_name(agent_name: str = "structuring") -> str:
    """AgentRun에 기록할 모델 이름을 반환한다."""

    config = load_claude_config(model_tier_for_agent(agent_name))
    if config.provider == "bedrock":
        return f"bedrock:{config.model_tier}:{config.model_id}"
    return "mock"


def validate_bedrock_environment() -> dict[str, str]:
    """네트워크 호출 전에 Bedrock 실행에 필요한 로컬 설정을 점검한다.

    자격증명의 실제 유효성과 모델 접근 권한은 Converse API 응답으로 최종 확인한다.
    """

    config = load_claude_config("haiku")
    if config.provider != "bedrock":
        raise RuntimeError(
            "AGENT_LLM_PROVIDER가 bedrock이 아닙니다."
        )

    try:
        import boto3
    except ImportError as exc:
        raise RuntimeError(
            "boto3가 설치되어 있지 않습니다. requirements.txt를 설치하세요."
        ) from exc

    session = boto3.Session(region_name=config.region_name)
    credentials = session.get_credentials()
    if credentials is None:
        raise RuntimeError(
            "AWS 자격증명을 찾을 수 없습니다. AWS_PROFILE 또는 "
            "AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY를 설정하세요."
        )

    # access key 전체를 출력하지 않고 자격증명 출처만 확인한다.
    credential_method = getattr(credentials, "method", "unknown")
    return {
        "provider": config.provider,
        "region": config.region_name,
        "haiku_model_id": config.model_id,
        "credential_method": credential_method,
    }


def _fallback_with_error(
    fallback: dict[str, Any],
    *,
    config: ClaudeConfig,
    error: str,
) -> dict[str, Any]:
    """Bedrock 실패를 output_json에서 명확히 식별할 수 있게 표시한다."""

    result = dict(fallback)
    result.update(
        {
            "llm_provider": "bedrock",
            "llm_model_id": config.model_id,
            "llm_succeeded": False,
            "llm_fallback_used": True,
            "llm_error": error,
        }
    )
    return result


def invoke_claude_json(
    *,
    system: str,
    user: str,
    fallback: dict[str, Any],
    model_tier: ModelTier = "haiku",
) -> dict[str, Any]:
    """Claude에게 JSON 출력을 요청하고 실패하면 fallback을 반환한다.

    네트워크, 인증, 권한, JSON 파싱 오류가 있어도 그래프 전체가 깨지지 않게
    기본 mock 결과를 그대로 돌려준다.
    """

    if not llm_enabled():
        return fallback

    config = load_claude_config(model_tier)

    try:
        import boto3

        client = boto3.client(
            "bedrock-runtime",
            region_name=config.region_name,
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
        text = _collect_text(response)
        parsed = _parse_json_object(text)
        if not isinstance(parsed, dict):
            return _fallback_with_error(
                fallback,
                config=config,
                error="Bedrock 응답이 JSON object가 아닙니다.",
            )
        return _merge_dicts(
            fallback,
            parsed,
            model_id=config.model_id,
        )
    except Exception as exc:
        return _fallback_with_error(
            fallback,
            config=config,
            error=f"{type(exc).__name__}: {exc}",
        )


def _collect_text(response: dict[str, Any]) -> str:
    """Bedrock Converse 응답에서 text 조각을 모은다."""

    parts = response.get("output", {}).get("message", {}).get("content", [])
    return "\n".join(
        part["text"]
        for part in parts
        if isinstance(part, dict) and isinstance(part.get("text"), str)
    )


def _parse_json_object(text: str) -> Any:
    """코드블록이 섞인 응답에서도 첫 JSON object를 파싱한다."""

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


def _merge_dicts(
    fallback: dict[str, Any],
    parsed: dict[str, Any],
    *,
    model_id: str,
) -> dict[str, Any]:
    """기본 키는 보존하고 Claude가 준 값으로 보강한다."""

    merged = dict(fallback)
    merged.update(parsed)
    merged["llm_provider"] = "bedrock"
    merged["llm_model_id"] = model_id
    merged["llm_succeeded"] = True
    merged["llm_fallback_used"] = False
    return merged
