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
    assert model_tier_for_agent("structuring") == "sonnet"
    assert model_tier_for_agent("market") == "sonnet"
    assert model_tier_for_agent("competitor") == "sonnet"
    assert model_tier_for_agent("bm") == "sonnet"
    assert model_tier_for_agent("tech") == "sonnet"
    assert model_tier_for_agent("ip") == "sonnet"
    assert model_tier_for_agent("critic") == "sonnet"


def test_all_agents_use_same_sonnet_model(monkeypatch):
    monkeypatch.setenv("AGENT_LLM_PROVIDER", "bedrock")
    monkeypatch.setenv("BEDROCK_SONNET_MODEL_ID", "sonnet-test")

    assert load_claude_config("sonnet").model_id == "sonnet-test"
    assert current_model_name("tech") == "bedrock:sonnet:sonnet-test"
    assert current_model_name("ip") == "bedrock:sonnet:sonnet-test"
    assert current_model_name("critic") == "bedrock:sonnet:sonnet-test"


def test_graph_passes_agent_tier_to_llm(monkeypatch):
    called_tiers = []

    def fake_invoke_claude_json(*, system, user, model_tier):
        called_tiers.append(model_tier)
        return {"summary": "test"}

    monkeypatch.setattr(graph, "invoke_claude_json", fake_invoke_claude_json)

    for agent_name in ("market", "competitor", "bm", "tech", "ip", "critic"):
        graph._agent_output_with_llm(
            agent_name=agent_name,
            hypothesis_id="H-test",
            role="test",
            required_fields=["summary"],
            context={},
        )

    assert called_tiers == [
        "sonnet",
        "sonnet",
        "sonnet",
        "sonnet",
        "sonnet",
        "sonnet",
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
        client=lambda service_name, region_name, **kwargs: FakeClient()
    )
    monkeypatch.setitem(sys.modules, "boto3", fake_boto3)
    monkeypatch.setenv("AGENT_LLM_PROVIDER", "bedrock")
    monkeypatch.setenv("BEDROCK_SONNET_MODEL_ID", "sonnet-selected")

    output = invoke_claude_json(
        system="system",
        user="user",
        model_tier="sonnet",
    )

    assert requested_model_ids == ["sonnet-selected"]
    assert output["summary"] == "ok"
    assert output["llm_model_id"] == "sonnet-selected"
    assert output["llm_succeeded"] is True
