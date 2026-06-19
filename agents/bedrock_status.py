"""Summarize strict Bedrock execution status."""

from __future__ import annotations

from typing import Any


BM_DOMAIN_FIELDS = (
    "revenue_model",
    "pricing_hypothesis",
    "market_size_signal",
    "unit_economics",
    "key_risk",
)


def _dump(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        return value.model_dump()
    return {}


def summarize_bedrock_run(result: dict[str, Any]) -> dict[str, Any]:
    agent_status = []
    bm_output: dict[str, Any] = {}

    for value in result.get("agent_runs", []):
        run = _dump(value)
        output = run.get("output_json") or {}
        agent_name = str(run.get("agent_name") or "unknown")
        succeeded = output.get("llm_succeeded") is True
        agent_status.append(
            {
                "agent_name": agent_name,
                "model_name": run.get("model_name"),
                "llm_succeeded": succeeded,
            }
        )
        if agent_name == "bm":
            bm_output = output

    bm_missing_fields = [
        field
        for field in BM_DOMAIN_FIELDS
        if not str(bm_output.get(field) or "").strip()
    ]
    failed_agents = [
        item["agent_name"]
        for item in agent_status
        if not item["llm_succeeded"]
    ]

    return {
        "bedrock_verified": bool(agent_status) and not failed_agents,
        "agent_status": agent_status,
        "failed_agents": failed_agents,
        "bm_fields_complete": not bm_missing_fields,
        "bm_fields_generated_by_claude": (
            not bm_missing_fields
            and any(
                item["agent_name"] == "bm" and item["llm_succeeded"]
                for item in agent_status
            )
        ),
        "bm_missing_fields": bm_missing_fields,
    }
