from __future__ import annotations

from dataclasses import dataclass
import json
import os
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from strands.models.litellm import LiteLLMModel


@dataclass(frozen=True)
class LiteLLMConfig:
    base_url: str
    api_key: str
    model: str

    @classmethod
    def from_environment(cls) -> "LiteLLMConfig":
        names = ("LITELLM_BASE_URL", "LITELLM_API_KEY", "LITELLM_MODEL")
        values = {name: os.environ.get(name, "") for name in names}
        missing = [name for name, value in values.items() if not value]
        if missing:
            raise ValueError(f"missing environment variables: {', '.join(missing)}")
        return cls(
            base_url=values["LITELLM_BASE_URL"],
            api_key=values["LITELLM_API_KEY"],
            model=values["LITELLM_MODEL"],
        )


@dataclass(frozen=True)
class ModelCapabilities:
    model: str
    mode: str | None = None
    supported_parameters: tuple[str, ...] = ()


def check_model(config: LiteLLMConfig) -> ModelCapabilities:
    """Fail early when the proxy cannot serve the configured chat model."""
    try:
        payload = _get_proxy_json(config, "model/info")
    except RuntimeError as info_error:
        try:
            payload = _get_proxy_json(config, "models")
        except RuntimeError as models_error:
            raise RuntimeError(
                f"could not check LiteLLM model {config.model!r}: {models_error}"
            ) from info_error

    records = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(records, list):
        raise RuntimeError("LiteLLM model check returned an invalid response")

    match = next(
        (
            record
            for record in records
            if isinstance(record, dict) and record.get("model_name") == config.model
        ),
        None,
    )
    if match is None:
        match = next(
            (
                record
                for record in records
                if isinstance(record, dict) and record.get("id") == config.model
            ),
            None,
        )
    if match is None:
        raise RuntimeError(
            f"LiteLLM model {config.model!r} is not available to this API key"
        )

    info = match.get("model_info") if isinstance(match.get("model_info"), dict) else {}
    mode = info.get("mode")
    if mode not in (None, "chat"):
        raise RuntimeError(
            f"LiteLLM model {config.model!r} has mode {mode!r}; chat is required"
        )
    if info.get("supports_function_calling") is False:
        raise RuntimeError(
            f"LiteLLM model {config.model!r} does not support the tools required by the harness"
        )

    parameters = info.get("supported_openai_params")
    supported = (
        tuple(item for item in parameters if isinstance(item, str))
        if isinstance(parameters, list)
        else ()
    )
    if supported and "tools" not in supported:
        raise RuntimeError(
            f"LiteLLM model {config.model!r} does not accept the tools parameter"
        )
    return ModelCapabilities(config.model, mode, supported)


def _get_proxy_json(config: LiteLLMConfig, path: str) -> dict[str, Any]:
    url = f"{config.base_url.rstrip('/')}/{path}"
    request = Request(
        url,
        headers={"Authorization": f"Bearer {config.api_key}", "Accept": "application/json"},
    )
    try:
        with urlopen(request, timeout=10) as response:
            payload = json.load(response)
    except HTTPError as exc:
        raise RuntimeError(f"{url} returned HTTP {exc.code}") from exc
    except (URLError, TimeoutError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        raise RuntimeError(f"could not reach {url}: {reason}") from exc
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise RuntimeError(f"{url} did not return valid JSON") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"{url} did not return a JSON object")
    return payload


def create_model(config: LiteLLMConfig) -> LiteLLMModel:
    return LiteLLMModel(
        client_args={
            "api_key": config.api_key,
            "api_base": config.base_url,
            "use_litellm_proxy": True,
        },
        model_id=config.model,
    )
