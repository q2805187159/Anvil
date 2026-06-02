from __future__ import annotations

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel


class BindableFakeMessagesListChatModel(FakeMessagesListChatModel):
    def bind_tools(self, tools, *, tool_choice=None, **kwargs):
        return self
