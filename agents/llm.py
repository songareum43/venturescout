"""Strict Amazon Bedrock Claude adapter for live VentureScout runs."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Literal

from dotenv import load_dotenv

load_dotenv()


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
