from __future__ import annotations

from langchain_core.messages import AIMessage

from anvil.agents import ThreadLifecycleStatus
from anvil.config import ConfigLayer, ConfigLayerKind
from anvil.runtime.checkpointers import CheckpointerBackend, create_checkpointer
from anvil.runtime.runs import RunEngine, RunRequest
from anvil.runtime.store import StoreBackend, create_store
from anvil.sandbox import PathService
from fake_models import BindableFakeMessagesListChatModel


def base_layers() -> list[ConfigLayer]:
    return [
        ConfigLayer(
            name="default",
            kind=ConfigLayerKind.DEFAULT,
            data={
                "default_model": "openai",
                "models": {
                    "openai": {
                        "name": "openai",
                        "provider": "openai",
                        "provider_kind": "openai_compatible",
                        "model_name": "gpt-5.4",
                    }
                },
            },
        )
    ]


def test_clarification_tool_interrupts_execution_and_marks_thread_state(contract_tmp_path) -> None:
    engine = RunEngine()
    result = engine.run(
        RunRequest(
            thread_id="thread-clarify",
            user_message="do the risky thing",
            config_layers=base_layers(),
            path_service=PathService(contract_tmp_path / "threads"),
            checkpointer=create_checkpointer(CheckpointerBackend.IN_MEMORY),
            store=create_store(StoreBackend.IN_MEMORY),
            chat_model_override=BindableFakeMessagesListChatModel(
                responses=[
                    AIMessage(
                        content="",
                        tool_calls=[
                            {
                                "name": "ask_clarification",
                                "args": {
                                    "question": "Which target environment should I use?",
                                    "clarification_type": "missing_info",
                                    "options": ["staging", "production"],
                                },
                                "id": "call_1",
                                "type": "tool_call",
                            }
                        ],
                    )
                ]
            ),
        )
    )

    assert result.thread_state.lifecycle.status is ThreadLifecycleStatus.AWAITING_CLARIFICATION
    assert result.thread_state.lifecycle.last_error == "Which target environment should I use?"
    assert result.thread_state.conversation.messages[-1]["role"] == "tool"
    assert result.thread_state.conversation.messages[-1]["name"] == "ask_clarification"
    assert "Which target environment should I use?" in result.thread_state.conversation.messages[-1]["content"]


def test_clarification_tool_persists_structured_user_interaction(contract_tmp_path) -> None:
    engine = RunEngine()
    result = engine.run(
        RunRequest(
            thread_id="thread-choice",
            user_message="build a frontend app",
            config_layers=base_layers(),
            path_service=PathService(contract_tmp_path / "threads"),
            checkpointer=create_checkpointer(CheckpointerBackend.IN_MEMORY),
            store=create_store(StoreBackend.IN_MEMORY),
            chat_model_override=BindableFakeMessagesListChatModel(
                responses=[
                    AIMessage(
                        content="",
                        tool_calls=[
                            {
                                "name": "ask_clarification",
                                "args": {
                                    "title": "Choose a frontend stack",
                                    "question": "Which stack should I scaffold?",
                                    "response_type": "single_select",
                                    "options": [
                                        {
                                            "id": "vite-react",
                                            "label": "Vite + React",
                                            "description": "Fast single-page app starter.",
                                            "recommended": True,
                                        },
                                        {
                                            "id": "nextjs",
                                            "label": "Next.js",
                                            "description": "Full-stack React with routing.",
                                        },
                                    ],
                                    "allow_custom": True,
                                },
                                "id": "call_stack",
                                "type": "tool_call",
                            }
                        ],
                    )
                ]
            ),
        )
    )

    assert result.thread_state.lifecycle.status is ThreadLifecycleStatus.AWAITING_CLARIFICATION
    interaction = result.thread_state.conversation.pending_user_interaction
    assert interaction is not None
    assert interaction["request_id"] == "call_stack"
    assert interaction["title"] == "Choose a frontend stack"
    assert interaction["selection_mode"] == "single"
    assert interaction["allow_custom"] is True
    assert interaction["options"][0]["id"] == "vite-react"
    assert interaction["options"][0]["recommended"] is True
    assert interaction["options"][1]["description"] == "Full-stack React with routing."


def test_clarification_tool_persists_multi_field_decision_form(contract_tmp_path) -> None:
    engine = RunEngine()
    result = engine.run(
        RunRequest(
            thread_id="thread-form-choice",
            user_message="build a frontend app",
            config_layers=base_layers(),
            path_service=PathService(contract_tmp_path / "threads"),
            checkpointer=create_checkpointer(CheckpointerBackend.IN_MEMORY),
            store=create_store(StoreBackend.IN_MEMORY),
            chat_model_override=BindableFakeMessagesListChatModel(
                responses=[
                    AIMessage(
                        content="",
                        tool_calls=[
                            {
                                "name": "ask_clarification",
                                "args": {
                                    "title": "Frontend build decisions",
                                    "question": "Choose the scaffold contract before I start.",
                                    "fields": [
                                        {
                                            "id": "stack",
                                            "label": "Framework",
                                            "selection_mode": "single",
                                            "options": [
                                                {"id": "vite-react", "label": "Vite + React", "recommended": True},
                                                {"id": "next-app-router", "label": "Next.js App Router"},
                                            ],
                                        },
                                        {
                                            "id": "scope",
                                            "label": "Completeness",
                                            "selection_mode": "multiple",
                                            "min_selections": 1,
                                            "max_selections": 2,
                                            "options": [
                                                {"id": "routing", "label": "Routing"},
                                                {"id": "tests", "label": "Tests"},
                                                {"id": "docker", "label": "Docker"},
                                            ],
                                        },
                                        {
                                            "id": "notes",
                                            "label": "Extra constraints",
                                            "selection_mode": "text",
                                            "required": False,
                                            "placeholder": "Any constraints",
                                        },
                                    ],
                                },
                                "id": "call_form",
                                "type": "tool_call",
                            }
                        ],
                    )
                ]
            ),
        )
    )

    assert result.thread_state.lifecycle.status is ThreadLifecycleStatus.AWAITING_CLARIFICATION
    interaction = result.thread_state.conversation.pending_user_interaction
    assert interaction is not None
    assert interaction["kind"] == "form"
    assert interaction["request_id"] == "call_form"
    assert len(interaction["fields"]) == 3
    assert interaction["fields"][0]["field_id"] == "stack"
    assert interaction["fields"][0]["options"][0]["recommended"] is True
    assert interaction["fields"][1]["selection_mode"] == "multiple"
    assert interaction["fields"][1]["max_selections"] == 2
    assert interaction["fields"][2]["selection_mode"] == "text"
    assert interaction["fields"][2]["required"] is False
