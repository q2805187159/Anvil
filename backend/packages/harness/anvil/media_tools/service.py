from __future__ import annotations

import asyncio
import base64
import json
import mimetypes
import os
from pathlib import Path
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any
from uuid import uuid4

from anvil.config import ConfigResolutionResult


DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"
DEFAULT_OPENAI_TTS_MODEL = "gpt-4o-mini-tts"
DEFAULT_OPENAI_TTS_VOICE = "alloy"
DEFAULT_OPENAI_STT_MODEL = "gpt-4o-mini-transcribe"
DEFAULT_GROQ_BASE_URL = "https://api.groq.com/openai/v1"
DEFAULT_GROQ_STT_MODEL = "whisper-large-v3-turbo"
DEFAULT_MISTRAL_BASE_URL = "https://api.mistral.ai/v1"
DEFAULT_MISTRAL_STT_MODEL = "voxtral-mini-latest"
DEFAULT_MINIMAX_TTS_MODEL = "speech-2.8-hd"
DEFAULT_MINIMAX_TTS_VOICE = "English_Graceful_Lady"
DEFAULT_MINIMAX_BASE_URL = "https://api.minimax.io/v1/t2a_v2"
DEFAULT_MINIMAX_IMAGE_MODEL = "image-01"
DEFAULT_EDGE_TTS_VOICE = "en-US-AriaNeural"
DEFAULT_OPENAI_IMAGE_MODEL = "gpt-image-1"
DEFAULT_TTS_MAX_CHARS = 4096
DEFAULT_STT_MAX_BYTES = 25 * 1024 * 1024
DEFAULT_IMAGE_PROMPT_MAX_CHARS = 4000
SUPPORTED_TTS_FORMATS = {"mp3", "opus", "aac", "flac", "wav", "pcm"}
TTS_EXTENSION_BY_FORMAT = {"opus": "ogg", "pcm": "pcm", "mp3": "mp3", "aac": "aac", "flac": "flac", "wav": "wav"}
SUPPORTED_STT_FORMATS = {".mp3", ".mp4", ".mpeg", ".mpga", ".m4a", ".wav", ".webm", ".ogg", ".aac", ".flac"}
OPENAI_STT_FORMATS = {".flac", ".mp3", ".mp4", ".mpeg", ".mpga", ".m4a", ".ogg", ".wav", ".webm"}
SUPPORTED_STT_RESPONSE_FORMATS = {"json", "text", "verbose_json", "srt", "vtt", "diarized_json"}
SUPPORTED_IMAGE_FORMATS = {"png", "jpeg", "webp"}
IMAGE_EXTENSION_BY_FORMAT = {"png": "png", "jpeg": "jpg", "webp": "webp"}
MOCK_PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+/p9sAAAAASUVORK5CYII="
)
SECRET_VALUE_RE = re.compile(
    r"(sk-[A-Za-z0-9_-]{12,}|gh[pousr]_[A-Za-z0-9_]{20,}|[A-Za-z0-9_-]{24,}\.[A-Za-z0-9_-]{12,}\.[A-Za-z0-9_-]{12,})"
)


class MediaToolsService:
    """Provider-adapted media tools with virtual-path-safe JSON contracts."""

    def default_tts_virtual_path(self, *, response_format: str = "mp3") -> str:
        normalized_format = _normalize_tts_format(response_format)
        extension = TTS_EXTENSION_BY_FORMAT[normalized_format]
        return f"/mnt/user-data/outputs/audio/tts_{uuid4().hex[:12]}.{extension}"

    def default_image_virtual_path(self, *, response_format: str = "png") -> str:
        normalized_format = _normalize_image_format(response_format)
        extension = IMAGE_EXTENSION_BY_FORMAT[normalized_format]
        return f"/mnt/user-data/outputs/images/image_{uuid4().hex[:12]}.{extension}"

    def text_to_speech(
        self,
        *,
        config_result: ConfigResolutionResult,
        text: str,
        output_path: Path,
        output_virtual_path: str,
        provider: str | None = None,
        voice: str | None = None,
        model: str | None = None,
        response_format: str = "mp3",
        speed: float = 1.0,
        instructions: str | None = None,
    ) -> dict[str, Any]:
        settings = _media_tools_settings(config_result)
        normalized_text = str(text or "").strip()
        if not normalized_text:
            return {"success": False, "error": "text is required"}
        max_chars = _bounded_int(_section(settings, "tts").get("max_text_chars") or settings.get("max_tts_chars"), default=DEFAULT_TTS_MAX_CHARS, minimum=1, maximum=10000)
        if len(normalized_text) > max_chars:
            return {
                "success": False,
                "error": f"text exceeds configured TTS limit of {max_chars} characters",
                "text_chars": len(normalized_text),
            }
        normalized_format = _normalize_tts_format(response_format, output_virtual_path=output_virtual_path)
        normalized_speed = _bounded_float(speed, default=1.0, minimum=0.25, maximum=4.0)
        provider_order = _provider_order(settings, "tts", explicit=provider)
        errors: list[dict[str, str]] = []
        for provider_name in provider_order:
            try:
                if provider_name == "mock":
                    audio = _mock_tts_bytes(settings=settings, text=normalized_text)
                    _write_audio(output_path, audio)
                    return _tts_success_payload(
                        provider=provider_name,
                        output_virtual_path=output_virtual_path,
                        output_path=output_path,
                        response_format=normalized_format,
                        model=model or "mock",
                        voice=voice or "mock",
                        text_chars=len(normalized_text),
                    )
                if provider_name == "openai":
                    audio = self._openai_tts(
                        settings=settings,
                        text=normalized_text,
                        model=model,
                        voice=voice,
                        response_format=normalized_format,
                        speed=normalized_speed,
                        instructions=instructions,
                    )
                    _write_audio(output_path, audio)
                    return _tts_success_payload(
                        provider=provider_name,
                        output_virtual_path=output_virtual_path,
                        output_path=output_path,
                        response_format=normalized_format,
                        model=model or str(_section(settings, "openai").get("model") or _section(settings, "tts").get("model") or DEFAULT_OPENAI_TTS_MODEL),
                        voice=voice or str(_section(settings, "openai").get("voice") or _section(settings, "tts").get("voice") or DEFAULT_OPENAI_TTS_VOICE),
                        text_chars=len(normalized_text),
                    )
                if provider_name == "minimax":
                    audio = self._minimax_tts(
                        settings=settings,
                        text=normalized_text,
                        model=model,
                        voice=voice,
                        response_format=normalized_format,
                    )
                    _write_audio(output_path, audio)
                    return _tts_success_payload(
                        provider=provider_name,
                        output_virtual_path=output_virtual_path,
                        output_path=output_path,
                        response_format=normalized_format,
                        model=model or str(_section(settings, "minimax").get("model") or DEFAULT_MINIMAX_TTS_MODEL),
                        voice=voice or str(_section(settings, "minimax").get("voice_id") or _section(settings, "minimax").get("voice") or DEFAULT_MINIMAX_TTS_VOICE),
                        text_chars=len(normalized_text),
                    )
                if provider_name == "edge":
                    if normalized_format != "mp3":
                        raise ValueError("edge provider supports mp3 output only")
                    self._edge_tts(
                        settings=settings,
                        text=normalized_text,
                        output_path=output_path,
                        voice=voice,
                    )
                    return _tts_success_payload(
                        provider=provider_name,
                        output_virtual_path=output_virtual_path,
                        output_path=output_path,
                        response_format=normalized_format,
                        model="edge-tts",
                        voice=voice or str(_section(settings, "edge").get("voice") or DEFAULT_EDGE_TTS_VOICE),
                        text_chars=len(normalized_text),
                    )
            except Exception as exc:
                errors.append(_error_payload(provider_name, exc))
        return {
            "success": False,
            "provider": "unavailable",
            "output_path": output_virtual_path,
            "text_chars": len(normalized_text),
            "errors": errors,
        }

    def speech_to_text(
        self,
        *,
        config_result: ConfigResolutionResult,
        input_path: Path,
        input_virtual_path: str,
        provider: str | None = None,
        model: str | None = None,
        language: str | None = None,
        prompt: str | None = None,
        response_format: str = "json",
    ) -> dict[str, Any]:
        settings = _media_tools_settings(config_result)
        if not input_path.exists() or not input_path.is_file():
            return {"success": False, "error": "input_path does not exist or is not a file", "input_path": input_virtual_path}
        suffix = input_path.suffix.lower()
        if suffix not in SUPPORTED_STT_FORMATS:
            return {
                "success": False,
                "error": f"unsupported audio format '{suffix or '(none)'}'",
                "supported_formats": sorted(SUPPORTED_STT_FORMATS),
                "input_path": input_virtual_path,
            }
        max_bytes = _bounded_int(_section(settings, "stt").get("max_file_bytes") or settings.get("max_audio_bytes"), default=DEFAULT_STT_MAX_BYTES, minimum=1, maximum=200 * 1024 * 1024)
        file_size = input_path.stat().st_size
        if file_size > max_bytes:
            return {
                "success": False,
                "error": f"audio file exceeds configured STT limit of {max_bytes} bytes",
                "file_size": file_size,
                "input_path": input_virtual_path,
            }
        normalized_response_format = str(response_format or "json").strip().lower()
        if normalized_response_format not in SUPPORTED_STT_RESPONSE_FORMATS:
            return {
                "success": False,
                "error": f"unsupported response_format '{response_format}'",
                "supported_response_formats": sorted(SUPPORTED_STT_RESPONSE_FORMATS),
            }
        provider_order = _provider_order(settings, "stt", explicit=provider)
        errors: list[dict[str, str]] = []
        for provider_name in provider_order:
            try:
                if provider_name == "mock":
                    return _mock_stt_payload(
                        settings=settings,
                        input_virtual_path=input_virtual_path,
                        input_path=input_path,
                        model=model or "mock",
                        language=language,
                    )
                if provider_name == "openai":
                    if suffix not in OPENAI_STT_FORMATS:
                        raise ValueError(f"OpenAI transcription does not support {suffix} inputs")
                    payload = self._openai_compatible_stt(
                        settings=settings,
                        provider_name=provider_name,
                        input_path=input_path,
                        model=model,
                        language=language,
                        prompt=prompt,
                        response_format=normalized_response_format,
                    )
                    return _stt_success_payload(
                        provider=provider_name,
                        input_virtual_path=input_virtual_path,
                        file_size=file_size,
                        payload=payload,
                        response_format=normalized_response_format,
                    )
                if provider_name == "groq":
                    payload = self._openai_compatible_stt(
                        settings=settings,
                        provider_name=provider_name,
                        input_path=input_path,
                        model=model,
                        language=language,
                        prompt=prompt,
                        response_format=normalized_response_format,
                    )
                    return _stt_success_payload(
                        provider=provider_name,
                        input_virtual_path=input_virtual_path,
                        file_size=file_size,
                        payload=payload,
                        response_format=normalized_response_format,
                    )
                if provider_name == "mistral":
                    payload = self._openai_compatible_stt(
                        settings=settings,
                        provider_name=provider_name,
                        input_path=input_path,
                        model=model,
                        language=language,
                        prompt=prompt,
                        response_format=normalized_response_format,
                    )
                    return _stt_success_payload(
                        provider=provider_name,
                        input_virtual_path=input_virtual_path,
                        file_size=file_size,
                        payload=payload,
                        response_format=normalized_response_format,
                    )
            except Exception as exc:
                errors.append(_error_payload(provider_name, exc))
        return {
            "success": False,
            "provider": "unavailable",
            "input_path": input_virtual_path,
            "file_size": file_size,
            "errors": errors,
        }

    def image_generate(
        self,
        *,
        config_result: ConfigResolutionResult,
        prompt: str,
        output_path: Path,
        output_virtual_path: str,
        provider: str | None = None,
        model: str | None = None,
        response_format: str = "png",
        size: str | None = None,
        quality: str | None = None,
        background: str | None = None,
        n: int = 1,
        model_image_generation: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        settings = _image_generation_settings(config_result, model_image_generation=model_image_generation)
        normalized_prompt = str(prompt or "").strip()
        if not normalized_prompt:
            return {"success": False, "error": "prompt is required"}
        max_chars = _bounded_int(
            _section(settings, "image_generation").get("max_prompt_chars") or settings.get("max_prompt_chars"),
            default=DEFAULT_IMAGE_PROMPT_MAX_CHARS,
            minimum=1,
            maximum=20000,
        )
        if len(normalized_prompt) > max_chars:
            return {
                "success": False,
                "error": f"prompt exceeds configured image generation limit of {max_chars} characters",
                "prompt_chars": len(normalized_prompt),
            }
        normalized_format = _normalize_image_format(response_format, output_virtual_path=output_virtual_path)
        normalized_size = str(size or _section(settings, "image_generation").get("size") or settings.get("size") or "1024x1024").strip()
        normalized_n = _bounded_int(n, default=1, minimum=1, maximum=4)
        provider_order = _provider_order(settings, "image_generation", explicit=provider)
        errors: list[dict[str, str]] = []
        for provider_name in provider_order:
            try:
                if provider_name == "mock":
                    image = _mock_image_bytes(settings=settings)
                    _write_media(output_path, image, kind="image")
                    return _image_success_payload(
                        provider=provider_name,
                        output_virtual_path=output_virtual_path,
                        output_path=output_path,
                        response_format=normalized_format,
                        model=model or str(_section(settings, "image_generation").get("model") or settings.get("model") or "mock"),
                        prompt_chars=len(normalized_prompt),
                        size=normalized_size,
                        n=normalized_n,
                    )
                if provider_name == "openai":
                    image = self._openai_image_generation(
                        settings=settings,
                        prompt=normalized_prompt,
                        model=model,
                        response_format=normalized_format,
                        size=normalized_size,
                        quality=quality,
                        background=background,
                        n=normalized_n,
                    )
                    _write_media(output_path, image, kind="image")
                    return _image_success_payload(
                        provider=provider_name,
                        output_virtual_path=output_virtual_path,
                        output_path=output_path,
                        response_format=normalized_format,
                        model=model or str(_section(settings, "openai").get("model") or _section(settings, "image_generation").get("model") or settings.get("model") or DEFAULT_OPENAI_IMAGE_MODEL),
                        prompt_chars=len(normalized_prompt),
                        size=normalized_size,
                        n=normalized_n,
                    )
                if provider_name == "minimax":
                    image = self._minimax_image_generation(
                        settings=settings,
                        prompt=normalized_prompt,
                        model=model,
                        size=normalized_size,
                        n=normalized_n,
                    )
                    _write_media(output_path, image, kind="image")
                    return _image_success_payload(
                        provider=provider_name,
                        output_virtual_path=output_virtual_path,
                        output_path=output_path,
                        response_format=normalized_format,
                        model=model or str(_section(settings, "minimax").get("model") or _section(settings, "image_generation").get("model") or settings.get("model") or DEFAULT_MINIMAX_IMAGE_MODEL),
                        prompt_chars=len(normalized_prompt),
                        size=normalized_size,
                        n=normalized_n,
                    )
            except Exception as exc:
                errors.append(_error_payload(provider_name, exc))
        return {
            "success": False,
            "provider": "unavailable",
            "prompt_chars": len(normalized_prompt),
            "errors": errors,
        }

    def _openai_tts(
        self,
        *,
        settings: dict[str, Any],
        text: str,
        model: str | None,
        voice: str | None,
        response_format: str,
        speed: float,
        instructions: str | None,
    ) -> bytes:
        tts_settings = _section(settings, "tts")
        openai_settings = _section(settings, "openai")
        api_key = _resolve_secret(
            openai_settings.get("api_key")
            or tts_settings.get("openai_api_key")
            or settings.get("openai_api_key")
            or "$VOICE_TOOLS_OPENAI_KEY"
        ) or _resolve_secret("$OPENAI_API_KEY")
        if not api_key:
            raise ValueError("OPENAI_API_KEY or VOICE_TOOLS_OPENAI_KEY is not configured")
        base_url = _openai_base_url(openai_settings, tts_settings, settings)
        payload: dict[str, Any] = {
            "model": model or openai_settings.get("model") or tts_settings.get("model") or DEFAULT_OPENAI_TTS_MODEL,
            "voice": voice or openai_settings.get("voice") or tts_settings.get("voice") or DEFAULT_OPENAI_TTS_VOICE,
            "input": text,
            "response_format": response_format,
            "speed": speed,
        }
        if instructions:
            payload["instructions"] = instructions
        return _binary_request(
            f"{base_url}/audio/speech",
            method="POST",
            payload=payload,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=_bounded_int(tts_settings.get("timeout_seconds") or settings.get("timeout_seconds"), default=60, minimum=1, maximum=180),
        )

    def _minimax_tts(
        self,
        *,
        settings: dict[str, Any],
        text: str,
        model: str | None,
        voice: str | None,
        response_format: str,
    ) -> bytes:
        minimax_settings = _section(settings, "minimax")
        api_key = _resolve_secret(minimax_settings.get("api_key") or settings.get("minimax_api_key") or "$MINIMAX_API_KEY")
        if not api_key:
            raise ValueError("MINIMAX_API_KEY is not configured")
        audio_format = "mp3" if response_format in {"opus", "aac", "pcm"} else response_format
        payload = {
            "model": model or minimax_settings.get("model") or DEFAULT_MINIMAX_TTS_MODEL,
            "text": text,
            "stream": False,
            "voice_setting": {
                "voice_id": voice or minimax_settings.get("voice_id") or minimax_settings.get("voice") or DEFAULT_MINIMAX_TTS_VOICE,
                "speed": _bounded_float(minimax_settings.get("speed"), default=1.0, minimum=0.5, maximum=2.0),
                "vol": _bounded_float(minimax_settings.get("vol"), default=1.0, minimum=0.1, maximum=10.0),
                "pitch": _bounded_int(minimax_settings.get("pitch"), default=0, minimum=-12, maximum=12),
            },
            "audio_setting": {
                "sample_rate": _bounded_int(minimax_settings.get("sample_rate"), default=32000, minimum=8000, maximum=48000),
                "bitrate": _bounded_int(minimax_settings.get("bitrate"), default=128000, minimum=32000, maximum=320000),
                "format": audio_format,
                "channel": _bounded_int(minimax_settings.get("channel"), default=1, minimum=1, maximum=2),
            },
        }
        base_url = str(minimax_settings.get("base_url") or settings.get("minimax_base_url") or DEFAULT_MINIMAX_BASE_URL).strip()
        response = _json_request(
            base_url,
            method="POST",
            payload=payload,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=_bounded_int(minimax_settings.get("timeout_seconds") or settings.get("timeout_seconds"), default=60, minimum=1, maximum=180),
        )
        base_response = response.get("base_resp") if isinstance(response.get("base_resp"), dict) else {}
        status_code = base_response.get("status_code", 0)
        if status_code not in {0, "0", None}:
            raise RuntimeError(f"MiniMax TTS API error: {base_response.get('status_msg') or status_code}")
        hex_audio = ((response.get("data") or {}) if isinstance(response.get("data"), dict) else {}).get("audio")
        if not isinstance(hex_audio, str) or not hex_audio:
            raise RuntimeError("MiniMax TTS returned empty audio data")
        return bytes.fromhex(hex_audio)

    def _edge_tts(self, *, settings: dict[str, Any], text: str, output_path: Path, voice: str | None) -> None:
        try:
            import edge_tts  # type: ignore[import-not-found]
        except ImportError as exc:
            raise RuntimeError("edge provider requires the optional edge-tts package") from exc
        edge_settings = _section(settings, "edge")
        selected_voice = voice or edge_settings.get("voice") or DEFAULT_EDGE_TTS_VOICE

        async def _save() -> None:
            communicate = edge_tts.Communicate(text, str(selected_voice))
            await communicate.save(str(output_path))

        output_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            asyncio.run(_save())
        except RuntimeError as exc:
            raise RuntimeError(f"edge TTS failed: {exc}") from exc

    def _openai_compatible_stt(
        self,
        *,
        settings: dict[str, Any],
        provider_name: str,
        input_path: Path,
        model: str | None,
        language: str | None,
        prompt: str | None,
        response_format: str,
    ) -> dict[str, Any] | str:
        provider_settings = _section(settings, provider_name)
        stt_settings = _section(settings, "stt")
        if provider_name == "openai":
            api_key = _resolve_secret(
                provider_settings.get("api_key")
                or stt_settings.get("openai_api_key")
                or settings.get("openai_api_key")
                or "$VOICE_TOOLS_OPENAI_KEY"
            ) or _resolve_secret("$OPENAI_API_KEY")
            base_url = _openai_base_url(provider_settings, stt_settings, settings)
            default_model = DEFAULT_OPENAI_STT_MODEL
        elif provider_name == "groq":
            api_key = _resolve_secret(provider_settings.get("api_key") or stt_settings.get("groq_api_key") or settings.get("groq_api_key") or "$GROQ_API_KEY")
            base_url = str(provider_settings.get("base_url") or settings.get("groq_base_url") or os.getenv("GROQ_BASE_URL") or DEFAULT_GROQ_BASE_URL).rstrip("/")
            default_model = DEFAULT_GROQ_STT_MODEL
        else:
            api_key = _resolve_secret(provider_settings.get("api_key") or stt_settings.get("mistral_api_key") or settings.get("mistral_api_key") or "$MISTRAL_API_KEY")
            base_url = str(provider_settings.get("base_url") or settings.get("mistral_base_url") or os.getenv("MISTRAL_BASE_URL") or DEFAULT_MISTRAL_BASE_URL).rstrip("/")
            default_model = DEFAULT_MISTRAL_STT_MODEL
        if not api_key:
            raise ValueError(f"{provider_name.upper()} transcription API key is not configured")
        fields: dict[str, str] = {
            "model": str(model or provider_settings.get("model") or stt_settings.get("model") or default_model),
            "response_format": response_format,
        }
        if language:
            fields["language"] = language
        if prompt:
            fields["prompt"] = prompt
        body, content_type = _multipart_body(fields=fields, file_path=input_path, file_field="file")
        response_body = _raw_request(
            f"{base_url}/audio/transcriptions",
            method="POST",
            data=body,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": content_type,
            },
            timeout=_bounded_int(stt_settings.get("timeout_seconds") or settings.get("timeout_seconds"), default=120, minimum=1, maximum=300),
        )
        if response_format == "text":
            return response_body.decode("utf-8", errors="replace").strip()
        parsed = json.loads(response_body.decode("utf-8", errors="replace") or "{}")
        return parsed if isinstance(parsed, dict) else {"data": parsed}

    def _openai_image_generation(
        self,
        *,
        settings: dict[str, Any],
        prompt: str,
        model: str | None,
        response_format: str,
        size: str,
        quality: str | None,
        background: str | None,
        n: int,
    ) -> bytes:
        image_settings = _section(settings, "image_generation")
        openai_settings = _section(settings, "openai")
        api_key = _resolve_secret(
            openai_settings.get("api_key")
            or image_settings.get("api_key")
            or _env_reference(image_settings.get("api_key_env"))
            or settings.get("api_key")
            or _env_reference(settings.get("api_key_env"))
            or "$OPENAI_API_KEY"
        )
        if not api_key:
            raise ValueError("OPENAI_API_KEY is not configured")
        base_url = _openai_base_url(openai_settings, image_settings, settings)
        endpoint = _image_generation_endpoint(image_settings, settings)
        payload: dict[str, Any] = {
            "model": model or openai_settings.get("model") or image_settings.get("model") or settings.get("model") or DEFAULT_OPENAI_IMAGE_MODEL,
            "prompt": prompt,
            "size": size,
            "n": n,
        }
        configured_response_format = image_settings.get("response_format") or settings.get("response_format")
        if configured_response_format:
            payload["response_format"] = configured_response_format
        if response_format:
            payload["output_format"] = response_format
        if quality:
            payload["quality"] = quality
        elif image_settings.get("quality"):
            payload["quality"] = image_settings.get("quality")
        if background:
            payload["background"] = background
        elif image_settings.get("background"):
            payload["background"] = image_settings.get("background")
        response = _json_request(
            f"{base_url}{endpoint}",
            method="POST",
            payload=payload,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=_bounded_int(image_settings.get("timeout_seconds") or image_settings.get("timeout") or settings.get("timeout_seconds"), default=120, minimum=1, maximum=300),
        )
        return _image_bytes_from_openai_response(response=response, timeout=_bounded_int(image_settings.get("download_timeout_seconds"), default=60, minimum=1, maximum=180))

    def _minimax_image_generation(
        self,
        *,
        settings: dict[str, Any],
        prompt: str,
        model: str | None,
        size: str,
        n: int,
    ) -> bytes:
        image_settings = _section(settings, "image_generation")
        minimax_settings = _section(settings, "minimax")
        api_key = _resolve_secret(
            minimax_settings.get("api_key")
            or image_settings.get("api_key")
            or _env_reference(image_settings.get("api_key_env"))
            or settings.get("api_key")
            or _env_reference(settings.get("api_key_env"))
            or settings.get("minimax_api_key")
            or "$MINIMAX_API_KEY"
        )
        if not api_key:
            raise ValueError("MINIMAX_API_KEY is not configured")
        base_url = _minimax_image_base_url(minimax_settings, image_settings, settings)
        endpoint = _image_generation_endpoint(image_settings, settings)
        aspect_ratio = str(image_settings.get("aspect_ratio") or settings.get("aspect_ratio") or "").strip()
        payload: dict[str, Any] = {
            "model": str(model or minimax_settings.get("model") or image_settings.get("model") or settings.get("model") or DEFAULT_MINIMAX_IMAGE_MODEL),
            "prompt": prompt,
            "response_format": _minimax_response_format(image_settings.get("response_format") or settings.get("response_format")),
            "n": _bounded_int(image_settings.get("n") or n, default=n, minimum=1, maximum=9),
        }
        if aspect_ratio:
            payload["aspect_ratio"] = aspect_ratio
        else:
            ratio = _minimax_aspect_ratio_for_size(size)
            if ratio:
                payload["aspect_ratio"] = ratio
            else:
                dimensions = _image_dimensions(size)
                if dimensions is not None:
                    payload["width"], payload["height"] = dimensions
        if "prompt_optimizer" in image_settings or "prompt_optimizer" in settings:
            payload["prompt_optimizer"] = bool(image_settings.get("prompt_optimizer", settings.get("prompt_optimizer")))
        if "aigc_watermark" in image_settings or "aigc_watermark" in settings:
            payload["aigc_watermark"] = bool(image_settings.get("aigc_watermark", settings.get("aigc_watermark")))
        style = image_settings.get("style") or settings.get("style")
        if isinstance(style, dict) and style:
            payload["style"] = style
        response = _json_request(
            f"{base_url}{endpoint}",
            method="POST",
            payload=payload,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=_bounded_int(image_settings.get("timeout_seconds") or image_settings.get("timeout") or settings.get("timeout_seconds"), default=120, minimum=1, maximum=300),
        )
        return _image_bytes_from_minimax_response(
            response=response,
            timeout=_bounded_int(image_settings.get("download_timeout_seconds"), default=60, minimum=1, maximum=180),
        )


def _media_tools_settings(config_result: ConfigResolutionResult) -> dict[str, Any]:
    additional = config_result.effective_config.additional_settings
    raw = additional.get("media_tools") or additional.get("audio_tools") or {}
    settings = dict(raw) if isinstance(raw, dict) else {}
    for key in ("tts", "stt", "image_generation"):
        section = additional.get(key)
        if isinstance(section, dict) and key not in settings:
            settings[key] = section
    return settings


def _image_generation_settings(
    config_result: ConfigResolutionResult,
    *,
    model_image_generation: dict[str, Any] | None,
) -> dict[str, Any]:
    settings = _media_tools_settings(config_result)
    configured = _section(settings, "image_generation")
    if model_image_generation:
        merged = {**configured, **dict(model_image_generation)}
        settings = {**settings, "image_generation": merged}
        for key in ("provider", "providers", "model", "base_url", "api_key", "api_key_env", "endpoint", "size", "quality", "output_format", "response_format", "timeout", "timeout_seconds"):
            if key in model_image_generation:
                settings[key] = model_image_generation[key]
    return settings


def _section(settings: dict[str, Any], name: str) -> dict[str, Any]:
    value = settings.get(name)
    return dict(value) if isinstance(value, dict) else {}


def _provider_order(settings: dict[str, Any], operation: str, *, explicit: str | None) -> list[str]:
    if explicit:
        return [_normalize_provider(explicit)]
    section = _section(settings, operation)
    raw = section.get("providers") or settings.get(f"{operation}_providers")
    if raw is None:
        raw = section.get("provider") or section.get("kind") or settings.get(f"{operation}_provider") or settings.get("provider")
    if raw is None:
        if operation == "tts":
            raw = ["openai", "minimax", "edge"]
        elif operation == "image_generation":
            raw = ["openai"]
        else:
            raw = ["openai", "groq", "mistral"]
    if isinstance(raw, str):
        raw = [raw]
    return [_normalize_provider(item) for item in raw if str(item).strip()]


def _normalize_provider(value: Any) -> str:
    aliases = {
        "openai_compatible": "openai",
        "openai_responses": "openai",
        "oai": "openai",
        "mini_max": "minimax",
        "edge_tts": "edge",
        "groq_whisper": "groq",
        "voxtral": "mistral",
    }
    normalized = str(value).strip().lower().replace("-", "_")
    return aliases.get(normalized, normalized)


def _normalize_tts_format(value: str | None, *, output_virtual_path: str | None = None) -> str:
    candidate = str(value or "").strip().lower().lstrip(".")
    if not candidate and output_virtual_path:
        candidate = Path(output_virtual_path).suffix.lower().lstrip(".")
    if candidate in {"ogg", "oga"}:
        candidate = "opus"
    if candidate not in SUPPORTED_TTS_FORMATS:
        candidate = "mp3"
    return candidate


def _normalize_image_format(value: str | None, *, output_virtual_path: str | None = None) -> str:
    candidate = str(value or "").strip().lower().lstrip(".")
    if not candidate and output_virtual_path:
        candidate = Path(output_virtual_path).suffix.lower().lstrip(".")
    if candidate in {"jpg", "jpe"}:
        candidate = "jpeg"
    if candidate not in SUPPORTED_IMAGE_FORMATS:
        candidate = "png"
    return candidate


def _openai_base_url(*sections: dict[str, Any]) -> str:
    for section in sections:
        value = section.get("base_url") or section.get("openai_base_url")
        if isinstance(value, str) and value.strip():
            resolved = _resolve_secret(value).rstrip("/")
            if resolved:
                return resolved
    return (os.getenv("OPENAI_AUDIO_BASE_URL") or os.getenv("STT_OPENAI_BASE_URL") or os.getenv("OPENAI_BASE_URL") or DEFAULT_OPENAI_BASE_URL).rstrip("/")


def _image_generation_endpoint(*sections: dict[str, Any]) -> str:
    for section in sections:
        value = section.get("endpoint") or section.get("path") or section.get("image_generation_path")
        if isinstance(value, str) and value.strip():
            endpoint = value.strip()
            return endpoint if endpoint.startswith("/") else f"/{endpoint}"
    raise ValueError("image_generation.endpoint is required when image generation is enabled")


def _mock_tts_bytes(*, settings: dict[str, Any], text: str) -> bytes:
    tts_settings = _section(settings, "tts")
    configured = tts_settings.get("mock_audio_bytes") or settings.get("mock_tts_audio_bytes")
    if isinstance(configured, str) and configured:
        return configured.encode("utf-8")
    return f"ANVIL_MOCK_AUDIO\n{text[:200]}".encode("utf-8")


def _mock_image_bytes(*, settings: dict[str, Any]) -> bytes:
    image_settings = _section(settings, "image_generation")
    configured = image_settings.get("mock_image_bytes") or settings.get("mock_image_bytes")
    if isinstance(configured, bytes):
        return configured
    if isinstance(configured, str) and configured:
        return configured.encode("utf-8")
    return MOCK_PNG_BYTES


def _mock_stt_payload(
    *,
    settings: dict[str, Any],
    input_virtual_path: str,
    input_path: Path,
    model: str,
    language: str | None,
) -> dict[str, Any]:
    stt_settings = _section(settings, "stt")
    transcripts = stt_settings.get("mock_transcripts") or settings.get("mock_transcripts") or {}
    transcript = ""
    if isinstance(transcripts, dict):
        transcript = str(transcripts.get(input_virtual_path) or transcripts.get(input_path.name) or transcripts.get("*") or "")
    if not transcript:
        transcript = str(stt_settings.get("mock_transcript") or settings.get("mock_transcript") or "")
    return {
        "success": True,
        "provider": "mock",
        "input_path": input_virtual_path,
        "file_size": input_path.stat().st_size,
        "model": model,
        "language": language,
        "transcript": transcript,
        "raw": {"text": transcript},
    }


def _tts_success_payload(
    *,
    provider: str,
    output_virtual_path: str,
    output_path: Path,
    response_format: str,
    model: str,
    voice: str,
    text_chars: int,
) -> dict[str, Any]:
    return {
        "success": True,
        "provider": provider,
        "output_path": output_virtual_path,
        "format": response_format,
        "bytes": output_path.stat().st_size,
        "model": model,
        "voice": voice,
        "text_chars": text_chars,
    }


def _stt_success_payload(
    *,
    provider: str,
    input_virtual_path: str,
    file_size: int,
    payload: dict[str, Any] | str,
    response_format: str,
) -> dict[str, Any]:
    transcript = payload.strip() if isinstance(payload, str) else str(payload.get("text") or "")
    return {
        "success": True,
        "provider": provider,
        "input_path": input_virtual_path,
        "file_size": file_size,
        "response_format": response_format,
        "transcript": _scrub_text(transcript),
        "raw": _scrub_data({"text": payload} if isinstance(payload, str) else payload),
    }


def _image_success_payload(
    *,
    provider: str,
    output_virtual_path: str,
    output_path: Path,
    response_format: str,
    model: str,
    prompt_chars: int,
    size: str,
    n: int,
) -> dict[str, Any]:
    return {
        "success": True,
        "provider": provider,
        "output_path": output_virtual_path,
        "format": response_format,
        "bytes": output_path.stat().st_size,
        "model": model,
        "prompt_chars": prompt_chars,
        "size": size,
        "n": n,
    }


def _write_audio(output_path: Path, audio: bytes) -> None:
    _write_media(output_path, audio, kind="audio")


def _write_media(output_path: Path, content: bytes, *, kind: str) -> None:
    if not content:
        raise ValueError(f"provider returned empty {kind}")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(content)


def _image_bytes_from_openai_response(*, response: dict[str, Any], timeout: int) -> bytes:
    data = response.get("data")
    if not isinstance(data, list) or not data:
        raise RuntimeError("image generation returned no data")
    item = data[0]
    if not isinstance(item, dict):
        raise RuntimeError("image generation returned malformed data")
    b64_json = item.get("b64_json")
    if isinstance(b64_json, str) and b64_json.strip():
        return base64.b64decode(b64_json)
    image_url = item.get("url")
    if isinstance(image_url, str) and image_url.strip():
        return _raw_request(
            image_url,
            method="GET",
            data=None,
            headers={"Accept": "image/*"},
            timeout=timeout,
        )
    raise RuntimeError("image generation returned neither b64_json nor url")


def _image_bytes_from_minimax_response(*, response: dict[str, Any], timeout: int) -> bytes:
    base_response = response.get("base_resp") if isinstance(response.get("base_resp"), dict) else {}
    status_code = base_response.get("status_code", 0)
    if status_code not in {0, "0", None}:
        raise RuntimeError(f"MiniMax image generation API error: {base_response.get('status_msg') or status_code}")
    data = response.get("data")
    if not isinstance(data, dict):
        raise RuntimeError("MiniMax image generation returned no data")
    encoded_images = data.get("image_base64")
    if isinstance(encoded_images, list):
        for encoded in encoded_images:
            if isinstance(encoded, str) and encoded.strip():
                return base64.b64decode(_strip_data_url_prefix(encoded.strip()))
    image_urls = data.get("image_urls")
    if isinstance(image_urls, list):
        for image_url in image_urls:
            if isinstance(image_url, str) and image_url.strip():
                return _raw_request(
                    image_url.strip(),
                    method="GET",
                    data=None,
                    headers={"Accept": "image/*"},
                    timeout=timeout,
                )
    raise RuntimeError("MiniMax image generation returned neither image_base64 nor image_urls")


def _strip_data_url_prefix(value: str) -> str:
    if value.startswith("data:") and "," in value:
        return value.split(",", 1)[1]
    return value


def _minimax_response_format(value: Any) -> str:
    normalized = str(value or "").strip().lower()
    if normalized in {"base64", "url"}:
        return normalized
    return "url"


def _minimax_image_base_url(*sections: dict[str, Any]) -> str:
    for section in sections:
        value = section.get("base_url") or section.get("minimax_base_url")
        if isinstance(value, str) and value.strip():
            resolved = _resolve_secret(value).rstrip("/")
            if not resolved:
                continue
            lowered = resolved.lower()
            for suffix in ("/image_generation", "/anthropic"):
                if lowered.endswith(suffix):
                    return resolved[: -len(suffix)].rstrip("/")
            return resolved
    env_base_url = os.getenv("MINIMAX_IMAGE_BASE_URL")
    if env_base_url and env_base_url.strip():
        return env_base_url.strip().rstrip("/")
    raise ValueError("image_generation.base_url is required for MiniMax image generation")


def _minimax_aspect_ratio_for_size(size: str | None) -> str | None:
    dimensions = _image_dimensions(size)
    if dimensions is None:
        return None
    mapping = {
        (1024, 1024): "1:1",
        (1280, 720): "16:9",
        (1152, 864): "4:3",
        (1248, 832): "3:2",
        (832, 1248): "2:3",
        (864, 1152): "3:4",
        (720, 1280): "9:16",
        (1344, 576): "21:9",
    }
    return mapping.get(dimensions)


def _image_dimensions(size: str | None) -> tuple[int, int] | None:
    if not isinstance(size, str):
        return None
    match = re.fullmatch(r"\s*(\d{3,4})x(\d{3,4})\s*", size.lower())
    if match is None:
        return None
    width = int(match.group(1))
    height = int(match.group(2))
    if not (512 <= width <= 2048 and 512 <= height <= 2048):
        return None
    if width % 8 != 0 or height % 8 != 0:
        return None
    return width, height


def _binary_request(
    url: str,
    *,
    method: str = "POST",
    payload: dict[str, Any],
    headers: dict[str, str] | None = None,
    timeout: int = 60,
) -> bytes:
    data = json.dumps(payload).encode("utf-8")
    return _raw_request(
        url,
        method=method,
        data=data,
        headers={"Accept": "application/octet-stream", "Content-Type": "application/json", **(headers or {})},
        timeout=timeout,
    )


def _json_request(
    url: str,
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: int = 60,
) -> dict[str, Any]:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    response = _raw_request(
        url,
        method=method,
        data=data,
        headers={"Accept": "application/json", "Content-Type": "application/json", **(headers or {})},
        timeout=timeout,
    )
    parsed = json.loads(response.decode("utf-8", errors="replace") or "{}")
    return parsed if isinstance(parsed, dict) else {"data": parsed}


def _raw_request(
    url: str,
    *,
    method: str,
    data: bytes | None,
    headers: dict[str, str],
    timeout: int,
) -> bytes:
    request = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={"User-Agent": "AnvilMediaTools/1.0", **headers},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def _multipart_body(*, fields: dict[str, str], file_path: Path, file_field: str) -> tuple[bytes, str]:
    boundary = f"----AnvilMediaTools{uuid4().hex}"
    parts: list[bytes] = []
    for name, value in fields.items():
        parts.extend(
            [
                f"--{boundary}\r\n".encode("utf-8"),
                f'Content-Disposition: form-data; name="{_quote_header(name)}"\r\n\r\n'.encode("utf-8"),
                str(value).encode("utf-8"),
                b"\r\n",
            ]
        )
    content_type = mimetypes.guess_type(file_path.name)[0] or "application/octet-stream"
    parts.extend(
        [
            f"--{boundary}\r\n".encode("utf-8"),
            f'Content-Disposition: form-data; name="{_quote_header(file_field)}"; filename="{_quote_header(file_path.name)}"\r\n'.encode("utf-8"),
            f"Content-Type: {content_type}\r\n\r\n".encode("utf-8"),
            file_path.read_bytes(),
            b"\r\n",
            f"--{boundary}--\r\n".encode("utf-8"),
        ]
    )
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def _quote_header(value: str) -> str:
    return urllib.parse.quote(str(value), safe="._-")


def _resolve_secret(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        return ""
    text = value.strip()
    if text.startswith("${") and text.endswith("}"):
        return os.getenv(text[2:-1], "")
    if text.startswith("$"):
        return os.getenv(text[1:], "")
    return text


def _env_reference(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        return ""
    return f"${value.strip()}"


def _error_payload(provider: str, exc: Exception) -> dict[str, str]:
    message = str(exc)
    if isinstance(exc, urllib.error.HTTPError):
        message = f"HTTP {exc.code}: {exc.reason}"
    return {"provider": provider, "error": _scrub_text(message)[:500]}


def _bounded_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, parsed))


def _bounded_float(value: Any, *, default: float, minimum: float, maximum: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return default
    return max(minimum, min(maximum, parsed))


def _scrub_text(value: str) -> str:
    return SECRET_VALUE_RE.sub("[REDACTED]", value)


def _scrub_data(value: Any) -> Any:
    if isinstance(value, str):
        return _scrub_text(value)
    if isinstance(value, list):
        return [_scrub_data(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _scrub_data(item) for key, item in value.items()}
    return value
