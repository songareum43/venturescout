from agents.bedrock_status import BM_DOMAIN_FIELDS, summarize_bedrock_run


def _run(agent_name: str, output_json: dict):
    return {
        "agent_name": agent_name,
        "model_name": "bedrock:test-model",
        "output_json": output_json,
    }


def test_bedrock_status_accepts_successful_bm_fields():
    bm_fields = {
        field: f"Claude generated {field}"
        for field in BM_DOMAIN_FIELDS
    }
    result = {
        "agent_runs": [
            _run("market", {"llm_succeeded": True}),
            _run("bm", {"llm_succeeded": True, **bm_fields}),
            _run("critic", {"llm_succeeded": True}),
        ]
    }

    status = summarize_bedrock_run(result)

    assert status["bedrock_verified"] is True
    assert status["bm_fields_complete"] is True
    assert status["bm_fields_generated_by_claude"] is True


def test_bedrock_status_rejects_fallback_and_mock_bm_fields():
    bm_fields = {
        field: f"[MOCK] {field}"
        for field in BM_DOMAIN_FIELDS
    }
    result = {
        "agent_runs": [
            _run(
                "market",
                {
                    "llm_succeeded": False,
                    "llm_fallback_used": True,
                    "llm_error": "AccessDeniedException",
                },
            ),
            _run("bm", {"llm_succeeded": True, **bm_fields}),
        ]
    }

    status = summarize_bedrock_run(result)

    assert status["bedrock_verified"] is False
    assert status["failed_agents"] == ["market"]
    assert status["bm_fields_generated_by_claude"] is False
    assert sorted(status["bm_mock_fields"]) == sorted(BM_DOMAIN_FIELDS)
