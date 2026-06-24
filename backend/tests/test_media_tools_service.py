from __future__ import annotations

import base64
import json

from anvil.config import ConfigLayer, ConfigLayerKind, ConfigService
from anvil.config.model_routing import ModelRouteRequest, RequiredModelCapabilities, resolve_model_route
from anvil.media_tools import MediaToolsService
from anvil.runtime.tool_registry import CapabilityAssemblyService
from anvil.sandbox import PathService, create_sandbox_provider


def _config(media_tools: dict[str, object]):
    return ConfigService().resolve(
        [
            ConfigLayer(
                name="test",
                kind=ConfigLayerKind.DEFAULT,
                data={
                    "default_model": "openai",
                    "models": {
                        "openai": {
                            "name": "openai",
                            "provider": "openai",
                            "provider_kind": "openai_compatible",
                            "model": "gpt-5.4",
                        }
                    },
                    "media_tools": media_tools,
                },
            )
        ]
    )


def test_media_tools_use_mock_contracts_without_network(contract_tmp_path) -> None:
    config_result = _config(
        {
            "tts": {"providers": ["mock"], "mock_audio_bytes": "audio-bytes"},
            "stt": {"providers": ["mock"], "mock_transcripts": {"sample.wav": "hello transcript"}},
        }
    )
    output_path = contract_tmp_path / "threads" / "thread-media" / "outputs" / "audio" / "sample.mp3"
    input_path = contract_tmp_path / "threads" / "thread-media" / "uploads" / "sample.wav"
    input_path.parent.mkdir(parents=True)
    input_path.write_bytes(b"wav-bytes")
    service = MediaToolsService()

    tts = service.text_to_speech(
        config_result=config_result,
        text="hello",
        output_path=output_path,
        output_virtual_path="/mnt/user-data/outputs/audio/sample.mp3",
        provider=None,
    )
    stt = service.speech_to_text(
        config_result=config_result,
        input_path=input_path,
        input_virtual_path="/mnt/user-data/uploads/sample.wav",
        provider=None,
    )

    assert tts["success"] is True
    assert tts["provider"] == "mock"
    assert tts["output_path"] == "/mnt/user-data/outputs/audio/sample.mp3"
    assert output_path.read_bytes() == b"audio-bytes"
    assert stt["success"] is True
    assert stt["provider"] == "mock"
    assert stt["transcript"] == "hello transcript"


def test_image_generation_uses_mock_contract_and_writes_output_artifact(contract_tmp_path) -> None:
    config_result = _config(
        {
            "image_generation": {
                "providers": ["mock"],
                "mock_image_bytes": "image-bytes",
                "model": "mock-image",
                "size": "1024x1024",
            }
        }
    )
    output_path = contract_tmp_path / "threads" / "thread-media" / "outputs" / "images" / "sample.png"

    payload = MediaToolsService().image_generate(
        config_result=config_result,
        prompt="A clean product UI screenshot",
        output_path=output_path,
        output_virtual_path="/mnt/user-data/outputs/images/sample.png",
        provider=None,
    )

    assert payload["success"] is True
    assert payload["provider"] == "mock"
    assert payload["output_path"] == "/mnt/user-data/outputs/images/sample.png"
    assert payload["format"] == "png"
    assert payload["model"] == "mock-image"
    assert output_path.read_bytes() == b"image-bytes"


def test_openai_image_generation_uses_output_format_without_default_response_format(monkeypatch, contract_tmp_path) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-testsecretsecretsecret")
    config_result = _config(
        {
            "image_generation": {
                "providers": ["openai"],
                "model": "gpt-image-1",
                "endpoint": "/images/generations",
                "output_format": "png",
            }
        }
    )
    output_path = contract_tmp_path / "threads" / "thread-media" / "outputs" / "images" / "sample.png"
    captured: dict[str, object] = {}

    def fake_json_request(*args, **kwargs):
        captured["url"] = args[0]
        captured["payload"] = kwargs["payload"]
        captured["headers"] = kwargs["headers"]
        return {"data": [{"b64_json": base64.b64encode(b"png-bytes").decode("ascii")}]}

    monkeypatch.setattr("anvil.media_tools.service._json_request", fake_json_request)

    payload = MediaToolsService().image_generate(
        config_result=config_result,
        prompt="A clean product UI screenshot",
        output_path=output_path,
        output_virtual_path="/mnt/user-data/outputs/images/sample.png",
        provider=None,
    )

    request_payload = captured["payload"]
    assert isinstance(request_payload, dict)
    assert payload["success"] is True
    assert captured["url"] == "https://api.openai.com/v1/images/generations"
    assert request_payload["output_format"] == "png"
    assert "response_format" not in request_payload
    assert output_path.read_bytes() == b"png-bytes"


def test_minimax_image_generation_uses_configured_endpoint_suffix_and_downloads_url(monkeypatch, contract_tmp_path) -> None:
    monkeypatch.setenv("MINIMAX_API_KEY", "sk-testsecretsecretsecret")
    config_result = _config({"image_generation": {}})
    output_path = contract_tmp_path / "threads" / "thread-media" / "outputs" / "images" / "sample.png"
    captured: dict[str, object] = {}

    def fake_json_request(*args, **kwargs):
        captured["url"] = args[0]
        captured["payload"] = kwargs["payload"]
        captured["headers"] = kwargs["headers"]
        return {
            "data": {"image_urls": ["https://cdn.example.test/generated.png"]},
            "base_resp": {"status_code": 0, "status_msg": "success"},
        }

    def fake_raw_request(*args, **kwargs):
        captured["download_url"] = args[0]
        return b"minimax-image-bytes"

    monkeypatch.setattr("anvil.media_tools.service._json_request", fake_json_request)
    monkeypatch.setattr("anvil.media_tools.service._raw_request", fake_raw_request)

    payload = MediaToolsService().image_generate(
        config_result=config_result,
        prompt="A clean landscape photograph",
        output_path=output_path,
        output_virtual_path="/mnt/user-data/outputs/images/sample.png",
        provider=None,
        model_image_generation={
            "providers": ["minimax"],
            "base_url": "https://api.minimaxi.com/v1",
            "endpoint": "/image_generation",
            "api_key": "$MINIMAX_API_KEY",
            "model": "image-01",
            "aspect_ratio": "16:9",
        },
    )

    request_payload = captured["payload"]
    assert isinstance(request_payload, dict)
    assert payload["success"] is True
    assert payload["provider"] == "minimax"
    assert captured["url"] == "https://api.minimaxi.com/v1/image_generation"
    assert captured["download_url"] == "https://cdn.example.test/generated.png"
    assert request_payload["model"] == "image-01"
    assert request_payload["aspect_ratio"] == "16:9"
    assert request_payload["response_format"] == "url"
    assert output_path.read_bytes() == b"minimax-image-bytes"


def test_minimax_image_generation_requires_configured_endpoint_suffix(monkeypatch, contract_tmp_path) -> None:
    monkeypatch.setenv("MINIMAX_API_KEY", "sk-testsecretsecretsecret")
    config_result = _config({"image_generation": {}})
    output_path = contract_tmp_path / "threads" / "thread-media" / "outputs" / "images" / "missing.png"

    payload = MediaToolsService().image_generate(
        config_result=config_result,
        prompt="A clean landscape photograph",
        output_path=output_path,
        output_virtual_path="/mnt/user-data/outputs/images/missing.png",
        provider=None,
        model_image_generation={
            "providers": ["minimax"],
            "base_url": "https://api.minimaxi.com/v1",
            "api_key": "$MINIMAX_API_KEY",
            "model": "image-01",
        },
    )

    assert payload["success"] is False
    assert not output_path.exists()
    assert "image_generation.endpoint" in payload["errors"][0]["error"]


def test_image_generation_failure_does_not_return_phantom_output_path(monkeypatch, contract_tmp_path) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-testsecretsecretsecret")
    config_result = _config({"image_generation": {"providers": ["openai"], "model": "gpt-image-1", "endpoint": "/images/generations"}})
    output_path = contract_tmp_path / "threads" / "thread-media" / "outputs" / "images" / "missing.png"

    def fake_json_request(*args, **kwargs):
        raise RuntimeError("HTTP 404: 404 Page not found")

    monkeypatch.setattr("anvil.media_tools.service._json_request", fake_json_request)

    payload = MediaToolsService().image_generate(
        config_result=config_result,
        prompt="A clean product UI screenshot",
        output_path=output_path,
        output_virtual_path="/mnt/user-data/outputs/images/missing.png",
        provider=None,
    )

    assert payload["success"] is False
    assert "output_path" not in payload
    assert not output_path.exists()


def test_runtime_media_tool_handlers_are_visible_and_use_virtual_paths(contract_tmp_path) -> None:
    config_result = _config(
        {
            "tts": {"providers": ["mock"], "mock_audio_bytes": "audio-bytes"},
            "stt": {"providers": ["mock"], "mock_transcript": "fallback transcript"},
        }
    )
    path_service = PathService(contract_tmp_path / "threads")
    sandbox_provider = create_sandbox_provider(config_result.effective_config)
    sandbox_handle = sandbox_provider.acquire(thread_id="thread-media-tools", path_service=path_service)
    upload_path = path_service.thread_uploads_dir("thread-media-tools") / "sample.wav"
    upload_path.parent.mkdir(parents=True, exist_ok=True)
    upload_path.write_bytes(b"wav-bytes")

    result = CapabilityAssemblyService().assemble(
        sandbox_handle=sandbox_handle,
        config_result=config_result,
        feature_set=__import__("anvil.agents.features", fromlist=["RuntimeFeatureSet"]).RuntimeFeatureSet(),
    )
    handlers = {entry.name: entry.handler for entry in result.bundle.visible_tools}

    assert {"text_to_speech", "speech_to_text"}.issubset(handlers)
    tts = json.loads(
        handlers["text_to_speech"].invoke(
            {
                "text": "hello",
                "output_path": "/mnt/user-data/outputs/audio/hello.mp3",
            }
        )
    )
    stt = json.loads(handlers["speech_to_text"].invoke({"input_path": "/mnt/user-data/uploads/sample.wav"}))

    assert tts["success"] is True
    assert tts["output_path"] == "/mnt/user-data/outputs/audio/hello.mp3"
    assert tts["artifact_url"].endswith("/threads/thread-media-tools/artifacts/outputs/audio/hello.mp3")
    assert path_service.thread_outputs_dir("thread-media-tools").joinpath("audio", "hello.mp3").read_bytes() == b"audio-bytes"
    assert stt["success"] is True
    assert stt["transcript"] == "fallback transcript"


def test_image_generation_tool_is_route_gated_and_returns_artifact_url(contract_tmp_path) -> None:
    config_result = ConfigService().resolve(
        [
            ConfigLayer(
                name="test",
                kind=ConfigLayerKind.DEFAULT,
                data={
                    "default_model": "openai",
                    "models": {
                        "openai": {
                            "name": "openai",
                            "provider": "openai",
                            "provider_kind": "openai_compatible",
                            "model": "gpt-5.4",
                            "supports_tool_calling": False,
                        },
                        "image_gen": {
                            "name": "image_gen",
                            "provider": "openai",
                            "provider_kind": "openai_compatible",
                            "model": "mock-image",
                            "supports_image_generation": True,
                            "image_generation": {
                                "endpoint": "mock://image-generation",
                                "providers": ["mock"],
                                "mock_image_bytes": "generated-image",
                                "model": "mock-image",
                            },
                        },
                    },
                    "media_tools": {"image_generation": {"providers": ["mock"]}},
                },
            )
        ]
    )
    service = CapabilityAssemblyService()
    path_service = PathService(contract_tmp_path / "threads")
    sandbox_provider = create_sandbox_provider(config_result.effective_config)

    text_handle = sandbox_provider.acquire(thread_id="thread-text-route", path_service=path_service)
    text_route = resolve_model_route(config_result.effective_config, ModelRouteRequest(subsystem="lead_agent"))
    text_result = service.assemble(
        sandbox_handle=text_handle,
        config_result=config_result,
        feature_set=__import__("anvil.agents.features", fromlist=["RuntimeFeatureSet"]).RuntimeFeatureSet(),
        resolved_route=text_route,
    )

    image_handle = sandbox_provider.acquire(thread_id="thread-image-route", path_service=path_service)
    image_route = resolve_model_route(
        config_result.effective_config,
        ModelRouteRequest(
            subsystem="lead_agent",
            request_override_model="image_gen",
            required_capabilities=RequiredModelCapabilities(image_generation=True),
        ),
    )
    image_result = service.assemble(
        sandbox_handle=image_handle,
        config_result=config_result,
        feature_set=__import__("anvil.agents.features", fromlist=["RuntimeFeatureSet"]).RuntimeFeatureSet(),
        resolved_route=image_route,
    )

    text_handlers = {entry.name: entry.handler for entry in text_result.bundle.visible_tools}
    image_handlers = {entry.name: entry.handler for entry in image_result.bundle.visible_tools}

    assert "image_generate" not in text_handlers
    assert "image_generate" in image_handlers

    payload = json.loads(
        image_handlers["image_generate"].invoke(
            {
                "prompt": "A clean product UI screenshot",
                "output_path": "/mnt/user-data/outputs/images/generated.png",
            }
        )
    )

    assert payload["success"] is True
    assert payload["output_path"] == "/mnt/user-data/outputs/images/generated.png"
    assert payload["artifact_url"].endswith("/threads/thread-image-route/artifacts/outputs/images/generated.png")
    assert path_service.thread_outputs_dir("thread-image-route").joinpath("images", "generated.png").read_bytes() == b"generated-image"


def test_image_generation_tool_uses_auxiliary_image_model_for_tool_calling_route(contract_tmp_path) -> None:
    config_result = ConfigService().resolve(
        [
            ConfigLayer(
                name="test",
                kind=ConfigLayerKind.DEFAULT,
                data={
                    "default_model": "openai",
                    "models": {
                        "openai": {
                            "name": "openai",
                            "provider": "openai",
                            "provider_kind": "openai_compatible",
                            "model": "gpt-5.4",
                            "supports_tool_calling": True,
                        },
                        "image_gen": {
                            "name": "image_gen",
                            "provider": "openai",
                            "provider_kind": "openai_compatible",
                            "model": "mock-image",
                            "supports_tool_calling": False,
                            "supports_image_generation": True,
                            "image_generation": {
                                "endpoint": "mock://image-generation",
                                "providers": ["mock"],
                                "mock_image_bytes": "aux-generated-image",
                                "model": "mock-image",
                            },
                        },
                    },
                },
            )
        ]
    )
    service = CapabilityAssemblyService()
    path_service = PathService(contract_tmp_path / "threads")
    sandbox_handle = create_sandbox_provider(config_result.effective_config).acquire(
        thread_id="thread-aux-image-route",
        path_service=path_service,
    )
    lead_route = resolve_model_route(config_result.effective_config, ModelRouteRequest(subsystem="lead_agent"))

    result = service.assemble(
        sandbox_handle=sandbox_handle,
        config_result=config_result,
        feature_set=__import__("anvil.agents.features", fromlist=["RuntimeFeatureSet"]).RuntimeFeatureSet(),
        resolved_route=lead_route,
    )
    handlers = {entry.name: entry.handler for entry in result.bundle.visible_tools}

    assert "image_generate" in handlers
    payload = json.loads(
        handlers["image_generate"].invoke(
            {
                "prompt": "A clean product UI screenshot",
                "output_path": "/mnt/user-data/outputs/images/aux-generated.png",
            }
        )
    )

    assert payload["success"] is True
    assert payload["provider"] == "mock"
    assert payload["model"] == "mock-image"
    assert payload["artifact_url"].endswith("/threads/thread-aux-image-route/artifacts/outputs/images/aux-generated.png")
    assert path_service.thread_outputs_dir("thread-aux-image-route").joinpath("images", "aux-generated.png").read_bytes() == b"aux-generated-image"


def test_image_generation_tool_defaults_minimax_model_to_minimax_provider(monkeypatch, contract_tmp_path) -> None:
    monkeypatch.setenv("MINIMAX_API_KEY", "sk-testsecretsecretsecret")
    config_result = ConfigService().resolve(
        [
            ConfigLayer(
                name="test",
                kind=ConfigLayerKind.DEFAULT,
                data={
                    "default_model": "lead",
                    "models": {
                        "lead": {
                            "name": "lead",
                            "provider": "openai",
                            "provider_kind": "openai_compatible",
                            "model": "gpt-5.4",
                            "supports_tool_calling": True,
                        },
                        "minimax_cn": {
                            "name": "minimax_cn",
                            "provider": "openai",
                            "provider_kind": "openai_compatible",
                            "model": "MiniMax-M2.7",
                            "base_url": "https://api.minimaxi.com/v1",
                            "api_key": "$MINIMAX_API_KEY",
                            "supports_image_generation": True,
                            "image_generation": {"endpoint": "/image_generation"},
                        },
                    },
                },
            )
        ]
    )
    captured: dict[str, object] = {}

    def fake_json_request(*args, **kwargs):
        captured["url"] = args[0]
        captured["payload"] = kwargs["payload"]
        return {
            "data": {"image_urls": ["https://cdn.example.test/generated.png"]},
            "base_resp": {"status_code": 0, "status_msg": "success"},
        }

    def fake_raw_request(*args, **kwargs):
        return b"minimax-image-bytes"

    monkeypatch.setattr("anvil.media_tools.service._json_request", fake_json_request)
    monkeypatch.setattr("anvil.media_tools.service._raw_request", fake_raw_request)

    service = CapabilityAssemblyService()
    path_service = PathService(contract_tmp_path / "threads")
    sandbox_handle = create_sandbox_provider(config_result.effective_config).acquire(
        thread_id="thread-minimax-default-image-route",
        path_service=path_service,
    )
    lead_route = resolve_model_route(config_result.effective_config, ModelRouteRequest(subsystem="lead_agent"))

    result = service.assemble(
        sandbox_handle=sandbox_handle,
        config_result=config_result,
        feature_set=__import__("anvil.agents.features", fromlist=["RuntimeFeatureSet"]).RuntimeFeatureSet(),
        resolved_route=lead_route,
    )
    handlers = {entry.name: entry.handler for entry in result.bundle.visible_tools}

    payload = json.loads(
        handlers["image_generate"].invoke(
            {
                "prompt": "A clean landscape photograph",
                "output_path": "/mnt/user-data/outputs/images/minimax-default.png",
            }
        )
    )

    assert payload["success"] is True
    assert payload["provider"] == "minimax"
    assert captured["url"] == "https://api.minimaxi.com/v1/image_generation"
    request_payload = captured["payload"]
    assert isinstance(request_payload, dict)
    assert request_payload["model"] == "image-01"


def test_media_tools_scrub_provider_errors(monkeypatch, contract_tmp_path) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-testsecretsecretsecret")
    config_result = _config({"tts": {"providers": ["openai"]}})

    def fake_binary_request(*args, **kwargs):
        raise RuntimeError("upstream rejected sk-testsecretsecretsecret")

    monkeypatch.setattr("anvil.media_tools.service._binary_request", fake_binary_request)
    payload = MediaToolsService().text_to_speech(
        config_result=config_result,
        text="hello",
        output_path=contract_tmp_path / "out.mp3",
        output_virtual_path="/mnt/user-data/outputs/out.mp3",
    )

    assert payload["success"] is False
    assert payload["errors"][0]["provider"] == "openai"
    assert "[REDACTED]" in payload["errors"][0]["error"]
    assert "sk-testsecret" not in payload["errors"][0]["error"]


def test_speech_to_text_rejects_oversized_or_unsupported_audio(contract_tmp_path) -> None:
    config_result = _config({"stt": {"providers": ["mock"], "max_file_bytes": 4}})
    large_audio = contract_tmp_path / "large.wav"
    large_audio.write_bytes(b"12345")
    unsupported = contract_tmp_path / "notes.txt"
    unsupported.write_text("not audio", encoding="utf-8")
    service = MediaToolsService()

    large_payload = service.speech_to_text(
        config_result=config_result,
        input_path=large_audio,
        input_virtual_path="/mnt/user-data/uploads/large.wav",
    )
    unsupported_payload = service.speech_to_text(
        config_result=config_result,
        input_path=unsupported,
        input_virtual_path="/mnt/user-data/uploads/notes.txt",
    )

    assert large_payload["success"] is False
    assert "exceeds" in large_payload["error"]
    assert unsupported_payload["success"] is False
    assert "unsupported audio format" in unsupported_payload["error"]
