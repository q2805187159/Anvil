from __future__ import annotations

import json
import threading
from pathlib import Path

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.messages import BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from anvil.config import ConfigLayer, ConfigLayerKind
from fake_models import BindableFakeMessagesListChatModel


def _minimal_pdf_bytes(text: str) -> bytes:
    objects = [
        b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n",
        b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n",
        (
            b"3 0 obj\n"
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 144] "
            b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>\n"
            b"endobj\n"
        ),
        (
            f"4 0 obj\n<< /Length {len(f'BT\\n/F1 24 Tf\\n72 72 Td\\n({text}) Tj\\nET\\n'.encode('latin-1'))} >>\nstream\n"
            f"BT\n/F1 24 Tf\n72 72 Td\n({text}) Tj\nET\n"
            "endstream\nendobj\n"
        ).encode("latin-1"),
        b"5 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n",
    ]
    header = b"%PDF-1.4\n"
    chunks = [header]
    offsets = [0]
    current = len(header)
    for obj in objects:
        offsets.append(current)
        chunks.append(obj)
        current += len(obj)
    xref_offset = current
    xref = [b"xref\n0 6\n", b"0000000000 65535 f \n"]
    for offset in offsets[1:]:
        xref.append(f"{offset:010d} 00000 n \n".encode("ascii"))
    trailer = (
        b"trailer\n<< /Root 1 0 R /Size 6 >>\n"
        + f"startxref\n{xref_offset}\n%%EOF\n".encode("ascii")
    )
    chunks.extend(xref)
    chunks.append(trailer)
    return b"".join(chunks)


class EchoLastUserChatModel(BaseChatModel):
    @property
    def _llm_type(self) -> str:
        return "echo-last-user"

    def bind_tools(self, tools, *, tool_choice=None, **kwargs):
        return self

    def _generate(self, messages: list[BaseMessage], stop=None, run_manager=None, **kwargs):  # type: ignore[override]
        latest_user = next(
            (str(getattr(message, "content", "")) for message in reversed(messages) if getattr(message, "type", None) == "human"),
            "",
        )
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content=f"echo:{latest_user}"))])


class DelegationRoundTripChatModel(BaseChatModel):
    @property
    def _llm_type(self) -> str:
        return "delegation-round-trip"

    def bind_tools(self, tools, *, tool_choice=None, **kwargs):
        return self

    def _generate(self, messages: list[BaseMessage], stop=None, run_manager=None, **kwargs):  # type: ignore[override]
        human_messages = [
            str(getattr(message, "content", ""))
            for message in messages
            if getattr(message, "type", None) == "human"
        ]
        tool_messages = [message for message in messages if getattr(message, "type", None) == "tool"]
        latest_human = human_messages[-1] if human_messages else ""

        if latest_human.startswith("Create /mnt/user-data/workspace/hello.md with hello"):
            if not any(getattr(message, "name", None) == "write_file" for message in tool_messages):
                return ChatResult(
                    generations=[
                        ChatGeneration(
                            message=AIMessage(
                                content="",
                                tool_calls=[
                                    {
                                        "name": "write_file",
                                        "args": {"path": "/mnt/user-data/workspace/hello.md", "content": "hello\n"},
                                        "id": "child_write_1",
                                        "type": "tool_call",
                                    }
                                ],
                            )
                        )
                    ]
                )
            return ChatResult(generations=[ChatGeneration(message=AIMessage(content="created hello.md"))])

        delegated_tool_message = next(
            (message for message in reversed(tool_messages) if getattr(message, "name", None) == "delegated_task"),
            None,
        )
        if delegated_tool_message is None:
            return ChatResult(
                generations=[
                    ChatGeneration(
                        message=AIMessage(
                            content="",
                            tool_calls=[
                                {
                                    "name": "delegated_task",
                                    "args": {
                                        "prompt": "Create /mnt/user-data/workspace/hello.md with hello",
                                        "requested_tool_names": ["write_file"],
                                    },
                                    "id": "delegate_round_trip_1",
                                    "type": "tool_call",
                                }
                            ],
                        )
                    )
                ]
            )

        subagent_tool_message = next(
            (message for message in reversed(tool_messages) if getattr(message, "name", None) == "subagent"),
            None,
        )
        if subagent_tool_message is None:
            payload = json.loads(str(delegated_tool_message.content))
            return ChatResult(
                generations=[
                    ChatGeneration(
                        message=AIMessage(
                            content="",
                            tool_calls=[
                                {
                                    "name": "subagent",
                                    "args": {"action": "wait", "task_id": payload["task_id"], "timeout_seconds": 5},
                                    "id": "delegate_round_trip_wait",
                                    "type": "tool_call",
                                }
                            ],
                        )
                    )
                ]
            )

        payload = json.loads(str(subagent_tool_message.content))
        return ChatResult(
            generations=[
                ChatGeneration(
                    message=AIMessage(
                        content=f"Subagent completed: {payload.get('summary') or payload.get('status')}"
                    )
                )
            ]
        )


def test_sync_run_endpoint_returns_translated_completion_view(gateway_app_factory) -> None:
    app = gateway_app_factory(
        chat_model_override=BindableFakeMessagesListChatModel(responses=[AIMessage(content="hello from model")])
    )
    from fastapi.testclient import TestClient

    with TestClient(app) as client:
        client.post("/threads", json={"thread_id": "thread-run"})
        response = client.post(
            "/threads/thread-run/runs",
            json={"message": "say hello"},
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["thread_id"] == "thread-run"
        assert payload["status"] == "completed"
        assert payload["state"]["execution_mode"] == "agent"
        assert payload["assistant_message"] == "hello from model"
        assert payload["thread"]["thread_id"] == "thread-run"


def test_run_endpoint_translates_runtime_unavailable_errors(gateway_app_factory) -> None:
    broken_layers = [
        ConfigLayer(
            name="default",
            kind=ConfigLayerKind.DEFAULT,
            data={"default_model": "missing-model", "models": {}},
        )
    ]
    app = gateway_app_factory(config_layers=broken_layers)
    from fastapi.testclient import TestClient

    with TestClient(app) as client:
        client.post("/threads", json={"thread_id": "broken-thread"})
        response = client.post("/threads/broken-thread/runs", json={"message": "hello"})
        assert response.status_code == 503
        assert response.json()["error"] == "runtime_unavailable"


def test_run_endpoint_translates_host_path_in_runtime_unavailable_detail(monkeypatch, gateway_app_factory) -> None:
    app = gateway_app_factory(
        chat_model_override=BindableFakeMessagesListChatModel(responses=[AIMessage(content="unused")])
    )
    from fastapi.testclient import TestClient

    with TestClient(app) as client:
        client.post("/threads", json={"thread_id": "thread-run-fail"})
        deps = client.app.state.runtime_deps
        workspace = deps.path_service.thread_workspace_dir("thread-run-fail")

        def fail_run(*args, **kwargs):
            raise RuntimeError(f"failed while opening {workspace}")

        monkeypatch.setattr(deps.run_engine, "run", fail_run)
        response = client.post("/threads/thread-run-fail/runs", json={"message": "hello"})

    assert response.status_code == 503
    payload = response.json()
    assert payload["error"] == "runtime_unavailable"
    assert "/mnt/user-data/workspace" in payload["detail"].replace("\\", "/")
    assert str(workspace) not in payload["detail"]


def test_approval_endpoint_translates_host_path_in_runtime_unavailable_detail(monkeypatch, gateway_app_factory) -> None:
    app = gateway_app_factory(
        chat_model_override=BindableFakeMessagesListChatModel(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "write_file",
                            "args": {"path": "/mnt/user-data/workspace/example.txt", "content": "hello"},
                            "id": "call_1",
                            "type": "tool_call",
                        }
                    ],
                )
            ]
        )
    )
    from fastapi.testclient import TestClient

    with TestClient(app) as client:
        client.post("/threads", json={"thread_id": "thread-approval-sync-fail"})
        pending = client.post("/threads/thread-approval-sync-fail/runs", json={"message": "write a file"})
        assert pending.status_code == 200
        assert pending.json()["status"] == "awaiting_approval"

        deps = client.app.state.runtime_deps
        workspace = deps.path_service.thread_workspace_dir("thread-approval-sync-fail")

        def fail_resume_approval(*args, **kwargs):
            raise RuntimeError(f"failed while resuming {workspace}")

        monkeypatch.setattr(deps.run_engine, "resume_approval", fail_resume_approval)
        response = client.post(
            "/threads/thread-approval-sync-fail/approvals/approve",
            json={"approval_context": "approved for this turn"},
        )

    assert response.status_code == 503
    payload = response.json()
    assert payload["error"] == "runtime_unavailable"
    assert "/mnt/user-data/workspace" in payload["detail"].replace("\\", "/")
    assert str(workspace) not in payload["detail"]


def test_run_endpoint_supports_chat_execution_mode_without_visible_tools(gateway_app_factory) -> None:
    app = gateway_app_factory(
        chat_model_override=BindableFakeMessagesListChatModel(responses=[AIMessage(content="chat only reply")])
    )
    from fastapi.testclient import TestClient

    with TestClient(app) as client:
        client.post("/threads", json={"thread_id": "chat-thread"})
        response = client.post(
            "/threads/chat-thread/runs",
            json={"message": "just chat", "execution_mode": "chat"},
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["state"]["execution_mode"] == "chat"
        assert payload["state"]["visible_tool_names"] == []
        assert payload["state"]["recent_tool_activity"] == []


def test_run_endpoint_supports_full_access_execution_mode(gateway_app_factory) -> None:
    app = gateway_app_factory(
        chat_model_override=BindableFakeMessagesListChatModel(responses=[AIMessage(content="full access reply")])
    )
    from fastapi.testclient import TestClient

    with TestClient(app) as client:
        client.post("/threads", json={"thread_id": "full-access-thread"})
        response = client.post(
            "/threads/full-access-thread/runs",
            json={"message": "show tools", "execution_mode": "full_access"},
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["state"]["execution_mode"] == "full_access"
        assert "ext_search" in payload["state"]["visible_tool_names"]
        assert payload["state"]["requires_approval_actions"] == []
        assert "without approval prompts" in payload["state"]["approval_policy_summary"]


def test_full_access_write_file_still_requires_guardrail_approval(gateway_app_factory) -> None:
    app = gateway_app_factory(
        chat_model_override=BindableFakeMessagesListChatModel(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "write_file",
                            "args": {"path": "/mnt/user-data/workspace/notes.txt", "content": "hello"},
                            "id": "call_1",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(content="write complete"),
            ]
        )
    )
    from fastapi.testclient import TestClient

    with TestClient(app) as client:
        client.post("/threads", json={"thread_id": "full-access-write"})
        response = client.post(
            "/threads/full-access-write/runs",
            json={"message": "write a file", "execution_mode": "full_access"},
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["status"] == "completed"
        assert payload["state"]["has_pending_approval"] is False


def test_run_endpoint_generates_thread_title_from_first_user_turn(gateway_app_factory) -> None:
    app = gateway_app_factory(
        chat_model_override=BindableFakeMessagesListChatModel(responses=[AIMessage(content="hello from model")])
    )
    from fastapi.testclient import TestClient

    with TestClient(app) as client:
        client.post("/threads", json={"thread_id": "thread-title"})
        response = client.post(
            "/threads/thread-title/runs",
            json={"message": "Need a concise summary of this first question for the thread rail"},
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["thread"]["title"].startswith("Need a concise summary")
        assert payload["thread"]["title"].endswith("...")

        listed = client.get("/threads").json()
        assert listed[0]["title"] == payload["thread"]["title"]
        assert listed[0]["last_user_message_preview"] == "Need a concise summary of this first question for the thread rail"


def test_run_endpoint_generates_llm_thread_title_when_configured(gateway_app_factory, monkeypatch) -> None:
    invoked = threading.Event()
    release = threading.Event()

    class FakeTitleModel:
        def invoke(self, prompt: str):
            invoked.set()
            release.wait(timeout=3)
            return AIMessage(content="Generated Project Title")

    monkeypatch.setattr("anvil.agents.middlewares.title_middleware.create_chat_model", lambda *args, **kwargs: FakeTitleModel())
    app = gateway_app_factory(
        config_layers=[
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
                        },
                        "title-mini": {
                            "name": "title-mini",
                            "provider": "openai",
                            "provider_kind": "openai_compatible",
                            "model_name": "gpt-4o-mini",
                        },
                    },
                    "subsystem_models": {"title": "title-mini"},
                    "title": {"enabled": True, "generation_strategy": "llm", "max_length": 60},
                },
            )
        ],
        chat_model_override=BindableFakeMessagesListChatModel(responses=[AIMessage(content="hello from model")]),
    )
    from fastapi.testclient import TestClient

    with TestClient(app) as client:
        client.post("/threads", json={"thread_id": "thread-title-llm"})
        response = client.post(
            "/threads/thread-title-llm/runs",
            json={"message": "Need a concise summary of this first question for the thread rail"},
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["thread"]["title"].startswith("Need a concise summary")
        assert invoked.wait(timeout=1)
        release.set()
        app.state.runtime_deps.run_engine.wait_for_background_tasks(timeout_seconds=5)
        detail = client.get("/threads/thread-title-llm/detail").json()
        assert detail["thread"]["title"] == "Generated Project Title"


def test_run_endpoint_bounds_llm_thread_title_for_sidebar(gateway_app_factory, monkeypatch) -> None:
    class FakeTitleModel:
        def invoke(self, prompt: str, config=None):
            assert "fits the sidebar row" in prompt
            return AIMessage(content="这是一个非常非常长的中文会话标题会超出侧边栏")

    monkeypatch.setattr("anvil.agents.middlewares.title_middleware.create_chat_model", lambda *args, **kwargs: FakeTitleModel())
    app = gateway_app_factory(
        config_layers=[
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
                    "title": {"enabled": True, "generation_strategy": "llm", "max_length": 60},
                },
            )
        ],
        chat_model_override=BindableFakeMessagesListChatModel(responses=[AIMessage(content="hello from model")]),
    )
    from fastapi.testclient import TestClient

    with TestClient(app) as client:
        client.post("/threads", json={"thread_id": "thread-title-bounded"})
        response = client.post(
            "/threads/thread-title-bounded/runs",
            json={"message": "需要为这个会话生成一个很短的标题"},
        )

        assert response.status_code == 200
        app.state.runtime_deps.run_engine.wait_for_background_tasks(timeout_seconds=5)
        detail = client.get("/threads/thread-title-bounded/detail").json()
        assert detail["thread"]["title"] == "这是一个非常非常长的中文会话标..."
        assert len(detail["thread"]["title"]) == 18


def test_run_endpoint_uses_title_subsystem_model_without_llm_strategy(gateway_app_factory, monkeypatch) -> None:
    invoked = threading.Event()
    release = threading.Event()

    class FakeTitleModel:
        def invoke(self, prompt: str):
            invoked.set()
            release.wait(timeout=3)
            return AIMessage(content="Subsystem Title")

    monkeypatch.setattr("anvil.agents.middlewares.title_middleware.create_chat_model", lambda *args, **kwargs: FakeTitleModel())
    app = gateway_app_factory(
        config_layers=[
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
                        },
                        "title-mini": {
                            "name": "title-mini",
                            "provider": "openai",
                            "provider_kind": "openai_compatible",
                            "model_name": "gpt-4o-mini",
                        },
                    },
                    "subsystem_models": {"title": "title-mini"},
                    "title": {"enabled": True, "max_length": 60},
                },
            )
        ],
        chat_model_override=BindableFakeMessagesListChatModel(responses=[AIMessage(content="hello from model")]),
    )
    from fastapi.testclient import TestClient

    with TestClient(app) as client:
        client.post("/threads", json={"thread_id": "thread-title-subsystem"})
        response = client.post(
            "/threads/thread-title-subsystem/runs",
            json={"message": "Need a concise summary of this first question for the thread rail"},
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["thread"]["title"].startswith("Need a concise summary")
        assert invoked.wait(timeout=1)
        release.set()
        app.state.runtime_deps.run_engine.wait_for_background_tasks(timeout_seconds=5)
        detail = client.get("/threads/thread-title-subsystem/detail").json()
        assert detail["thread"]["title"] == "Subsystem Title"


def test_run_endpoint_uses_internal_task_concrete_model_for_title(gateway_app_factory, monkeypatch) -> None:
    invoked = threading.Event()
    release = threading.Event()
    calls: dict[str, object] = {}

    class FakeTitleModel:
        def invoke(self, prompt: str, config=None):
            invoked.set()
            calls["config"] = config
            release.wait(timeout=3)
            return AIMessage(content="Mimo Background Title")

    def fake_create_chat_model(model_config, **kwargs):
        calls["model_name"] = model_config.model_name
        calls["selected_model"] = model_config.selected_model
        calls["thinking_enabled"] = kwargs.get("thinking_enabled")
        return FakeTitleModel()

    monkeypatch.setattr("anvil.agents.middlewares.title_middleware.create_chat_model", fake_create_chat_model)
    app = gateway_app_factory(
        config_layers=[
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
                        },
                        "minimax": {
                            "name": "minimax",
                            "provider": "minimax",
                            "provider_kind": "openai_compatible",
                            "model": ["MiniMax-M2.7", "mimo-v2-flash"],
                            "default_model": "MiniMax-M2.7",
                        },
                    },
                    "llm": {
                        "internal_task_model": "mimo-v2-flash",
                        "subsystems": {"title": "minimax"},
                    },
                    "title": {"enabled": True, "max_length": 60},
                },
            )
        ],
        chat_model_override=BindableFakeMessagesListChatModel(responses=[AIMessage(content="hello from model")]),
    )
    from fastapi.testclient import TestClient

    with TestClient(app) as client:
        client.post("/threads", json={"thread_id": "thread-title-internal-task-model"})
        response = client.post(
            "/threads/thread-title-internal-task-model/runs",
            json={"message": "Need a title from the configured background model"},
        )

        assert response.status_code == 200
        assert invoked.wait(timeout=1)
        release.set()
        app.state.runtime_deps.run_engine.wait_for_background_tasks(timeout_seconds=5)
        detail = client.get("/threads/thread-title-internal-task-model/detail").json()
        assert detail["thread"]["title"] == "Mimo Background Title"

    assert calls["model_name"] == "mimo-v2-flash"
    assert calls["selected_model"] == "mimo-v2-flash"
    assert calls["thinking_enabled"] is False


def test_run_endpoint_generates_title_when_main_model_fails(gateway_app_factory) -> None:
    class FailingFirstTurnChatModel(BaseChatModel):
        @property
        def _llm_type(self) -> str:
            return "failing-first-turn"

        def bind_tools(self, tools, *, tool_choice=None, **kwargs):
            return self

        def _generate(self, messages: list[BaseMessage], stop=None, run_manager=None, **kwargs):  # type: ignore[override]
            raise RuntimeError("main model unavailable")

    app = gateway_app_factory(chat_model_override=FailingFirstTurnChatModel())
    from fastapi.testclient import TestClient
    first_message = "Investigate title generation even when the main model fails during the first user turn"

    with TestClient(app) as client:
        client.post("/threads", json={"thread_id": "thread-title-failure"})
        response = client.post(
            "/threads/thread-title-failure/runs",
            json={"message": first_message},
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["status"] == "failed"
        assert payload["thread"]["title"].startswith("Investigate title generation")
        assert payload["thread"]["title"].endswith("...")
        detail = client.get("/threads/thread-title-failure/detail").json()
        assert detail["messages"][0]["role"] in {"user", "human"}
        assert detail["messages"][0]["content"] == first_message


def test_run_endpoint_strips_inline_think_tags_from_final_answer(gateway_app_factory) -> None:
    app = gateway_app_factory(
        chat_model_override=BindableFakeMessagesListChatModel(
            responses=[AIMessage(content="<think>private reasoning</think>\n\n你好，我可以帮你处理任务。")]
        )
    )
    from fastapi.testclient import TestClient

    with TestClient(app) as client:
        client.post("/threads", json={"thread_id": "thread-think-tags"})
        response = client.post(
            "/threads/thread-think-tags/runs",
            json={"message": "你好，你都可以干什么"},
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["assistant_message"] == "你好，我可以帮你处理任务。"
        detail = client.get("/threads/thread-think-tags/detail")
        assert detail.status_code == 200
        assert "<think>" not in detail.json()["messages"][-1]["content"]


def test_run_endpoint_can_read_uploaded_pdf_as_text(gateway_app_factory) -> None:
    app = gateway_app_factory(
        chat_model_override=BindableFakeMessagesListChatModel(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "read_file",
                            "args": {"path": "/mnt/user-data/uploads/resume.pdf"},
                            "id": "call_1",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(content="PDF processed."),
            ]
        )
    )
    from fastapi.testclient import TestClient

    with TestClient(app) as client:
        client.post("/threads", json={"thread_id": "thread-pdf-read"})
        upload = client.post(
            "/threads/thread-pdf-read/uploads",
            files=[("files", ("resume.pdf", _minimal_pdf_bytes("Resume Source Text"), "application/pdf"))],
        )
        assert upload.status_code == 200

        response = client.post(
            "/threads/thread-pdf-read/runs",
            json={"message": "Read the uploaded pdf."},
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["status"] == "completed"
        assert payload["state"]["recent_tool_activity"][0]["name"] == "read_file"
        assert "Resume Source Text" in payload["state"]["recent_tool_activity"][0]["result_text"]


def test_edit_latest_and_resend_reruns_from_latest_user_message(gateway_app_factory) -> None:
    app = gateway_app_factory(chat_model_override=EchoLastUserChatModel())
    from fastapi.testclient import TestClient

    with TestClient(app) as client:
        client.post("/threads", json={"thread_id": "thread-edit-latest"})
        first = client.post("/threads/thread-edit-latest/runs", json={"message": "first prompt"})
        assert first.status_code == 200
        detail = client.get("/threads/thread-edit-latest/detail").json()
        latest_user = next(message for message in detail["messages"] if message["role"] == "human")

        edited = client.post(
            f"/threads/thread-edit-latest/messages/{latest_user['message_id']}/edit-latest-and-resend",
            json={"message": "edited prompt"},
        )

        assert edited.status_code == 200
        payload = edited.json()
        assert payload["assistant_message"] == "echo:edited prompt"
        after_detail = client.get("/threads/thread-edit-latest/detail").json()
        assert [message["content"] for message in after_detail["messages"] if message["role"] == "human"] == ["edited prompt"]


def test_edit_latest_and_resend_rejects_non_latest_user_message(gateway_app_factory) -> None:
    app = gateway_app_factory(chat_model_override=EchoLastUserChatModel())
    from fastapi.testclient import TestClient

    with TestClient(app) as client:
        client.post("/threads", json={"thread_id": "thread-edit-old"})
        assert client.post("/threads/thread-edit-old/runs", json={"message": "first prompt"}).status_code == 200
        assert client.post("/threads/thread-edit-old/runs", json={"message": "second prompt"}).status_code == 200
        detail = client.get("/threads/thread-edit-old/detail").json()
        user_messages = [message for message in detail["messages"] if message["role"] == "human"]

        rejected = client.post(
            f"/threads/thread-edit-old/messages/{user_messages[0]['message_id']}/edit-latest-and-resend",
            json={"message": "edited old prompt"},
        )

        assert rejected.status_code == 409
        assert rejected.json()["error"] == "latest_user_message_only"


def test_run_endpoint_registers_outputs_created_by_export_document(gateway_app_factory, monkeypatch) -> None:
    from anvil.documents import ExportedDocumentResult

    def fake_export_document_file(*, output_path, content, format, mode, scratch_root, cleanup_intermediates):
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(b"docx-bytes")
        return ExportedDocumentResult(
            output_path=output_path,
            mode=mode,
            format=format,
            provider="test-exporter",
            warnings=(),
            scratch_paths=(),
            cleaned_scratch_paths=(),
        )

    monkeypatch.setattr("anvil.tools.assembly.export_document_file", fake_export_document_file)

    app = gateway_app_factory(
        chat_model_override=BindableFakeMessagesListChatModel(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "export_document",
                            "args": {
                                "content": "# Resume\n\nBody",
                                "output_path": "/mnt/user-data/outputs/generated-resume.docx",
                            },
                            "id": "call_1",
                            "type": "tool_call",
                        }
                    ],
                ),
                AIMessage(content="generated"),
            ]
        )
    )
    from fastapi.testclient import TestClient

    with TestClient(app) as client:
        client.post("/threads", json={"thread_id": "thread-export-docx"})
        response = client.post(
            "/threads/thread-export-docx/runs",
            json={
                "message": "Create the final Word file.",
                "execution_mode": "full_access",
                "approval_context": "approved for this turn",
            },
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["status"] == "completed"
        assert payload["state"]["output_artifacts"][0]["label"] == "generated-resume.docx"
        assert payload["state"]["output_artifacts"][0]["artifact_url"].endswith("/threads/thread-export-docx/artifacts/outputs/generated-resume.docx")


def test_run_endpoint_default_subagent_runner_executes_and_returns_result(gateway_app_factory) -> None:
    app = gateway_app_factory(chat_model_override=DelegationRoundTripChatModel())
    from fastapi.testclient import TestClient

    with TestClient(app) as client:
        client.post("/threads", json={"thread_id": "thread-subagent-real"})
        response = client.post(
            "/threads/thread-subagent-real/runs",
            json={"message": "use a subagent to create hello.md", "execution_mode": "full_access"},
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["status"] == "completed"
        assert "Subagent completed: created hello.md" in payload["assistant_message"]
        subagent_tasks = payload["state"]["subagent_tasks"]
        assert subagent_tasks[0]["status"] == "completed"
        assert subagent_tasks[0]["child_thread_id"] is not None
        assert subagent_tasks[0]["child_run_id"] is not None
        assert subagent_tasks[0]["recent_tool_activity"][0]["name"] == "write_file"
        child_file = app.state.runtime_deps.path_service.thread_workspace_dir("thread-subagent-real") / "hello.md"
        assert child_file.read_text(encoding="utf-8") == "hello\n"
