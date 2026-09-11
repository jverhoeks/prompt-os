from __future__ import annotations

import io
import json
from typing import Any

import pytest

from prompt_os.model_client import LiteLLMConfig, check_model, create_model


class _Response(io.BytesIO):
    def __enter__(self) -> "_Response":
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()


def _response(payload: dict[str, Any]) -> _Response:
    return _Response(json.dumps(payload).encode())


def test_model_uses_provider_defaults_for_optional_parameters() -> None:
    model = create_model(LiteLLMConfig("https://proxy.example/v1", "secret", "chat"))

    assert "params" not in model.get_config()


def test_model_check_reads_proxy_capabilities(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_urlopen(request: Any, timeout: int) -> _Response:
        assert request.full_url == "https://proxy.example/v1/model/info"
        assert timeout == 10
        return _response(
            {
                "data": [
                    {
                        "model_name": "chat",
                        "model_info": {
                            "mode": "chat",
                            "supports_function_calling": True,
                            "supported_openai_params": ["stream", "tools"],
                        },
                    }
                ]
            }
        )

    monkeypatch.setattr("prompt_os.model_client.urlopen", fake_urlopen)

    capabilities = check_model(
        LiteLLMConfig("https://proxy.example/v1/", "secret", "chat")
    )

    assert capabilities.mode == "chat"
    assert capabilities.supported_parameters == ("stream", "tools")


def test_model_check_rejects_unavailable_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "prompt_os.model_client.urlopen", lambda request, timeout: _response({"data": []})
    )

    with pytest.raises(RuntimeError, match="not available to this API key"):
        check_model(LiteLLMConfig("https://proxy.example/v1", "secret", "missing"))


def test_model_check_rejects_model_without_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "prompt_os.model_client.urlopen",
        lambda request, timeout: _response(
            {
                "data": [
                    {
                        "model_name": "chat",
                        "model_info": {
                            "mode": "chat",
                            "supported_openai_params": ["stream"],
                        },
                    }
                ]
            }
        ),
    )

    with pytest.raises(RuntimeError, match="does not accept the tools parameter"):
        check_model(LiteLLMConfig("https://proxy.example/v1", "secret", "chat"))
