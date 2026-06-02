from __future__ import annotations

from langchain_core.language_models.chat_models import BaseChatModel


class CapturingChatModel(BaseChatModel):
    captured_kwargs: dict = {}

    def __init__(self, **kwargs):
        self.__class__.captured_kwargs = dict(kwargs)
        super().__init__(**kwargs)

    @property
    def _llm_type(self) -> str:
        return "capturing"

    def _generate(self, *args, **kwargs):  # type: ignore[override]
        raise NotImplementedError

    def _stream(self, *args, **kwargs):  # type: ignore[override]
        raise NotImplementedError


class CapturingReasoningCliChatModel(CapturingChatModel):
    pass


class StrictChatModel(CapturingChatModel):
    captured_kwargs: dict = {}

    def __init__(self, *, model: str, api_key: str | None = None, timeout: float | None = None):
        self.__class__.captured_kwargs = {"model": model, "api_key": api_key, "timeout": timeout}
        super().__init__(model=model, api_key=api_key, timeout=timeout)


class TypeErrorRetryChatModel(CapturingChatModel):
    captured_kwargs: dict = {}

    def __init__(self, **kwargs):
        if "temperature" in kwargs:
            raise TypeError("__init__() got an unexpected keyword argument 'temperature'")
        self.__class__.captured_kwargs = dict(kwargs)
        super().__init__(**kwargs)


class FailingSecretChatModel(CapturingChatModel):
    def __init__(self, **kwargs):
        raise RuntimeError(f"provider rejected Authorization: Bearer {kwargs.get('api_key')}")
