from __future__ import annotations

from pathlib import Path

from langchain_core.messages import AIMessage

from anvil import ApprovalDecision, ExecutionFailureClassification, NetworkApprovalDecision
from anvil.agents import ThreadLifecycleStatus
from anvil.config import ConfigLayer, ConfigLayerKind
from anvil.runtime.approvals import ApprovalService, NetworkApprovalService
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
                "guardrails": {"enabled": True},
            },
        )
    ]


def test_pending_approval_preserves_typed_request_and_sandbox_state(contract_tmp_path: Path) -> None:
    engine = RunEngine()
    result = engine.run(
        RunRequest(
            thread_id="thread-approval",
            user_message="write a file",
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
                                "name": "write_file",
                                "args": {"path": "/mnt/user-data/workspace/example.txt", "content": "hello"},
                                "id": "call_1",
                                "type": "tool_call",
                            }
                        ],
                    ),
                ]
            ),
        )
    )

    assert result.thread_state.lifecycle.status is ThreadLifecycleStatus.AWAITING_APPROVAL
    assert result.thread_state.approvals.pending_approval is ApprovalDecision.NEEDS_USER_APPROVAL
    assert result.thread_state.approvals.approval_request is not None
    assert result.thread_state.approvals.approval_request.action_kind == "tool_call"
    assert result.thread_state.approvals.approval_request.requested_permissions == ["filesystem_write"]
    assert result.thread_state.execution.sandbox_state is not None
    assert result.thread_state.execution.sandbox_state.sandbox_id == "local:thread-approval"


def test_approved_path_executes_with_path_projection_consistent_to_path_service(contract_tmp_path: Path) -> None:
    path_service = PathService(contract_tmp_path / "threads")
    engine = RunEngine()
    result = engine.run(
        RunRequest(
            thread_id="thread-approved",
            user_message="write a file",
            config_layers=base_layers(),
            path_service=path_service,
            checkpointer=create_checkpointer(CheckpointerBackend.IN_MEMORY),
            store=create_store(StoreBackend.IN_MEMORY),
            approval_context="approved for this turn",
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
                    ),
                    AIMessage(content="done"),
                ]
            ),
        )
    )

    expected_projection = path_service.to_sandbox_projection(
        "thread-approved",
        writable_kinds=("workspace", "outputs"),
    )
    assert result.thread_state.lifecycle.status is ThreadLifecycleStatus.COMPLETED
    assert result.thread_state.approvals.pending_approval is None
    assert result.runtime.context.sandbox_handle.projection == expected_projection
    assert (contract_tmp_path / "threads" / "thread-approved" / "workspace" / "example.txt").read_text(
        encoding="utf-8"
    ) == "hello"


def test_network_approval_grants_do_not_leak_between_sessions() -> None:
    service = NetworkApprovalService()
    assert service.classify(session_id="thread-a", host="example.com") is NetworkApprovalDecision.PROMPT
    grant = service.grant(session_id="thread-a", host="example.com")
    assert grant.network_hosts == ["example.com"]
    assert service.classify(session_id="thread-a", host="example.com") is NetworkApprovalDecision.ALLOW
    assert service.classify(session_id="thread-b", host="example.com") is NetworkApprovalDecision.PROMPT


def test_failure_classification_matches_policy_paths() -> None:
    service = ApprovalService()
    assert service.classify_failure(approved=False, had_policy=True) is ExecutionFailureClassification.APPROVAL_DECLINED
    assert service.classify_failure(approved=True, had_policy=True) is ExecutionFailureClassification.POLICY_DENIED
    assert service.classify_failure(approved=False, had_policy=False) is ExecutionFailureClassification.EXECUTION_FAILED
