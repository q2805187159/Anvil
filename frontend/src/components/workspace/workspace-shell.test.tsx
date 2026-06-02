import { cleanup, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { createElement } from "react";
import { TooltipProvider } from "@/src/components/ui/tooltip";
import { I18nProvider } from "@/src/core/i18n";

import type { MessageView, QueuedFollowUpView, RunStreamEvent, ToolActivityView } from "@/src/core/contracts";
import { sortThreadsByRecency, threadActivityAt } from "@/src/core/threads/recency";

import {
  buildRuntimePhaseDiagnostics,
  buildOptimisticUserMessage,
  buildRecentTools,
  buildQueuedFollowupDispatchSignature,
  buildQueuedFollowupRunBody,
  buildQueuedFollowupRestoreRequest,
  buildSubagentDependencyGraph,
  buildTimelineItems,
  canDispatchQueuedFollowup,
  deduplicateTranscriptMessages,
  deriveContextWindowUsageForDisplay,
  modelNameForRequest,
  mergeIncomingDetailWindow,
  selectNextQueuedFollowup,
  selectPreferredModelName,
  shouldShowComposerRunning,
  shouldUseNewSessionStart,
  shouldRequestFullThreadState,
} from "./workspace-shell";

afterEach(() => {
  cleanup();
});

function expectVisibleText(text: string | RegExp) {
  const matches =
    typeof text === "string"
      ? screen.getAllByText(text)
      : screen.getAllByText((content) => text.test(content));
  expect(matches.some((item) => item.getAttribute("role") !== "tooltip")).toBe(true);
}

describe("model selection helpers", () => {
  it("selects the first configured model instead of using an auto sentinel", () => {
    expect(
      selectPreferredModelName(
        null,
        [
          { name: "minimax_cn" },
          { name: "openai" },
        ],
      ),
    ).toBe("minimax_cn");
    expect(selectPreferredModelName("openai", [{ name: "minimax_cn" }, { name: "openai" }])).toBe("openai");
    expect(selectPreferredModelName("missing", [{ name: "minimax_cn" }])).toBe("minimax_cn");
  });

  it("sends the visible model value directly without translating auto to null", () => {
    expect(modelNameForRequest("minimax_cn")).toBe("minimax_cn");
    expect(modelNameForRequest("")).toBeNull();
  });
});

describe("composer run state", () => {
  it("shows the running control immediately for draft and active thread submissions", () => {
    expect(
      shouldShowComposerRunning({
        activeThreadId: null,
        runStreamIsStreaming: false,
        isSubmittingRun: true,
        optimisticRunningThreadId: "__draft__",
      }),
    ).toBe(true);
    expect(
      shouldShowComposerRunning({
        activeThreadId: "thread-a",
        runStreamIsStreaming: false,
        isSubmittingRun: true,
        optimisticRunningThreadId: "thread-a",
      }),
    ).toBe(true);
    expect(
      shouldShowComposerRunning({
        activeThreadId: "thread-a",
        runStreamIsStreaming: false,
        isSubmittingRun: true,
        optimisticRunningThreadId: "thread-b",
      }),
    ).toBe(false);
    expect(
      shouldShowComposerRunning({
        activeThreadId: "thread-a",
        runStreamIsStreaming: false,
        isSubmittingRun: false,
        optimisticRunningThreadId: "thread-a",
      }),
    ).toBe(false);
    expect(
      shouldShowComposerRunning({
        activeThreadId: "thread-a",
        runStreamIsStreaming: true,
        isSubmittingRun: false,
        optimisticRunningThreadId: null,
      }),
    ).toBe(true);
    expect(
      shouldShowComposerRunning({
        activeThreadId: "thread-a",
        runStreamIsStreaming: true,
        isSubmittingRun: false,
        optimisticRunningThreadId: "thread-a",
        durableThreadStatus: "completed",
        streamMessageCompletedSeen: true,
      }),
    ).toBe(false);
  });

  it("keeps the composer in running mode when durable thread state is still running without a live overlay", () => {
    expect(
      shouldShowComposerRunning({
        activeThreadId: "thread-a",
        runStreamIsStreaming: false,
        isSubmittingRun: false,
        optimisticRunningThreadId: null,
        durableThreadStatus: "running",
      }),
    ).toBe(true);
    expect(
      shouldShowComposerRunning({
        activeThreadId: "thread-a",
        runStreamIsStreaming: false,
        isSubmittingRun: false,
        optimisticRunningThreadId: null,
        durableThreadStatus: "new",
      }),
    ).toBe(false);
    expect(
      shouldShowComposerRunning({
        activeThreadId: "thread-a",
        runStreamIsStreaming: false,
        isSubmittingRun: false,
        optimisticRunningThreadId: null,
        durableThreadStatus: "running",
        streamTerminalSeen: true,
      }),
    ).toBe(false);
  });

  it("uses the raised new-session start layout only for an idle empty draft", () => {
    expect(
      shouldUseNewSessionStart({
        activeThreadId: null,
        visibleMessageCount: 0,
        hasOptimisticUserMessage: false,
        hasPendingApproval: false,
        hasPendingUserInteraction: false,
        queuedFollowupCount: 0,
        isStreaming: false,
        isSubmitting: false,
      }),
    ).toBe(true);

    expect(
      shouldUseNewSessionStart({
        activeThreadId: "thread-a",
        visibleMessageCount: 0,
        hasOptimisticUserMessage: false,
        hasPendingApproval: false,
        hasPendingUserInteraction: false,
        queuedFollowupCount: 0,
        isStreaming: false,
        isSubmitting: false,
      }),
    ).toBe(false);
    expect(
      shouldUseNewSessionStart({
        activeThreadId: null,
        visibleMessageCount: 0,
        hasOptimisticUserMessage: true,
        hasPendingApproval: false,
        hasPendingUserInteraction: false,
        queuedFollowupCount: 0,
        isStreaming: false,
        isSubmitting: false,
      }),
    ).toBe(false);
    expect(
      shouldUseNewSessionStart({
        activeThreadId: null,
        visibleMessageCount: 0,
        hasOptimisticUserMessage: false,
        hasPendingApproval: false,
        hasPendingUserInteraction: false,
        queuedFollowupCount: 0,
        isStreaming: true,
        isSubmitting: false,
      }),
    ).toBe(false);
  });
});

describe("thread rail recency", () => {
  it("keeps active thread rows ordered by latest message activity", () => {
    const ordered = sortThreadsByRecency([
      {
        thread_id: "thread-settings-edited",
        title: "Settings edited",
        status: "ready",
        updated_at: "2026-05-23T12:00:00.000Z",
        last_message_at: "2026-05-23T09:00:00.000Z",
        last_user_message_preview: null,
        has_pending_approval: false,
        has_active_subagent_tasks: false,
      },
      {
        thread_id: "thread-latest",
        title: "Latest",
        status: "completed",
        updated_at: "2026-05-23T11:00:00.000Z",
        last_message_at: "2026-05-23T11:00:00.000Z",
        last_user_message_preview: null,
        has_pending_approval: false,
        has_active_subagent_tasks: false,
      },
    ]);

    expect(ordered.map((thread) => thread.thread_id)).toEqual(["thread-latest", "thread-settings-edited"]);
    expect(threadActivityAt(ordered[1])).toBe("2026-05-23T09:00:00.000Z");
  });
});

describe("queued follow-up helpers", () => {
  it("dispatches guidance before regular queued follow-ups", () => {
    const first: QueuedFollowUpView = {
      queue_id: "followup-1",
      thread_id: "thread-a",
      message: "normal follow-up",
      mode: "followup",
      status: "queued",
      created_at: "2026-05-25T00:00:00.000Z",
      updated_at: "2026-05-25T00:00:00.000Z",
      uploaded_filenames: ["guide.png"],
      uploaded_file_refs: [],
      promoted_capabilities: [],
    };
    const guidance: QueuedFollowUpView = {
      ...first,
      queue_id: "followup-2",
      message: "guide the next safe turn",
      mode: "guidance",
    };

    expect(selectNextQueuedFollowup([first, guidance])).toMatchObject({
      queue_id: "followup-2",
      uploaded_filenames: ["guide.png"],
    });
  });

  it("rebuilds a run body from queued text, settings, and persisted attachment filenames", () => {
    const body = buildQueuedFollowupRunBody(
      {
        queue_id: "followup-1",
        thread_id: "thread-a",
        message: "continue with the uploaded files",
        mode: "followup",
        status: "queued",
        created_at: "2026-05-25T00:00:00.000Z",
        updated_at: "2026-05-25T00:00:00.000Z",
        execution_mode: "agent",
        selected_model: "minimax_cn",
        selected_reasoning_effort: null,
        profile: "default",
        uploaded_filenames: ["spec.pdf"],
        uploaded_file_refs: [],
        promoted_capabilities: ["ppt"],
        is_plan_mode: true,
        dispatch_id: "dispatch-1",
      },
      {
        executionMode: "chat",
        selectedModelName: "openai",
        selectedProfile: "",
        selectedReasoningEffort: "medium",
        selectedPlanMode: false,
      },
    );

    expect(body).toEqual({
      message: "continue with the uploaded files",
      client_message_id: "queued:followup-1",
      execution_mode: "agent",
      selected_model: "minimax_cn",
      profile: "default",
      selected_reasoning_effort: "medium",
      uploaded_filenames: ["spec.pdf"],
      promoted_capabilities: ["ppt"],
      is_plan_mode: true,
      followup_dispatch_id: "dispatch-1",
    });
  });

  it("uses the queue id as a stable client message id for queued runs", () => {
    const first = buildQueuedFollowupRunBody(
      {
        queue_id: "followup-a",
        thread_id: "thread-a",
        message: "same text",
        mode: "followup",
        status: "queued",
        created_at: "2026-05-25T00:00:00.000Z",
        updated_at: "2026-05-25T00:00:00.000Z",
        uploaded_filenames: [],
        uploaded_file_refs: [],
        promoted_capabilities: [],
      },
      {
        executionMode: "agent",
        selectedModelName: "openai",
        selectedProfile: "",
        selectedReasoningEffort: "medium",
        selectedPlanMode: false,
      },
    );
    const second = buildQueuedFollowupRunBody(
      {
        queue_id: "followup-b",
        thread_id: "thread-a",
        message: "same text",
        mode: "followup",
        status: "queued",
        created_at: "2026-05-25T00:00:01.000Z",
        updated_at: "2026-05-25T00:00:01.000Z",
        uploaded_filenames: [],
        uploaded_file_refs: [],
        promoted_capabilities: [],
      },
      {
        executionMode: "agent",
        selectedModelName: "openai",
        selectedProfile: "",
        selectedReasoningEffort: "medium",
        selectedPlanMode: false,
      },
    );

    expect(first.client_message_id).toBe("queued:followup-a");
    expect(second.client_message_id).toBe("queued:followup-b");
  });

  it("rebuilds a front-of-queue restore request from a popped queued follow-up", () => {
    const request = buildQueuedFollowupRestoreRequest({
      queue_id: "followup-1",
      thread_id: "thread-a",
      message: "retry this queued turn",
      mode: "guidance",
      status: "queued",
      created_at: "2026-05-25T00:00:00.000Z",
      updated_at: "2026-05-25T00:00:00.000Z",
      execution_mode: "agent",
      selected_model: "minimax_cn",
      selected_reasoning_effort: "high",
      profile: "default",
      upload_context: "uploaded context",
      uploaded_filenames: ["spec.pdf"],
      uploaded_file_refs: [
        {
          kind: "upload",
          label: "spec.pdf",
          artifact_url: "/threads/thread-a/artifacts/uploads/spec.pdf",
          virtual_path: "/mnt/user-data/workspace/uploads/spec.pdf",
          companions: [],
          outline: [],
          outline_preview: [],
        },
      ],
      promoted_capabilities: ["ppt"],
      is_plan_mode: true,
    });

    expect(request).toEqual({
      message: "retry this queued turn",
      mode: "guidance",
      execution_mode: "agent",
      selected_model: "minimax_cn",
      selected_reasoning_effort: "high",
      profile: "default",
      upload_context: "uploaded context",
      uploaded_filenames: ["spec.pdf"],
      uploaded_file_refs: [
        {
          kind: "upload",
          label: "spec.pdf",
          artifact_url: "/threads/thread-a/artifacts/uploads/spec.pdf",
          virtual_path: "/mnt/user-data/workspace/uploads/spec.pdf",
          companions: [],
          outline: [],
          outline_preview: [],
        },
      ],
      promoted_capabilities: ["ppt"],
      is_plan_mode: true,
      insert_position: "front",
    });
  });

  it("selects one queued follow-up at a time while preserving FIFO order", () => {
    const queued: QueuedFollowUpView[] = [
      {
        queue_id: "followup-1",
        thread_id: "thread-a",
        message: "first",
        mode: "followup",
        status: "queued",
        created_at: "2026-05-25T00:00:00.000Z",
        updated_at: "2026-05-25T00:00:00.000Z",
        uploaded_filenames: [],
        uploaded_file_refs: [],
        promoted_capabilities: [],
      },
      {
        queue_id: "followup-2",
        thread_id: "thread-a",
        message: "second",
        mode: "followup",
        status: "queued",
        created_at: "2026-05-25T00:00:01.000Z",
        updated_at: "2026-05-25T00:00:01.000Z",
        uploaded_filenames: [],
        uploaded_file_refs: [],
        promoted_capabilities: [],
      },
    ];

    const selected = selectNextQueuedFollowup(queued);

    expect(selected?.queue_id).toBe("followup-1");
    expect(queued.map((item) => item.queue_id)).toEqual(["followup-1", "followup-2"]);
  });

  it("only auto-dispatches queued follow-ups when durable thread state is safe", () => {
    expect(canDispatchQueuedFollowup({ status: "completed", has_pending_approval: false })).toBe(true);
    expect(canDispatchQueuedFollowup({ status: "ready", has_pending_approval: false })).toBe(true);
    expect(canDispatchQueuedFollowup({ status: "running", has_pending_approval: false })).toBe(false);
    expect(canDispatchQueuedFollowup({ status: "awaiting_approval", has_pending_approval: false })).toBe(false);
    expect(canDispatchQueuedFollowup({ status: "awaiting_clarification", has_pending_approval: false })).toBe(false);
    expect(canDispatchQueuedFollowup({ status: "completed", has_pending_approval: true })).toBe(false);
    expect(
      canDispatchQueuedFollowup({
        status: "completed",
        has_pending_approval: false,
        active_followup_dispatch: {
          dispatch_id: "dispatch-1",
          queue_id: "followup-1",
          started_at: "2026-05-25T00:00:00.000Z",
          status: "dispatching",
        },
      }),
    ).toBe(false);
    expect(canDispatchQueuedFollowup(null)).toBe(false);
  });

  it("changes the queued follow-up dispatch signature when durable lease state changes", () => {
    expect(
      buildQueuedFollowupDispatchSignature({
        status: "completed",
        has_pending_approval: false,
        active_followup_dispatch: null,
      }),
    ).toBe("completed:false:");
    expect(
      buildQueuedFollowupDispatchSignature({
        status: "completed",
        has_pending_approval: false,
        active_followup_dispatch: {
          dispatch_id: "dispatch-1",
          queue_id: "followup-1",
          started_at: "2026-05-31T00:00:00.000Z",
          status: "dispatching",
        },
      }),
    ).toBe("completed:false:dispatch-1:followup-1");
    expect(buildQueuedFollowupDispatchSignature(null)).toBe("none");
  });
});

describe("buildRecentTools", () => {
  it("does not surface hidden memory tool steps in the recent tool drawer", () => {
    const events: RunStreamEvent[] = [
      {
        event: "step_started",
        data: {
          step: {
            step_id: "assistant-1:call:call-memory",
            message_id: "assistant-1",
            type: "call",
            title: "Memory",
            action: '{"action":"inspect","layer":"user"}',
            status: "running",
            payload: "",
            language: "json",
            tool_name: "memory",
            tool_call_id: "call-memory",
            started_at: "2026-05-13T00:00:00.000Z",
            completed_at: null,
            duration_ms: null,
            visibility: "hidden",
          },
        },
      },
    ];

    expect(buildRecentTools([], events)).toEqual([]);
  });

  it("keeps visible tool steps in the recent tool drawer", () => {
    const events: RunStreamEvent[] = [
      {
        event: "step_started",
        data: {
          step: {
            step_id: "assistant-1:call:call-read",
            message_id: "assistant-1",
            type: "call",
            title: "Read File",
            action: '{"path":"/mnt/user-data/workspace/a.py"}',
            status: "running",
            payload: "",
            language: "json",
            tool_name: "read_file",
            tool_call_id: "call-read",
            started_at: "2026-05-13T00:00:00.000Z",
            completed_at: null,
            duration_ms: null,
            visibility: "chat",
          },
        },
      },
    ];

    expect(buildRecentTools([], events)).toMatchObject([
      {
        tool_call_id: "call-read",
        name: "read_file",
        status: "running",
      },
    ]);
  });

  it("does not replay persisted memory activities into recent tools", () => {
    const existing: ToolActivityView[] = [
      {
        tool_call_id: "call-memory",
        message_id: "assistant-1",
        name: "memory",
        display_name: "Memory",
        source_kind: "builtin",
        source_id: "core",
        capability_group: "memory",
        tool_execution_mode: "sync",
        args: { action: "inspect" },
        status: "completed",
        result_text: '{"entries":[]}',
        started_at: "2026-05-13T00:00:00.000Z",
        completed_at: "2026-05-13T00:00:01.000Z",
        duration_ms: 1000,
      },
    ];

    expect(buildRecentTools(existing, [])).toEqual([]);
  });

  it("does not replay persisted memory activities into the runtime timeline", () => {
    const existing: ToolActivityView[] = [
      {
        tool_call_id: "call-memory",
        message_id: "assistant-1",
        name: "memory",
        display_name: "Memory",
        source_kind: "builtin",
        source_id: "core",
        capability_group: "memory",
        tool_execution_mode: "sync",
        args: { action: "inspect" },
        status: "completed",
        result_text: '{"entries":[]}',
        started_at: "2026-05-13T00:00:00.000Z",
        completed_at: "2026-05-13T00:00:01.000Z",
        duration_ms: 1000,
      },
    ];

    expect(buildTimelineItems(null, existing, [], [], [], [])).toEqual([]);
  });

  it("uses persisted operator timeline after refresh", () => {
    const operatorStatus = {
      status: "running",
      active_tool_count: 1,
      completed_tool_count: 0,
      failed_tool_count: 0,
      pending_approval_count: 0,
      running_process_count: 1,
      active_subagent_count: 0,
      latest_activity: "Read File",
      latest_activity_at: "2026-05-14T02:00:00.000Z",
      timeline: [
        {
          item_id: "call-read",
          kind: "tool",
          status: "running",
          title: "Read File",
          detail: "/mnt/user-data/workspace/a.py",
          timestamp: "2026-05-14T02:00:00.000Z",
          started_at: "2026-05-14T02:00:00.000Z",
          completed_at: null,
          duration_ms: null,
          source_id: "call-read",
          source_kind: "builtin",
          hidden: false,
        },
      ],
    };

    expect(buildTimelineItems(operatorStatus, [], [], [], [], [])).toMatchObject([
      {
        id: "call-read",
        kind: "tool",
        status: "running",
        label: "Read File",
      },
    ]);
  });

  it("summarizes persisted runtime phase timings for diagnostics", () => {
    const diagnostics = buildRuntimePhaseDiagnostics({
      run_id: "run-diagnostics",
      thread_id: "thread-diagnostics",
      status: "completed",
      started_at: "2026-05-24T00:00:00.000Z",
      total_elapsed_ms: 12_340,
      first_model_event_elapsed_ms: 1800,
      first_content_delta_elapsed_ms: 4300,
      completed_elapsed_ms: 12_340,
      marks: [
        {
          phase: "thread_state_loaded",
          label: "Thread state loaded",
          elapsed_ms: 120,
          duration_since_previous_ms: 120,
        },
        {
          phase: "first_content_delta",
          label: "First content delta",
          elapsed_ms: 4300,
          duration_since_previous_ms: 2500,
        },
        {
          phase: "final_state_persisted",
          label: "Final state persisted",
          elapsed_ms: 12_340,
          duration_since_previous_ms: 8040,
        },
      ],
    });

    expect(diagnostics).toMatchObject({
      status: "completed",
      totalElapsedMs: 12_340,
      firstModelEventElapsedMs: 1800,
      firstContentDeltaElapsedMs: 4300,
      completedElapsedMs: 12_340,
      phaseCount: 3,
      slowestPhase: {
        phase: "final_state_persisted",
        label: "Final state persisted",
        durationSincePreviousMs: 8040,
      },
    });
  });

  it("does not show runtime diagnostics when timing payload is absent", () => {
    expect(buildRuntimePhaseDiagnostics(null)).toBeNull();
    expect(
      buildRuntimePhaseDiagnostics({
        run_id: null,
        thread_id: null,
        status: "unknown",
        started_at: null,
        total_elapsed_ms: null,
        first_model_event_elapsed_ms: null,
        first_content_delta_elapsed_ms: null,
        completed_elapsed_ms: null,
        marks: [],
      }),
    ).toBeNull();
  });

  it("shows persisted runtime phase timing marks in the timeline", () => {
    const operatorStatus = {
      status: "completed",
      active_tool_count: 0,
      completed_tool_count: 0,
      failed_tool_count: 0,
      pending_approval_count: 0,
      running_process_count: 0,
      active_subagent_count: 0,
      latest_activity: "Run completed emitted",
      latest_activity_at: "2026-05-23T14:30:00.000Z",
      runtime_phase_timings: {
        run_id: "run-phase-1",
        thread_id: "thread-phase",
        status: "completed",
        started_at: "2026-05-23T14:30:00.000Z",
        total_elapsed_ms: 920,
        first_model_event_elapsed_ms: 120,
        first_content_delta_elapsed_ms: 300,
        completed_elapsed_ms: 920,
        marks: [
          {
            phase: "runtime_assembled",
            label: "Runtime assembled",
            elapsed_ms: 42,
            duration_since_previous_ms: 42,
          },
          {
            phase: "first_content_delta",
            label: "First content delta",
            elapsed_ms: 300,
            duration_since_previous_ms: 180,
          },
        ],
      },
      timeline: [
        {
          item_id: "runtime-phase:run-phase-1:runtime_assembled",
          kind: "runtime",
          status: "completed",
          title: "Runtime assembled",
          detail: "+42ms from run start, +42ms from previous phase",
          timestamp: "2026-05-23T14:30:00.000Z",
          started_at: "2026-05-23T14:30:00.000Z",
          completed_at: null,
          duration_ms: 42,
          source_id: "run-phase-1",
          source_kind: "runtime_assembled",
          hidden: false,
        },
        {
          item_id: "runtime-phase:run-phase-1:first_content_delta",
          kind: "runtime",
          status: "completed",
          title: "First content delta",
          detail: "+300ms from run start, +180ms from previous phase",
          timestamp: "2026-05-23T14:30:00.000Z",
          started_at: "2026-05-23T14:30:00.000Z",
          completed_at: null,
          duration_ms: 180,
          source_id: "run-phase-1",
          source_kind: "first_content_delta",
          hidden: false,
        },
      ],
    };

    expect(buildTimelineItems(operatorStatus, [], [], [], [], [])).toMatchObject([
      {
        id: "runtime-phase:run-phase-1:runtime_assembled",
        kind: "runtime",
        status: "completed",
        label: "Runtime assembled",
        durationMs: 42,
      },
      {
        id: "runtime-phase:run-phase-1:first_content_delta",
        kind: "runtime",
        status: "completed",
        label: "First content delta",
        durationMs: 180,
      },
    ]);
  });
});

describe("shouldRequestFullThreadState", () => {
  it("keeps chat hydration light until a heavy drawer section is visible", () => {
    expect(shouldRequestFullThreadState(false, "files")).toBe(false);
    expect(shouldRequestFullThreadState(true, "timeline")).toBe(false);
    expect(shouldRequestFullThreadState(true, "memory")).toBe(false);
    expect(shouldRequestFullThreadState(true, "files")).toBe(true);
    expect(shouldRequestFullThreadState(true, "recent_tools")).toBe(true);
    expect(shouldRequestFullThreadState(true, "approvals")).toBe(true);
    expect(shouldRequestFullThreadState(true, "subagents")).toBe(true);
    expect(shouldRequestFullThreadState(true, "processes")).toBe(true);
  });
});

describe("deduplicateTranscriptMessages", () => {
  function message(messageId: string, role: MessageView["role"], content: string): MessageView {
    return {
      message_id: messageId,
      role,
      content,
      steps: [],
      content_blocks: [],
      reasoning: null,
      tool_calls: [],
      tool_call_id: null,
      name: null,
      status: null,
      stream_status: null,
      artifact_refs: [],
      approval: null,
    };
  }

  it("collapses equivalent durable user messages before optimistic/live overlay is applied", () => {
    const messages = deduplicateTranscriptMessages([
      message("user-host", "human", "在“E:\\临时下载”目录下生成一个PPT"),
      message("assistant-early", "ai", "我来处理。"),
      message("user-virtual", "human", "在“/mnt/user-data/workspace/_host/e_drive/临时下载”目录下生成一个PPT"),
      message("assistant-final", "ai", "PPT 已生成。"),
    ]);

    expect(messages.map((item) => item.message_id)).toEqual(["user-host", "assistant-early", "assistant-final"]);
  });

  it("collapses equivalent quoted host and docker bridge paths in complex Chinese prompts", () => {
    const messages = deduplicateTranscriptMessages([
      message(
        "user-host",
        "human",
        "在“E:\\临时下载”目录下生成一个关于“E:\\python\\python学习\\harness\\Anvil”项目的介绍PPT，要求页面精美、布局优雅",
      ),
      message("assistant-early", "ai", "我来处理。"),
      message(
        "user-virtual",
        "human",
        "在“/mnt/user-data/workspace/_host/e_drive/临时下载”目录下生成一个关于“/mnt/user-data/workspace/_host/e_drive/python/python学习/harness/Anvil”项目的介绍PPT，要求页面精美、布局优雅",
      ),
      message("assistant-final", "ai", "PPT 已生成。"),
    ]);

    expect(messages.map((item) => item.message_id)).toEqual(["user-host", "assistant-early", "assistant-final"]);
  });

  it("keeps repeated user retries when their displayed text is identical", () => {
    const messages = deduplicateTranscriptMessages([
      message("user-1", "human", "继续"),
      message("assistant-1", "ai", "处理中。"),
      message("user-2", "human", "继续"),
    ]);

    expect(messages.map((item) => item.message_id)).toEqual(["user-1", "assistant-1", "user-2"]);
  });
});

describe("buildOptimisticUserMessage", () => {
  const artifact = (label: string, virtualPath: string): MessageView["artifact_refs"][number] => ({
    kind: "upload",
    label,
    artifact_url: `/threads/thread-a/artifacts/uploads/${label}`,
    virtual_path: virtualPath,
    source_scope: null,
    internal: false,
    extension: label.split(".").pop() ?? null,
    markdown_file: null,
    markdown_virtual_path: null,
    markdown_artifact_url: null,
    companions: [],
    extraction: null,
    outline: [],
    outline_preview: [],
    converter_used: null,
    ocr_used: false,
    conversion_error: null,
  });

  it("uses attachments in the optimistic message id so identical text can coexist with different files", () => {
    const first = buildOptimisticUserMessage("继续", [artifact("one.png", "/mnt/user-data/uploads/one.png")]);
    const second = buildOptimisticUserMessage("继续", [artifact("two.png", "/mnt/user-data/uploads/two.png")]);
    const plain = buildOptimisticUserMessage("继续");

    expect(first.message_id).toMatch(/^optimistic-user:/);
    expect(second.message_id).toMatch(/^optimistic-user:/);
    expect(first.message_id).not.toBe(second.message_id);
    expect(first.message_id).not.toBe(plain.message_id);
  });

  it("uses the explicit client message id when one is available", () => {
    const message = buildOptimisticUserMessage("继续", [], "client:message-1");

    expect(message.message_id).toBe("client:message-1");
    expect(message.client_message_id).toBe("client:message-1");
  });
});

describe("deriveContextWindowUsageForDisplay", () => {
  it("keeps provider context pressure separate from provider token usage", () => {
    const usage = deriveContextWindowUsageForDisplay(
      {
        context_tokens: 650,
        estimated_context_tokens: 640,
        context_source: "provider+estimated",
        context_breakdown: {
          messages: 320,
          system: 160,
          tool_schemas: 120,
        },
        context_breakdown_percentages: {
          messages: 0.5,
          system: 0.25,
          tool_schemas: 0.1875,
        },
        dominant_context_category: "messages",
        total_tokens: 54,
        input_tokens: null,
        output_tokens: null,
        context_window_tokens: 1000,
        auto_compact_threshold_tokens: 800,
        compact_status: "below_threshold",
      },
      null,
      {
        total: {
          input_tokens: 30,
          output_tokens: 24,
          total_tokens: 54,
          cache_read_tokens: 12,
          cache_write_tokens: 8,
        },
      },
    );

    expect(usage?.context_tokens).toBe(650);
    expect(usage?.total_tokens).toBe(54);
    expect(usage?.input_tokens).toBe(30);
    expect(usage?.output_tokens).toBe(24);
    expect(usage?.cache_read_tokens).toBe(12);
    expect(usage?.cache_write_tokens).toBe(8);
    expect(usage?.cache_hit_ratio).toBe(0.6);
    expect(usage?.cache_savings_tokens).toBe(12);
    expect(usage?.context_breakdown.messages).toBe(320);
    expect(usage?.context_breakdown_percentages.messages).toBe(0.5);
    expect(usage?.dominant_context_category).toBe("messages");
    expect(usage?.compact_ratio).toBeCloseTo(0.8125);
  });

  it("does not use cumulative provider totals as current context pressure", () => {
    const usage = deriveContextWindowUsageForDisplay(
      {
        context_tokens: 650,
        context_source: "estimated",
        total_tokens: null,
        input_tokens: null,
        output_tokens: null,
        context_breakdown: {},
        context_breakdown_percentages: {},
        context_window_tokens: 10_000,
        auto_compact_threshold_tokens: 8_000,
        compact_status: "below_threshold",
      },
      null,
      {
        total: {
          input_tokens: 80_000,
          output_tokens: 2_000,
          total_tokens: 82_000,
        },
        last: {
          input_tokens: 700,
          output_tokens: 20,
          total_tokens: 720,
        },
      },
    );

    expect(usage?.context_tokens).toBe(650);
    expect(usage?.total_tokens).toBe(82_000);
    expect(usage?.usage_ratio).toBeCloseTo(0.065);
    expect(usage?.compact_ratio).toBeCloseTo(0.08125);
  });

  it("does not infer durable context pressure from frontend messages", () => {
    const usage = deriveContextWindowUsageForDisplay(
      {
        context_tokens: null,
        context_source: null,
        total_tokens: null,
        input_tokens: null,
        output_tokens: null,
        context_breakdown: {},
        context_breakdown_percentages: {},
        context_window_tokens: 1000,
        auto_compact_threshold_tokens: 800,
        compact_status: "unknown",
      },
      null,
      null,
    );

    expect(usage?.context_tokens).toBeNull();
    expect(usage?.context_source).toBeNull();
    expect(usage?.usage_ratio).toBeNull();
    expect(usage?.compact_ratio).toBeNull();
    expect(usage?.compact_status).toBe("unknown");
  });

  it("prefers typed token usage summary over raw token usage fallback", () => {
    const usage = deriveContextWindowUsageForDisplay(
      {
        context_tokens: 650,
        context_source: "estimated",
        total_tokens: null,
        input_tokens: null,
        output_tokens: null,
        context_breakdown: {},
        context_breakdown_percentages: {},
        context_window_tokens: 1000,
        auto_compact_threshold_tokens: 800,
        compact_status: "below_threshold",
      },
      {
        model: "minimax",
        concrete_model: "MiniMax-M2.7",
        provider: "minimax_cn",
        request_count: 2,
        input_tokens: 240,
        output_tokens: 30,
        total_tokens: 270,
        cache_read_tokens: 25,
        cache_write_tokens: 10,
        reasoning_tokens: 8,
        total: {
          input_tokens: 240,
          output_tokens: 30,
          total_tokens: 270,
          cache_read_tokens: 25,
          cache_write_tokens: 10,
          reasoning_tokens: 8,
        },
        last: {
          input_tokens: 140,
          output_tokens: 20,
          total_tokens: 160,
          cache_read_tokens: 5,
          cache_write_tokens: 0,
          reasoning_tokens: 3,
        },
        estimated_cost_usd: 0.0012,
        cost_status: "estimated",
        currency: "USD",
        pricing_source: "config",
        provider_models: ["MiniMax-M2.7"],
      },
      {
        total: {
          input_tokens: 999,
          output_tokens: 999,
          total_tokens: 1998,
          cache_read_tokens: 99,
          cache_write_tokens: 99,
        },
        request_count: 99,
      },
    );

    expect(usage?.context_tokens).toBe(650);
    expect(usage?.input_tokens).toBe(240);
    expect(usage?.output_tokens).toBe(30);
    expect(usage?.total_tokens).toBe(270);
    expect(usage?.cache_read_tokens).toBe(25);
    expect(usage?.cache_write_tokens).toBe(10);
    expect(usage?.cache_hit_ratio).toBeCloseTo(25 / 35);
    expect(usage?.request_count).toBe(2);
    expect(usage?.last_input_tokens).toBe(140);
    expect(usage?.last_output_tokens).toBe(20);
    expect(usage?.last_total_tokens).toBe(160);
  });
});

describe("ContextWindowUsagePanel", () => {
  function withProviders(component: React.ReactElement) {
    return createElement(I18nProvider, null, createElement(TooltipProvider, null, component));
  }

  it("renders the composer context window control as a dense status panel", async () => {
    const { fireEvent, render, screen } = await import("@testing-library/react");
    const { ContextWindowUsageControl } = await import("./workspace-shell");

    const { unmount } = render(
      withProviders(
        createElement(ContextWindowUsageControl, {
          usage: {
            context_tokens: 650,
            context_source: "estimated",
            context_breakdown: { messages: 320, system: 160, tool_schemas: 120, skills: 40 },
            context_breakdown_percentages: { messages: 0.5, system: 0.25, tool_schemas: 0.1875, skills: 0.0625 },
            dominant_context_category: "messages",
            total_tokens: 54,
            input_tokens: 30,
            output_tokens: 24,
            autocompact_buffer_tokens: 150,
            free_space_tokens: 350,
            context_window_tokens: 1000,
            auto_compact_threshold_tokens: 800,
            compact_status: "below_threshold",
          },
          promptCacheDiagnostics: {
            hits: 1,
            misses: 0,
            writes: 0,
            evictions: 0,
            bypasses: 0,
            size_before: 1,
            size_after: 1,
            net_size_change: 0,
            max_entries: 256,
            cumulative_hits: 4,
            cumulative_misses: 2,
            cumulative_writes: 2,
            cumulative_evictions: 0,
            cumulative_bypasses: 0,
            cumulative_size: 2,
          },
          promptSectionTokenLedger: {
            stable_prompt_tokens: 1200,
            volatile_prompt_tokens: 80,
            stable_section_tokens: { capability_summary: 700, role_and_intent: 120 },
            volatile_section_tokens: { request_context: 80 },
          },
          contextCacheDiagnostics: {
            project_context_cache_status: "hit",
            project_context_fingerprint: "project-context-fingerprint-123456",
            project_context_file_count: 2,
            project_context_truncated_file_count: 1,
            project_context_total_chars: 4096,
            project_context_discovery_scanned_path_count: 77,
            project_context_discovery_max_scanned_paths: 100,
            project_context_discovery_scan_truncated: true,
            project_context_scope_counts: { ".": 1, docs: 1 },
            project_context_applies_to_counts: { "/mnt/user-data/workspace": 1 },
            runtime_path_cache_status: "miss",
            runtime_path_fingerprint: "runtime-path-fingerprint-abcdef",
            runtime_path_root_count: 4,
            runtime_path_host_bridge_count: 2,
          },
          capabilityAssemblyDiagnostics: {
            discovered_tool_count: 20,
            enabled_tool_count: 18,
            materialized_tool_count: 16,
            visible_tool_count: 8,
            deferred_tool_count: 4,
            active_promotion_count: 1,
            visible_schema_token_budget: 1200,
            visible_schema_tokens: 900,
            deferred_schema_tokens: 700,
            total_schema_tokens: 1600,
            visible_schema_budget_remaining_tokens: 300,
            schema_compacted_tool_count: 2,
            schema_deferred_tool_count: 3,
            action_prefilter_deferred_tool_count: 4,
            sanitizer_truncated_tool_count: 1,
            assembly_stage_durations_ms: { runtime_tools: 25, skills_discovery: 10, final_bundle: 42, total: 100 },
            slowest_assembly_stage: "final_bundle",
            slowest_assembly_stage_duration_ms: 42,
            skills_discovery_cache_hit: false,
            skills_discovery_watch_enabled: true,
            skills_discovery_root_count: 2,
            skills_discovery_manifest_count: 40,
            skills_discovery_enabled_count: 35,
            skills_discovery_package_count: 12,
            skills_discovery_stage_durations_ms: { resolve_roots: 4, loader_discover: 30, total: 42 },
            slowest_skills_discovery_stage: "loader_discover",
            slowest_skills_discovery_stage_duration_ms: 30,
            visible_by_source_kind: { builtin: 6, skill: 2 },
            deferred_by_source_kind: { mcp: 4 },
            visible_by_group: { code: 5 },
            deferred_by_group: { browser: 3 },
          },
          memoryInjectionDiagnostics: {
            source: "memory_manager",
            status: "injected",
            snapshot_id: "snapshot-1",
            query_tokens: 9,
            curated_match_count: 2,
            archive_hit_count: 1,
            evidence_count: 3,
            provider_note_count: 1,
            summary_present: true,
            rendered_tokens_before_truncation: 1200,
            rendered_tokens: 900,
            token_budget: 900,
            truncated: true,
            error_type: null,
            store_counts: { project: 2 },
            source_kind_counts: { curated: 2, archive: 1 },
          },
          activeModelName: "openai",
        }),
      ),
    );

    expect(screen.getByRole("button", { name: /Context window 650 \/ 1k \(65%\)/ })).toBeInTheDocument();
    expect(screen.queryByTestId("context-window-usage-panel")).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /Context window 650 \/ 1k \(65%\)/ }));

    expect(screen.getByTestId("context-window-popover").className).toContain("max-h-[min(76dvh,42rem)]");
    expect(screen.getByTestId("context-window-popover").className).toContain("overflow-y-auto");
    expect(screen.getByTestId("context-window-usage-panel")).toBeInTheDocument();
    expectVisibleText("Context window");
    expectVisibleText("650 / 1k (65%)");
    expectVisibleText("System tools");
    fireEvent.click(screen.getByRole("button", { name: "Show details" }));
    expect(screen.getByText("Prompt cache")).toBeInTheDocument();
    expect(screen.getByText(/Run delta:/)).toBeInTheDocument();
    expect(screen.getByText("Prompt sections")).toBeInTheDocument();
    expect(screen.getByText(/capability_summary:700/)).toBeInTheDocument();
    expect(screen.getByText("Context cache")).toBeInTheDocument();
    expectVisibleText(/Project context: hit/);
    expectVisibleText(/Runtime paths: miss/);
    expectVisibleText("Scan budget");
    expectVisibleText("77 / 100");
    expectVisibleText("Scan truncated");
    expectVisibleText("true");
    expect(screen.getByText(/Scopes: \.:1/)).toBeInTheDocument();
    expect(screen.getByText("Memory injection")).toBeInTheDocument();
    expect(screen.getByText(/Curated 2/)).toBeInTheDocument();
    expect(screen.getByText(/Stores: project:2/)).toBeInTheDocument();
    expect(screen.getByText("Capability diagnostics")).toBeInTheDocument();
    expect(screen.getByText(/8 visible tools \/ 4 deferred tools/)).toBeInTheDocument();
    expectVisibleText("Schema budget");
    expect(screen.getAllByText("Skills total").length).toBeGreaterThan(0);
    expect(screen.getAllByText("40").length).toBeGreaterThan(0);
    expect(screen.getByText(/Skills stages: loader_discover:30/)).toBeInTheDocument();
    expect(screen.getByText(/Visible sources: builtin:6/)).toBeInTheDocument();
    unmount();
  });

  it("caps the expanded composer context window to the space above the control", async () => {
    const { fireEvent, render, screen, waitFor } = await import("@testing-library/react");
    const { ContextWindowUsageControl } = await import("./workspace-shell");
    const originalInnerHeight = window.innerHeight;
    const originalRect = HTMLElement.prototype.getBoundingClientRect;
    Object.defineProperty(window, "innerHeight", { configurable: true, value: 900 });
    HTMLElement.prototype.getBoundingClientRect = function getBoundingClientRect() {
      return {
        bottom: 562,
        height: 42,
        left: 0,
        right: 340,
        top: 520,
        width: 340,
        x: 0,
        y: 520,
        toJSON: () => ({}),
      };
    };

    try {
      const { unmount } = render(
        withProviders(
          createElement(ContextWindowUsageControl, {
            usage: {
              context_tokens: 650,
              context_source: "estimated",
              context_breakdown: { messages: 320, system: 160, tool_schemas: 120, skills: 40 },
              context_breakdown_percentages: { messages: 0.5, system: 0.25, tool_schemas: 0.1875, skills: 0.0625 },
              dominant_context_category: "messages",
              total_tokens: 54,
              input_tokens: 30,
              output_tokens: 24,
              autocompact_buffer_tokens: 150,
              free_space_tokens: 350,
              context_window_tokens: 1000,
              auto_compact_threshold_tokens: 800,
              compact_status: "below_threshold",
            },
            activeModelName: "openai",
          }),
        ),
      );

      fireEvent.click(screen.getByRole("button", { name: /Context window 650 \/ 1k \(65%\)/ }));
      const popover = screen.getByTestId("context-window-popover");

      await waitFor(() => expect(popover).toHaveStyle({ maxHeight: "504px" }));
      expect(popover).toHaveStyle({ overflowY: "auto", scrollbarGutter: "stable" });
      fireEvent.click(screen.getByRole("button", { name: "Show details" }));
      expect(popover).toHaveStyle({ maxHeight: "504px" });
      unmount();
    } finally {
      HTMLElement.prototype.getBoundingClientRect = originalRect;
      Object.defineProperty(window, "innerHeight", { configurable: true, value: originalInnerHeight });
    }
  });

  it("renders Claude Code style context window rows and usage details", async () => {
    const { fireEvent, render, screen } = await import("@testing-library/react");
    const { ContextWindowUsagePanel } = await import("./workspace-shell");

    render(
      withProviders(
        createElement(ContextWindowUsagePanel, {
          usage: {
            context_tokens: 650,
            context_source: "estimated",
            context_breakdown: { messages: 320, system: 160, tool_schemas: 120, skills: 40 },
            context_breakdown_percentages: { messages: 0.5, system: 0.25, tool_schemas: 0.1875, skills: 0.0625 },
            dominant_context_category: "messages",
            total_tokens: 54,
            input_tokens: 30,
            output_tokens: 24,
            autocompact_buffer_tokens: 150,
            free_space_tokens: 350,
            cache_read_tokens: 12,
            cache_write_tokens: 8,
            cache_hit_ratio: 0.6,
            cache_savings_tokens: 12,
            compaction_level: 2,
            compaction_level_label: "recursive_summary",
            compaction_reason: "token_threshold_exceeded",
            compaction_input_tokens: 1800,
            compaction_summary_tokens: 120,
            compaction_savings_tokens: 1150,
            compaction_keep_recent_turns: 4,
            context_window_tokens: 1000,
            auto_compact_threshold_tokens: 800,
            compact_status: "compacted",
          },
          promptCacheDiagnostics: {
            hits: 1,
            misses: 0,
            writes: 0,
            evictions: 0,
            bypasses: 0,
            size_before: 1,
            size_after: 1,
            net_size_change: 0,
            max_entries: 256,
            cumulative_hits: 4,
            cumulative_misses: 2,
            cumulative_writes: 2,
            cumulative_evictions: 0,
            cumulative_bypasses: 0,
            cumulative_size: 2,
          },
          promptSectionTokenLedger: {
            stable_prompt_tokens: 1200,
            volatile_prompt_tokens: 88,
            stable_section_tokens: {
              role_and_intent: 120,
              capability_summary: 700,
              response_contract: 220,
            },
            volatile_section_tokens: {
              request_context: 88,
            },
          },
          contextCacheDiagnostics: {
            project_context_cache_status: "hit",
            project_context_fingerprint: "project-context-fingerprint-123456",
            project_context_file_count: 2,
            project_context_truncated_file_count: 1,
            project_context_total_chars: 4096,
            project_context_discovery_scanned_path_count: 77,
            project_context_discovery_max_scanned_paths: 100,
            project_context_discovery_scan_truncated: true,
            project_context_scope_counts: { ".": 1, docs: 1 },
            project_context_applies_to_counts: { "/mnt/user-data/workspace": 1 },
            runtime_path_cache_status: "hit",
            runtime_path_fingerprint: "runtime-path-fingerprint-abcdef",
            runtime_path_root_count: 4,
            runtime_path_host_bridge_count: 2,
          },
          capabilityAssemblyDiagnostics: {
            discovered_tool_count: 20,
            enabled_tool_count: 18,
            materialized_tool_count: 16,
            visible_tool_count: 8,
            deferred_tool_count: 4,
            active_promotion_count: 1,
            visible_schema_token_budget: 1200,
            visible_schema_tokens: 900,
            deferred_schema_tokens: 700,
            total_schema_tokens: 1600,
            visible_schema_budget_remaining_tokens: 300,
            schema_compacted_tool_count: 2,
            schema_deferred_tool_count: 3,
            action_prefilter_deferred_tool_count: 4,
            sanitizer_truncated_tool_count: 1,
            assembly_stage_durations_ms: { runtime_tools: 25, skills_discovery: 10, final_bundle: 42, total: 100 },
            slowest_assembly_stage: "final_bundle",
            slowest_assembly_stage_duration_ms: 42,
            skills_discovery_cache_hit: false,
            skills_discovery_watch_enabled: true,
            skills_discovery_root_count: 2,
            skills_discovery_manifest_count: 40,
            skills_discovery_enabled_count: 35,
            skills_discovery_package_count: 12,
            skills_discovery_stage_durations_ms: { resolve_roots: 4, loader_discover: 30, total: 42 },
            slowest_skills_discovery_stage: "loader_discover",
            slowest_skills_discovery_stage_duration_ms: 30,
            visible_by_source_kind: { builtin: 6, skill: 2 },
            deferred_by_source_kind: { mcp: 4 },
            visible_by_group: { code: 5 },
            deferred_by_group: { browser: 3 },
          },
          memoryInjectionDiagnostics: {
            source: "memory_manager",
            status: "injected",
            snapshot_id: "snapshot-1",
            query_tokens: 9,
            curated_match_count: 2,
            archive_hit_count: 1,
            evidence_count: 3,
            provider_note_count: 1,
            summary_present: true,
            rendered_tokens_before_truncation: 1200,
            rendered_tokens: 900,
            token_budget: 900,
            truncated: true,
            error_type: null,
            store_counts: { project: 2 },
            source_kind_counts: { curated: 2, archive: 1 },
          },
          compactionDiagnostics: {
            compaction_level: 2,
            compaction_level_label: "recursive_summary",
            compaction_reason: "token_threshold_exceeded",
            summary_source: "model",
            summary_model: "minimax/MiniMax-M2.7",
            summary_error_type: "must not render",
            has_existing_summary: true,
            archived_message_count: 9,
            tool_call_count: 3,
            tool_result_count: 2,
            image_block_count: 1,
            truncated_message_count: 2,
            pruned_tool_result_count: 1,
            serialized_chars: 4096,
            serialized_tokens: 720,
            summary_prompt_tokens: 940,
            compaction_input_tokens: 1800,
            compaction_summary_tokens: 120,
            compaction_savings_tokens: 1150,
            keep_recent_turns: 4,
          },
          activeModelName: "openai",
          variant: "drawer",
          defaultExpanded: false,
        }),
      ),
    );

    expectVisibleText("Context window");
    expectVisibleText("650 / 1k (65%)");
    expectVisibleText("System tools");
    expectVisibleText("Free space");
    fireEvent.click(screen.getByRole("button", { name: "Show details" }));

    expect(screen.getByText("Main pressure")).toBeInTheDocument();
    expect(screen.getAllByText("Messages").length).toBeGreaterThanOrEqual(2);
    expectVisibleText("Cache hit");
    expectVisibleText("60%");
    expectVisibleText("Cache saved");
    expectVisibleText("Billed usage");
    expectVisibleText("Compaction level");
    expect(screen.getAllByText("L2 recursive summary").length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText("Prompt cache")).toBeInTheDocument();
    expect(screen.getByText(/Run delta:/)).toBeInTheDocument();
    expect(screen.getByText(/Cumulative:/)).toBeInTheDocument();
    expect(screen.getByText("Prompt sections")).toBeInTheDocument();
    expect(screen.getByText(/Stable prompt:/)).toBeInTheDocument();
    expect(screen.getByText(/Turn injection:/)).toBeInTheDocument();
    expect(screen.getByText(/capability_summary:700/)).toBeInTheDocument();
    expect(screen.getByText(/request_context:88/)).toBeInTheDocument();
    expect(screen.getByText("Context cache")).toBeInTheDocument();
    expectVisibleText("Project context");
    expectVisibleText("Runtime paths");
    expectVisibleText("Truncated files");
    expectVisibleText("Scan budget");
    expectVisibleText("77 / 100");
    expectVisibleText("Scan truncated");
    expect(screen.getByText(/Runtime paths Fingerprint:/)).toBeInTheDocument();
    expect(screen.getByText("Memory injection")).toBeInTheDocument();
    expectVisibleText("Rendered tokens");
    expect(screen.getByText(/Recall: Curated 2/)).toBeInTheDocument();
    expect(screen.getByText(/Sources: curated:2/)).toBeInTheDocument();
    expect(screen.getByText("Compaction diagnostics")).toBeInTheDocument();
    expect(screen.getAllByText("Summary source").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Summary model").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Archived messages").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Image blocks").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Serialized tokens").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Summary prompt").length).toBeGreaterThan(0);
    expect(screen.getAllByText("model").length).toBeGreaterThan(0);
    expect(screen.getAllByText("minimax/MiniMax-M2.7").length).toBeGreaterThan(0);
    expect(screen.getAllByText("720").length).toBeGreaterThan(0);
    expect(screen.getAllByText("940").length).toBeGreaterThan(0);
    expect(screen.queryByText("must not render")).not.toBeInTheDocument();
    expect(screen.getByText("Capability diagnostics")).toBeInTheDocument();
    expectVisibleText("Visible tools");
    expectVisibleText("Deferred tools");
    expectVisibleText("Deferred schema tokens");
    expectVisibleText("Skills total");
    expect(screen.getByText(/Skills stages: loader_discover:30/)).toBeInTheDocument();
    expect(screen.getByText(/Deferred sources: mcp:4/)).toBeInTheDocument();
    expect(screen.getByText(/Deferred groups: browser:3/)).toBeInTheDocument();
    expect(screen.getByText("Before compaction")).toBeInTheDocument();
    expect(screen.getByText("1.8k")).toBeInTheDocument();
    expect(screen.getByText("Keep recent")).toBeInTheDocument();
    expect(screen.getAllByText("12").length).toBeGreaterThanOrEqual(2);
  });
});

describe("UserInteractionCard", () => {
  function withProviders(component: React.ReactElement) {
    return createElement(I18nProvider, null, createElement(TooltipProvider, null, component));
  }

  it("renders single-select decisions and lets custom text replace the selected option", async () => {
    const { fireEvent, render, screen } = await import("@testing-library/react");
    const { UserInteractionCard } = await import("./workspace-shell");
    const onSubmit = vi.fn();

    render(
      withProviders(
        createElement(UserInteractionCard, {
          interaction: {
            request_id: "ui:stack",
            kind: "decision",
            title: "Choose a frontend stack",
            question: "Which stack should Anvil scaffold?",
            description: "The agent will continue after this choice.",
            selection_mode: "single",
            options: [
              { id: "vite", label: "Vite + React", description: "Fast SPA scaffold", recommended: true, disabled: false, metadata: {} },
              { id: "next", label: "Next.js", description: "Full-stack app router", recommended: false, disabled: false, metadata: {} },
            ],
            min_selections: 1,
            max_selections: 1,
            allow_custom: true,
            custom_label: "Other requirement",
            placeholder: "Add constraints",
            required: true,
            source_tool_name: "ask_clarification",
            fields: [],
          },
          onSubmit,
        }),
      ),
    );

    expect(screen.getByText("Choose a frontend stack")).toBeInTheDocument();
    expect(screen.getByText("Vite + React")).toBeInTheDocument();
    fireEvent.click(screen.getByText("Next.js"));
    fireEvent.change(screen.getByPlaceholderText("Add constraints"), {
      target: { value: "Use Tailwind and keep it lightweight" },
    });
    fireEvent.click(screen.getByRole("button", { name: /Confirm and continue/ }));

    expect(onSubmit).toHaveBeenCalledWith({
      selectedOptionIds: [],
      customResponse: "Use Tailwind and keep it lightweight",
      freeText: null,
      fieldResponses: [],
    });
  });

  it("supports multi-select validation before resume", async () => {
    const { fireEvent, render, screen } = await import("@testing-library/react");
    const { UserInteractionCard } = await import("./workspace-shell");
    const onSubmit = vi.fn();

    render(
      withProviders(
        createElement(UserInteractionCard, {
          interaction: {
            request_id: "ui:ppt-style",
            kind: "decision",
            title: "Choose deck directions",
            question: "Pick two deck style constraints.",
            selection_mode: "multiple",
            options: [
              { id: "dense", label: "Dense executive", description: null, recommended: false, disabled: false, metadata: {} },
              { id: "visual", label: "Visual storytelling", description: null, recommended: false, disabled: false, metadata: {} },
              { id: "appendix", label: "Appendix heavy", description: null, recommended: false, disabled: false, metadata: {} },
            ],
            min_selections: 2,
            max_selections: 2,
            allow_custom: false,
            required: true,
            source_tool_name: "ask_clarification",
            fields: [],
          },
          onSubmit,
        }),
      ),
    );

    expect(screen.getByRole("button", { name: /Confirm and continue/ })).toBeDisabled();
    expect(onSubmit).not.toHaveBeenCalled();

    fireEvent.click(screen.getByText("Dense executive"));
    fireEvent.click(screen.getByText("Visual storytelling"));
    fireEvent.click(screen.getByRole("button", { name: /Confirm and continue/ }));

    expect(onSubmit).toHaveBeenCalledWith({
      selectedOptionIds: ["dense", "visual"],
      customResponse: null,
      freeText: null,
      fieldResponses: [],
    });
  });

  it("renders multi-field decisions and submits field responses", async () => {
    const { fireEvent, render, screen } = await import("@testing-library/react");
    const { UserInteractionCard } = await import("./workspace-shell");
    const onSubmit = vi.fn();

    render(
      withProviders(
        createElement(UserInteractionCard, {
          interaction: {
            request_id: "ui:frontend-form",
            kind: "form",
            title: "Frontend build decisions",
            question: "Pick the scaffold contract.",
            selection_mode: "single",
            options: [],
            min_selections: 1,
            max_selections: 1,
            allow_custom: false,
            required: true,
            source_tool_name: "ask_clarification",
            fields: [
              {
                field_id: "stack",
                label: "Framework",
                selection_mode: "single",
                options: [
                  { id: "vite", label: "Vite + React", description: null, recommended: true, disabled: false, metadata: {} },
                  { id: "next", label: "Next.js", description: null, recommended: false, disabled: false, metadata: {} },
                ],
                min_selections: 1,
                max_selections: 1,
                allow_custom: false,
                required: true,
                metadata: {},
              },
              {
                field_id: "scope",
                label: "Completeness",
                selection_mode: "multiple",
                options: [
                  { id: "routing", label: "Routing", description: null, recommended: false, disabled: false, metadata: {} },
                  { id: "tests", label: "Tests", description: null, recommended: false, disabled: false, metadata: {} },
                ],
                min_selections: 1,
                max_selections: 2,
                allow_custom: false,
                required: true,
                metadata: {},
              },
              {
                field_id: "notes",
                label: "Extra constraints",
                selection_mode: "text",
                options: [],
                min_selections: 0,
                max_selections: null,
                allow_custom: false,
                placeholder: "Any constraints",
                required: false,
                metadata: {},
              },
            ],
          },
          onSubmit,
        }),
      ),
    );

    expect(screen.getByText("Frontend build decisions")).toBeInTheDocument();
    expect(screen.getByText("Framework")).toBeInTheDocument();
    expect(screen.getByText("Completeness")).toBeInTheDocument();
    fireEvent.click(screen.getByText("Tests"));
    fireEvent.change(screen.getByPlaceholderText("Any constraints"), {
      target: { value: "Keep controls dense and quiet" },
    });
    fireEvent.click(screen.getByRole("button", { name: /Confirm and continue/ }));

    expect(onSubmit).toHaveBeenCalledWith({
      selectedOptionIds: ["vite"],
      customResponse: null,
      freeText: null,
      fieldResponses: [
        { fieldId: "stack", selectedOptionIds: ["vite"], customResponse: null, freeText: null },
        { fieldId: "scope", selectedOptionIds: ["tests"], customResponse: null, freeText: null },
        { fieldId: "notes", selectedOptionIds: [], customResponse: null, freeText: "Keep controls dense and quiet" },
      ],
    });
  });
});

describe("mergeIncomingDetailWindow", () => {
  function message(messageId: string, role: MessageView["role"], content: string): MessageView {
    return {
      message_id: messageId,
      role,
      content,
      steps: [],
      content_blocks: [],
      reasoning: null,
      tool_calls: [],
      tool_call_id: null,
      name: null,
      status: null,
      stream_status: null,
      artifact_refs: [],
      approval: null,
    };
  }

  it("replaces the active detail window instead of retaining stale live-turn cards", () => {
    const merged = mergeIncomingDetailWindow(
      [
        message("user-1", "human", "生成 PPT"),
        message("assistant-live", "ai", "我先分析项目。"),
        message("user-runtime-duplicate", "human", "生成 PPT"),
        message("assistant-final-live", "ai", "已完成。"),
      ],
      [message("user-1", "human", "生成 PPT"), message("assistant-final", "ai", "已完成。")],
      { total: 4, offset: 0, returned: 4, has_more_before: false, has_more_after: false, truncated: false },
      { total: 2, offset: 0, returned: 2, has_more_before: false, has_more_after: false, truncated: false },
    );

    expect(merged.map((item) => item.message_id)).toEqual(["user-1", "assistant-final"]);
  });

  it("keeps manually loaded earlier messages while replacing the incoming range", () => {
    const merged = mergeIncomingDetailWindow(
      [
        message("older-user", "human", "上一轮"),
        message("older-assistant", "ai", "上一轮回答"),
        message("user-2", "human", "当前轮"),
        message("assistant-live", "ai", "处理中"),
      ],
      [message("user-2", "human", "当前轮"), message("assistant-final", "ai", "完成")],
      { total: 4, offset: 0, returned: 4, has_more_before: false, has_more_after: false, truncated: false },
      { total: 4, offset: 2, returned: 2, has_more_before: true, has_more_after: false, truncated: true },
    );

    expect(merged.map((item) => item.message_id)).toEqual([
      "older-user",
      "older-assistant",
      "user-2",
      "assistant-final",
    ]);
  });
});

describe("buildSubagentDependencyGraph", () => {
  it("classifies ready waiting and blocked dependency chains", () => {
    const graph = buildSubagentDependencyGraph([
      {
        task_id: "subagent-source",
        parent_thread_id: "thread-a",
        status: "completed",
        assigned_profile: "general",
        delegation_depth: 1,
        depends_on_task_ids: [],
        requested_tool_names: [],
        allowed_tool_names: [],
        messages: [],
        recent_tool_activity: [],
        recent_events: [],
        artifacts: [],
      },
      {
        task_id: "subagent-waiting",
        parent_thread_id: "thread-a",
        status: "queued",
        assigned_profile: "general",
        delegation_depth: 1,
        depends_on_task_ids: ["subagent-running"],
        requested_tool_names: [],
        allowed_tool_names: [],
        messages: [],
        recent_tool_activity: [],
        recent_events: [],
        artifacts: [],
      },
      {
        task_id: "subagent-running",
        parent_thread_id: "thread-a",
        status: "running",
        assigned_profile: "general",
        delegation_depth: 1,
        depends_on_task_ids: [],
        requested_tool_names: [],
        allowed_tool_names: [],
        messages: [],
        recent_tool_activity: [],
        recent_events: [],
        artifacts: [],
      },
      {
        task_id: "subagent-blocked",
        parent_thread_id: "thread-a",
        status: "failed",
        assigned_profile: "general",
        delegation_depth: 1,
        depends_on_task_ids: ["subagent-missing"],
        requested_tool_names: [],
        allowed_tool_names: [],
        messages: [],
        recent_tool_activity: [],
        recent_events: [],
        artifacts: [],
      },
    ]);

    expect(graph.readyTaskIds).toEqual(["subagent-source", "subagent-running"]);
    expect(graph.waitingTaskIds).toEqual(["subagent-waiting"]);
    expect(graph.blockedTaskIds).toEqual(["subagent-blocked"]);
    expect(graph.missingDependencyTaskIds).toEqual(["subagent-missing"]);
    expect(graph.layers.map((layer) => layer.map((node) => node.task.task_id))).toEqual([
      ["subagent-running", "subagent-source"],
      ["subagent-waiting"],
      ["subagent-blocked"],
    ]);
    expect(graph.nodes.find((node) => node.task.task_id === "subagent-waiting")).toMatchObject({
      dependencyState: "waiting",
      waitingForTaskIds: ["subagent-running"],
      downstreamTaskIds: [],
    });
    expect(graph.nodes.find((node) => node.task.task_id === "subagent-blocked")).toMatchObject({
      dependencyState: "blocked",
      missingDependencyTaskIds: ["subagent-missing"],
    });
    expect(graph.edges).toMatchObject([
      {
        source_task_id: "subagent-running",
        target_task_id: "subagent-waiting",
        status: "waiting",
        source_status: "running",
      },
      {
        source_task_id: "subagent-missing",
        target_task_id: "subagent-blocked",
        status: "missing",
        source_status: null,
      },
    ]);
  });

  it("surfaces critical blockers by downstream impact", () => {
    const graph = buildSubagentDependencyGraph([
      {
        task_id: "subagent-root",
        parent_thread_id: "thread-a",
        status: "failed",
        assigned_profile: "general",
        delegation_depth: 1,
        depends_on_task_ids: [],
        requested_tool_names: [],
        allowed_tool_names: [],
        messages: [],
        recent_tool_activity: [],
        recent_events: [],
        artifacts: [],
      },
      {
        task_id: "subagent-mid",
        parent_thread_id: "thread-a",
        status: "failed",
        assigned_profile: "general",
        delegation_depth: 1,
        depends_on_task_ids: ["subagent-root"],
        requested_tool_names: [],
        allowed_tool_names: [],
        messages: [],
        recent_tool_activity: [],
        recent_events: [],
        artifacts: [],
      },
      {
        task_id: "subagent-leaf-a",
        parent_thread_id: "thread-a",
        status: "queued",
        assigned_profile: "general",
        delegation_depth: 1,
        depends_on_task_ids: ["subagent-mid"],
        requested_tool_names: [],
        allowed_tool_names: [],
        messages: [],
        recent_tool_activity: [],
        recent_events: [],
        artifacts: [],
      },
      {
        task_id: "subagent-leaf-b",
        parent_thread_id: "thread-a",
        status: "queued",
        assigned_profile: "general",
        delegation_depth: 1,
        depends_on_task_ids: ["subagent-mid"],
        requested_tool_names: [],
        allowed_tool_names: [],
        messages: [],
        recent_tool_activity: [],
        recent_events: [],
        artifacts: [],
      },
    ]);

    expect(graph.criticalBlockers[0]).toMatchObject({
      taskId: "subagent-root",
      status: "failed",
      affectedTaskIds: ["subagent-leaf-a", "subagent-leaf-b", "subagent-mid"],
    });
    expect(graph.criticalBlockers[1]).toMatchObject({
      taskId: "subagent-mid",
      status: "failed",
      affectedTaskIds: ["subagent-leaf-a", "subagent-leaf-b"],
    });
  });
});
