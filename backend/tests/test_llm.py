from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.agent.llm import OpenAILLM
from app.agent.schemas import GeneratedCode
from app.core.config import Settings
from app.core.errors import LLMError


class FakeResponses:
    def __init__(self, response):
        self.response = response
        self.params = None

    def parse(self, **params):
        self.params = params
        return self.response


def response(*, parsed=None, output=None, status="completed"):
    return SimpleNamespace(
        model="gpt-5.6-luna",
        status=status,
        incomplete_details=None,
        output=output or [],
        output_parsed=parsed,
        usage=SimpleNamespace(
            input_tokens=100,
            output_tokens=20,
            input_tokens_details=SimpleNamespace(cached_tokens=40, cache_write_tokens=10),
        ),
    )


def test_openai_llm_uses_responses_structured_outputs():
    settings = Settings(_env_file=None, openai_api_key="test", analyst_effort="high")
    llm = OpenAILLM(settings)
    fake = FakeResponses(response(parsed=GeneratedCode(approach="sum", code="print(1)")))
    llm._client = SimpleNamespace(responses=fake)

    result = llm.generate(
        system=[{"type": "text", "text": "Rules"},
                {"type": "text", "text": "Dataset", "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": "Analyse it"}],
        schema=GeneratedCode,
        purpose="code",
    )

    assert fake.params["model"] == "gpt-5.6-luna"
    assert fake.params["input"][0] == {"role": "developer", "content": "Rules\n\nDataset"}
    assert fake.params["input"][1]["role"] == "user"
    assert fake.params["text_format"] is GeneratedCode
    assert fake.params["reasoning"] == {"effort": "high"}
    assert fake.params["store"] is False
    assert result.usage["cache_read_tokens"] == 40
    assert result.usage["cache_write_tokens"] == 10


def test_openai_llm_surfaces_refusals():
    refusal = SimpleNamespace(type="refusal", refusal="I cannot help with that.")
    message = SimpleNamespace(content=[refusal])
    llm = OpenAILLM(Settings(_env_file=None, openai_api_key="test"))
    llm._client = SimpleNamespace(responses=FakeResponses(response(output=[message])))

    with pytest.raises(LLMError) as exc:
        llm.generate(system=[], messages=[], schema=GeneratedCode, purpose="code")
    assert exc.value.code == "llm_refusal"
