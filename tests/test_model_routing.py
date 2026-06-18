import sys
from types import SimpleNamespace

from agents import graph
from agents.llm import (
    current_model_name,
    invoke_claude_json,
    load_claude_config,
    model_tier_for_agent,
)


def test_agent_model_tiers():
    assert model_tier_for_agent("structuring") == "haiku"
    assert model_tier_for_agent("market") == "haiku"
    assert model_tier_for_agent("competitor") == "haiku"
    assert model_tier_for_agent("bm") == "haiku"
    assert model_tier_for_agent("tech") == "haiku"
    assert model_tier_for_agent("ip") == "haiku"
    assert model_tier_for_agent("critic") == "haiku"


def test_all_agents_use_same_haiku_model(monkeypatch):
    monkeypatch.setenv("AGENT_LLM_PROVIDER", "bedrock")
    monkeypatch.setenv("BEDROCK_HAIKU_MODEL_ID", "haiku-test")

    assert load_claude_config("haiku").model_id == "haiku-test"
    assert current_model_name("tech") == "bedrock:haiku:haiku-test"
    assert current_model_name("ip") == "bedrock:haiku:haiku-test"
    assert current_model_name("critic") == "bedrock:haiku:haiku-test"


def test_graph_passes_agent_tier_to_llm(monkeypatch):
    called_tiers = []

    def fake_invoke_claude_json(*, system, user, fallback, model_tier):
        called_tiers.append(model_tier)
        return fallback

    monkeypatch.setattr(graph, "invoke_claude_json", fake_invoke_claude_json)

    for agent_name in ("market", "competitor", "bm", "tech", "ip", "critic"):
        graph._agent_output_with_llm(
            agent_name=agent_name,
            hypothesis_id="H-test",
            role="test",
            default_output={"summary": "test"},
            context={},
        )

    assert called_tiers == [
        "haiku",
        "haiku",
        "haiku",
        "haiku",
        "haiku",
        "haiku",
    ]


def test_invoke_uses_selected_tier_model_id(monkeypatch):
    requested_model_ids = []

    class FakeClient:
        def converse(self, *, modelId, **kwargs):
            requested_model_ids.append(modelId)
            return {
                "output": {
                    "message": {
                        "content": [{"text": '{"summary": "ok"}'}]
                    }
                }
            }

    fake_boto3 = SimpleNamespace(
        client=lambda service_name, region_name: FakeClient()
    )
    monkeypatch.setitem(sys.modules, "boto3", fake_boto3)
    monkeypatch.setenv("AGENT_LLM_PROVIDER", "bedrock")
    monkeypatch.setenv("BEDROCK_HAIKU_MODEL_ID", "haiku-selected")

    output = invoke_claude_json(
        system="system",
        user="user",
        fallback={"summary": "fallback"},
        model_tier="haiku",
    )

    assert requested_model_ids == ["haiku-selected"]
    assert output["summary"] == "ok"
    assert output["llm_model_id"] == "haiku-selected"
    assert output["llm_succeeded"] is True
